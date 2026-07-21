#!/usr/bin/env python3
"""Generate publication-quality figures from the benchmark summary.json.

Usage:
    python3 scripts/plot_summary.py \\
        logs/benchmarks/scripts/summary.json \\
        -o figures/summary/

Figures produced:
    1. confirmation_rate_vs_loss.{pdf,png}
       Confirmation rate (%) vs. packet-loss probability.

    2. acceptance_rate_vs_loss.{pdf,png}
       Payment acceptance rate (%) vs. packet-loss probability.

    3. heatmap_confirmation_rate.{pdf,png}
       Heatmap of confirmation-rate: routing × loss_probability, for each rate.

    4. network_throughput_vs_loss.{pdf,png}
       Network-layer throughput (TX/RX) vs. packet-loss probability.

    5. quorum_latency_vs_loss.{pdf,png}
       Average time-to-quorum vs. packet-loss probability.

    6. network_throghput_impact.{pdf,png}
       Time series of TX and RX throughput during the 50% packet-loss attack.

    7. bandwidth_phase_table.{pdf,png,csv,md}
       Average MeshPay application TX/RX goodput before, during, and after each attack.

    8. network_phase_table.{pdf,png,csv,md}
       Network interface TX/RX throughput before, during, and after each attack.

    9. goodput_50_loss_table.{pdf,png,csv,md}
       Application goodput at 50% packet-loss attack.

    10. cohort_phase_table.{csv,md}
        Payment confirmation cohorts grouped by payment creation phase.

    11. attack_validation_table.{csv,md}
        Packet-loss target fraction, iptables DROP counters, and cleanup verification when available.

    12. post_attack_funnel_table.{csv,md}
        Post-attack payment-stage funnel showing where created transactions stop.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------

ROUTING_COLORS = {
    "epidemic":      "#1E88E5",   # Blue
    "spray-and-wait": "#FB8C00",  # Orange
    "prophet":       "#E53935",   # Red
}
ROUTING_LABELS = {
    "epidemic":      "Epidemic",
    "spray-and-wait": "Spray-and-Wait",
    "prophet":       "PRoPHET",
}
ROUTING_MARKERS = {
    "epidemic":      "o",
    "spray-and-wait": "s",
    "prophet":       "^",
}

RATES = [10, 20, 50, 100]
LOSSES = [0.0, 0.25, 0.5, 0.8]
ROUTINGS = ["epidemic", "spray-and-wait", "prophet"]

FIGURE_DPI = 150


def _style_ax(ax: plt.Axes) -> None:
    ax.grid(True, alpha=0.3, linestyle="-", linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=10)


def _save(fig: plt.Figure, output_dir: Path, name: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        p = output_dir / f"{name}.{ext}"
        fig.savefig(str(p), dpi=FIGURE_DPI, bbox_inches="tight")
        print(f"  Saved: {p}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

def load_summary(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []

    events: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def normalise_loss(v: Any) -> float:
    if v is None:
        return 0.0
    return round(float(v), 4)

def ms_to_seconds(v: Any) -> float | None:
    if v is None:
        return None
    return float(v) / 1000.0


def _run_param(run: Dict[str, Any], name: str, default: Any = None) -> Any:
    return run.get(f"param.{name}", run.get(name, default))


def _run_dir(run: Dict[str, Any]) -> Path | None:
    for key in ("run_dir", "config.log_dir", "paths.log_dir", "log_dir", "param.log_dir"):
        value = run.get(key)
        if value:
            return Path(value)

    benchmark_path = run.get("benchmark_path") or run.get("paths.benchmark")
    if benchmark_path:
        return Path(benchmark_path).parent

    command = run.get("command")
    if command:
        try:
            parts = shlex.split(str(command))
        except ValueError:
            parts = str(command).split()
        for idx, part in enumerate(parts[:-1]):
            if part == "--log-dir":
                return Path(parts[idx + 1])

    return None


def _load_benchmark_json(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / "benchmark.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _inset_label(ax: plt.Axes, text: str, *, x: float = 0.03, y: float = 0.94) -> None:
    ax.text(
        x,
        y,
        text,
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=10,
        fontweight="bold",
        bbox={"facecolor": "white", "edgecolor": "#BDBDBD", "alpha": 0.85, "pad": 3},
    )


def _format_delta(value: float | None, baseline: float | None) -> str:
    if value is None:
        return "-"

    value_text = f"{value:,.1f}"
    if baseline is None or baseline <= 0:
        return value_text

    delta = ((value - baseline) / baseline) * 100.0
    return f"{value_text} ({delta:+.1f}%)"


def _format_rate(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.1f}"


def _phase_name(t: float, phases: Dict[str, tuple[float, float]]) -> str | None:
    for phase, (start, end) in phases.items():
        if start <= t < end:
            return phase
    return None


def _attack_phases(
    run: Dict[str, Any],
    events: List[Dict[str, Any]],
) -> tuple[Dict[str, tuple[float, float]], Dict[str, Any]] | None:
    attack_started = [
        float(e["time"])
        for e in events
        if e.get("event") == "attack_started" and "time" in e
    ]
    attack_stopped = [
        float(e["time"])
        for e in events
        if e.get("event") == "attack_stopped" and "time" in e
    ]
    if not attack_started or not attack_stopped:
        return None

    attack_start = attack_started[0]
    attack_stop = attack_stopped[-1]
    attack_event = next(
        (event for event in events if event.get("event") == "attack_started"),
        {},
    )
    tpre = float(
        attack_event.get("tpre")
        or run.get("param.attack_tpre")
        or run.get("attack_tpre")
        or 0.0
    )
    tpost = float(
        attack_event.get("tpost")
        or run.get("param.attack_tpost")
        or run.get("attack_tpost")
        or 0.0
    )

    return (
        {
            "before": (attack_start - max(tpre, 0.0), attack_start),
            "during": (attack_start, attack_stop),
            "after": (attack_stop, attack_stop + max(tpost, 0.0)),
        },
        attack_event,
    )


def _attack_targets_text(attack_event: Dict[str, Any], run: Dict[str, Any]) -> str:
    attack_targets = attack_event.get("targets", run.get("attack_targets", ""))
    if isinstance(attack_targets, list):
        return ",".join(str(target) for target in attack_targets)
    return str(attack_targets)


def _phase_event_counts(
    events: List[Dict[str, Any]],
    phases: Dict[str, tuple[float, float]],
    event_name: str,
) -> Dict[str, int]:
    counts = {"before": 0, "during": 0, "after": 0}
    for event in events:
        if event.get("event") != event_name or "time" not in event:
            continue
        phase = _phase_name(float(event["time"]), phases)
        if phase is not None:
            counts[phase] += 1
    return counts


def _phase_payload_bytes(
    events: List[Dict[str, Any]],
    phases: Dict[str, tuple[float, float]],
    event_name: str,
) -> Dict[str, int]:
    payload_bytes = {"before": 0, "during": 0, "after": 0}
    for event in events:
        if event.get("event") != event_name or "time" not in event:
            continue
        phase = _phase_name(float(event["time"]), phases)
        if phase is not None:
            payload_bytes[phase] += int(event.get("payload_size_bytes", 0) or 0)
    return payload_bytes


def _payload_goodput_kib_s(payload_bytes: int, duration_s: float) -> float | None:
    if duration_s <= 0:
        return None
    return payload_bytes / duration_s / 1024.0


def _bandwidth_phase_row(run: Dict[str, Any]) -> Dict[str, Any] | None:
    run_dir_raw = run.get("run_dir")
    if not run_dir_raw:
        return None

    events = load_jsonl(Path(run_dir_raw) / "payment.log")
    if not events:
        return None

    phase_result = _attack_phases(run, events)
    if phase_result is None:
        return None

    timed_events = [
        e
        for e in events
        if "time" in e and e.get("event") in {
            "payment_created",
            "payload_injected",
            "payment_payload_delivered",
            "confirmation_created",
            "network_stats",
        }
    ]
    if not timed_events:
        return None

    phases, attack_event = phase_result

    row: Dict[str, Any] = {
        "routing": run.get("param.routing", run.get("routing", "")),
        "payment_rate": run.get("param.payment_rate", run.get("payment_rate", "")),
        "packet_loss": normalise_loss(run.get("param.attack_loss_probability", 0.0)),
        "run_id": run.get("run_id", Path(run_dir_raw).name),
        "attack_targets": _attack_targets_text(attack_event, run),
    }

    injected_bytes = _phase_payload_bytes(events, phases, "payload_injected")
    delivered_bytes = _phase_payload_bytes(events, phases, "payment_payload_delivered")

    for phase, (start, end) in phases.items():
        duration_s = max(end - start, 0.0)
        row[f"{phase}_duration_s"] = duration_s
        row[f"tx_{phase}_kib_s"] = _payload_goodput_kib_s(injected_bytes[phase], duration_s)
        row[f"rx_{phase}_kib_s"] = _payload_goodput_kib_s(delivered_bytes[phase], duration_s)
        row[f"app_tx_payload_bytes_{phase}"] = injected_bytes[phase]
        row[f"app_rx_payload_bytes_{phase}"] = delivered_bytes[phase]

    for event_name, prefix in [
        ("payment_created", "payments_created"),
        ("payload_injected", "payload_injected"),
        ("payment_payload_delivered", "payload_delivered"),
        ("confirmation_created", "confirmations_created"),
    ]:
        counts = _phase_event_counts(events, phases, event_name)
        for phase, count in counts.items():
            row[f"{prefix}_{phase}"] = count

    return row


def _write_bandwidth_phase_csv(output_dir: Path, rows: List[Dict[str, Any]]) -> None:
    path = output_dir / "bandwidth_phase_table.csv"
    fields = [
        "routing",
        "payment_rate",
        "packet_loss",
        "run_id",
        "attack_targets",
        "before_duration_s",
        "during_duration_s",
        "after_duration_s",
        "tx_before_kib_s",
        "tx_during_kib_s",
        "tx_after_kib_s",
        "rx_before_kib_s",
        "rx_during_kib_s",
        "rx_after_kib_s",
        "app_tx_payload_bytes_before",
        "app_tx_payload_bytes_during",
        "app_tx_payload_bytes_after",
        "app_rx_payload_bytes_before",
        "app_rx_payload_bytes_during",
        "app_rx_payload_bytes_after",
        "payments_created_before",
        "payments_created_during",
        "payments_created_after",
        "payload_injected_before",
        "payload_injected_during",
        "payload_injected_after",
        "payload_delivered_before",
        "payload_delivered_during",
        "payload_delivered_after",
        "confirmations_created_before",
        "confirmations_created_during",
        "confirmations_created_after",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved: {path}")


def _write_bandwidth_phase_markdown(output_dir: Path, rows: List[Dict[str, Any]]) -> None:
    path = output_dir / "bandwidth_phase_table.md"
    lines = [
        "Application goodput is computed from MeshPay payload events: TX uses `payload_injected` bytes and RX uses successfully delivered `payment_payload_delivered` bytes. The after phase is the active `attack_tpost` traffic window.",
        "",
        "| Routing | Loss | TX Before | TX During (% Delta) | TX After (% Delta) | RX Before | RX During (% Delta) | RX After (% Delta) | Durations B/D/A (s) | Payloads Injected B/D/A | Payloads Delivered B/D/A |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for row in rows:
        tx_before = row.get("tx_before_kib_s")
        rx_before = row.get("rx_before_kib_s")
        lines.append(
            "| "
            f"{ROUTING_LABELS.get(str(row['routing']), row['routing'])} | "
            f"{int(float(row['packet_loss']) * 100)}% | "
            f"{_format_delta(tx_before, None)} | "
            f"{_format_delta(row.get('tx_during_kib_s'), tx_before)} | "
            f"{_format_delta(row.get('tx_after_kib_s'), tx_before)} | "
            f"{_format_delta(rx_before, None)} | "
            f"{_format_delta(row.get('rx_during_kib_s'), rx_before)} | "
            f"{_format_delta(row.get('rx_after_kib_s'), rx_before)} | "
            f"{row.get('before_duration_s', 0):.1f}/{row.get('during_duration_s', 0):.1f}/{row.get('after_duration_s', 0):.1f} | "
            f"{row.get('payload_injected_before', 0)}/{row.get('payload_injected_during', 0)}/{row.get('payload_injected_after', 0)} | "
            f"{row.get('payload_delivered_before', 0)}/{row.get('payload_delivered_during', 0)}/{row.get('payload_delivered_after', 0)} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Saved: {path}")


def _last_sample_at_or_before(
    records: List[Dict[str, Any]],
    timestamp: float,
) -> Dict[str, Any] | None:
    candidate = None
    for record in records:
        if float(record.get("time", 0.0)) <= timestamp:
            candidate = record
        else:
            break
    return candidate


def _counter_delta(first: Dict[str, Any], last: Dict[str, Any], field: str) -> int:
    return max(0, int(last.get(field, 0) or 0) - int(first.get(field, 0) or 0))


def _network_phase_row(run: Dict[str, Any]) -> Dict[str, Any] | None:
    run_dir_raw = run.get("run_dir")
    if not run_dir_raw:
        return None

    run_dir = Path(run_dir_raw)
    events = load_jsonl(run_dir / "payment.log")
    raw_records = load_jsonl(run_dir / "network_raw.jsonl")
    if not events or not raw_records:
        return None

    phase_result = _attack_phases(run, events)
    if phase_result is None:
        return None
    phases, attack_event = phase_result

    node_records: Dict[str, List[Dict[str, Any]]] = {}
    for record in raw_records:
        node = record.get("node")
        if node is None or "time" not in record:
            continue
        node_records.setdefault(str(node), []).append(record)
    for records in node_records.values():
        records.sort(key=lambda record: float(record["time"]))
    if not node_records:
        return None

    row: Dict[str, Any] = {
        "routing": run.get("param.routing", run.get("routing", "")),
        "payment_rate": run.get("param.payment_rate", run.get("payment_rate", "")),
        "packet_loss": normalise_loss(run.get("param.attack_loss_probability", 0.0)),
        "run_id": run.get("run_id", run_dir.name),
        "attack_targets": _attack_targets_text(attack_event, run),
        "network_sample_nodes": len(node_records),
    }

    for phase, (start, end) in phases.items():
        duration_s = max(end - start, 0.0)
        totals = {
            "tx_bytes": 0,
            "rx_bytes": 0,
            "tx_packets": 0,
            "rx_packets": 0,
            "tx_dropped": 0,
            "rx_dropped": 0,
            "tx_errors": 0,
            "rx_errors": 0,
        }
        contributing_nodes = 0
        for records in node_records.values():
            first = _last_sample_at_or_before(records, start)
            last = _last_sample_at_or_before(records, end)
            if first is None or last is None:
                continue
            contributing_nodes += 1
            for field in totals:
                totals[field] += _counter_delta(first, last, field)

        row[f"{phase}_duration_s"] = duration_s
        row[f"{phase}_sample_nodes"] = contributing_nodes
        row[f"network_tx_{phase}_kib_s"] = (
            totals["tx_bytes"] / duration_s / 1024.0 if duration_s > 0 else None
        )
        row[f"network_rx_{phase}_kib_s"] = (
            totals["rx_bytes"] / duration_s / 1024.0 if duration_s > 0 else None
        )
        row[f"network_total_{phase}_kib_s"] = (
            (totals["tx_bytes"] + totals["rx_bytes"]) / duration_s / 1024.0
            if duration_s > 0
            else None
        )
        node_divisor = contributing_nodes if contributing_nodes > 0 else None
        row[f"avg_peer_network_tx_{phase}_kib_s"] = (
            totals["tx_bytes"] / duration_s / 1024.0 / node_divisor
            if duration_s > 0 and node_divisor
            else None
        )
        row[f"avg_peer_network_rx_{phase}_kib_s"] = (
            totals["rx_bytes"] / duration_s / 1024.0 / node_divisor
            if duration_s > 0 and node_divisor
            else None
        )
        row[f"avg_peer_network_total_{phase}_kib_s"] = (
            (totals["tx_bytes"] + totals["rx_bytes"]) / duration_s / 1024.0 / node_divisor
            if duration_s > 0 and node_divisor
            else None
        )
        row[f"network_packets_{phase}_per_s"] = (
            (totals["tx_packets"] + totals["rx_packets"]) / duration_s
            if duration_s > 0
            else None
        )
        row[f"network_drops_{phase}"] = totals["tx_dropped"] + totals["rx_dropped"]
        row[f"network_errors_{phase}"] = totals["tx_errors"] + totals["rx_errors"]

    return row


def _write_network_phase_csv(output_dir: Path, rows: List[Dict[str, Any]]) -> None:
    path = output_dir / "network_phase_table.csv"
    fields = [
        "routing",
        "payment_rate",
        "packet_loss",
        "run_id",
        "attack_targets",
        "network_sample_nodes",
        "before_sample_nodes",
        "during_sample_nodes",
        "after_sample_nodes",
        "before_duration_s",
        "during_duration_s",
        "after_duration_s",
        "network_tx_before_kib_s",
        "network_tx_during_kib_s",
        "network_tx_after_kib_s",
        "network_rx_before_kib_s",
        "network_rx_during_kib_s",
        "network_rx_after_kib_s",
        "network_total_before_kib_s",
        "network_total_during_kib_s",
        "network_total_after_kib_s",
        "avg_peer_network_tx_before_kib_s",
        "avg_peer_network_tx_during_kib_s",
        "avg_peer_network_tx_after_kib_s",
        "avg_peer_network_rx_before_kib_s",
        "avg_peer_network_rx_during_kib_s",
        "avg_peer_network_rx_after_kib_s",
        "avg_peer_network_total_before_kib_s",
        "avg_peer_network_total_during_kib_s",
        "avg_peer_network_total_after_kib_s",
        "network_packets_before_per_s",
        "network_packets_during_per_s",
        "network_packets_after_per_s",
        "network_drops_before",
        "network_drops_during",
        "network_drops_after",
        "network_errors_before",
        "network_errors_during",
        "network_errors_after",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved: {path}")


def _write_network_phase_markdown(output_dir: Path, rows: List[Dict[str, Any]]) -> None:
    path = output_dir / "network_phase_table.md"
    lines = [
        "Network throughput is computed from interface counter deltas in `network_raw.jsonl`. TX/RX/total are aggregate KiB/s across sampled nodes; avg-peer fields in the CSV divide those aggregates by sampled nodes. The after phase is the active `attack_tpost` traffic window.",
        "",
        "| Routing | Loss | TX Before | TX During (% Delta) | TX After (% Delta) | RX Before | RX During (% Delta) | RX After (% Delta) | Total Before | Total During (% Delta) | Total After (% Delta) | Sample Nodes B/D/A |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for row in rows:
        tx_before = row.get("network_tx_before_kib_s")
        rx_before = row.get("network_rx_before_kib_s")
        total_before = row.get("network_total_before_kib_s")
        lines.append(
            "| "
            f"{ROUTING_LABELS.get(str(row['routing']), row['routing'])} | "
            f"{int(float(row['packet_loss']) * 100)}% | "
            f"{_format_delta(tx_before, None)} | "
            f"{_format_delta(row.get('network_tx_during_kib_s'), tx_before)} | "
            f"{_format_delta(row.get('network_tx_after_kib_s'), tx_before)} | "
            f"{_format_delta(rx_before, None)} | "
            f"{_format_delta(row.get('network_rx_during_kib_s'), rx_before)} | "
            f"{_format_delta(row.get('network_rx_after_kib_s'), rx_before)} | "
            f"{_format_delta(total_before, None)} | "
            f"{_format_delta(row.get('network_total_during_kib_s'), total_before)} | "
            f"{_format_delta(row.get('network_total_after_kib_s'), total_before)} | "
            f"{row.get('before_sample_nodes', 0)}/{row.get('during_sample_nodes', 0)}/{row.get('after_sample_nodes', 0)} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Saved: {path}")


def _run_benchmark(run: Dict[str, Any]) -> Dict[str, Any]:
    run_dir = _run_dir(run)
    if run_dir is None:
        return {}
    return _load_benchmark_json(run_dir)


def _phase_cohort_rows(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    benchmark = _run_benchmark(run)
    cohorts = (
        benchmark.get("payment_metrics", {})
        .get("phase_cohorts", {})
        .get("cohorts_by_created_phase", {})
    )
    if not cohorts:
        run_dir = _run_dir(run)
        if run_dir is None:
            return []
        events = load_jsonl(run_dir / "payment.log")
        phase_result = _attack_phases(run, events)
        if phase_result is None:
            return []
        phases, _attack_event = phase_result
        created = {e["order_id"]: e for e in events if e.get("event") == "payment_created" and "order_id" in e}
        confirmed = {e["order_id"]: e for e in events if e.get("event") == "confirmation_created" and "order_id" in e}
        accepted = {e["order_id"]: e for e in events if e.get("event") == "payment_accepted" and "order_id" in e}
        cohorts = {}
        for phase, (start, end) in phases.items():
            ids = [order_id for order_id, event in created.items() if start <= float(event.get("time", 0.0)) < end]
            confirmed_ids = [order_id for order_id in ids if order_id in confirmed]
            accepted_ids = [order_id for order_id in ids if order_id in accepted]
            quorum_lats = [
                (float(confirmed[order_id]["time"]) - float(created[order_id]["time"])) * 1000.0
                for order_id in confirmed_ids
            ]
            cohorts[phase] = {
                "payments_created": len(ids),
                "payments_confirmed_by_run_end": len(confirmed_ids),
                "payments_accepted_by_run_end": len(accepted_ids),
                "payments_censored_for_quorum": max(len(ids) - len(confirmed_ids), 0),
                "confirmation_rate_by_run_end_percent": (len(confirmed_ids) / len(ids) * 100.0) if ids else 0.0,
                "acceptance_rate_by_run_end_percent": (len(accepted_ids) / len(ids) * 100.0) if ids else 0.0,
                "time_to_quorum_ms": {"avg": (sum(quorum_lats) / len(quorum_lats)) if quorum_lats else None},
            }

    rows: List[Dict[str, Any]] = []
    for phase in ("before", "during", "after"):
        item = cohorts.get(phase)
        if not item:
            continue
        rows.append(
            {
                "routing": run.get("param.routing", run.get("routing", "")),
                "payment_rate": run.get("param.payment_rate", run.get("payment_rate", "")),
                "packet_loss": normalise_loss(run.get("param.attack_loss_probability", 0.0)),
                "run_id": run.get("run_id", ""),
                "phase": phase,
                "payments_created": item.get("payments_created", 0),
                "payments_confirmed_by_run_end": item.get("payments_confirmed_by_run_end", 0),
                "payments_accepted_by_run_end": item.get("payments_accepted_by_run_end", 0),
                "payments_censored_for_quorum": item.get("payments_censored_for_quorum", 0),
                "confirmation_rate_by_run_end_percent": item.get("confirmation_rate_by_run_end_percent", 0.0),
                "acceptance_rate_by_run_end_percent": item.get("acceptance_rate_by_run_end_percent", 0.0),
                "avg_time_to_quorum_ms": item.get("time_to_quorum_ms", {}).get("avg"),
            }
        )
    return rows


def fig_cohort_phase_table(runs: List[Dict], output_dir: Path) -> None:
    rows = [row for run in runs for row in _phase_cohort_rows(run)]
    rows = sorted(rows, key=lambda r: (float(r.get("payment_rate", 0) or 0), str(r.get("routing", "")), float(r.get("packet_loss", 0)), str(r.get("phase", ""))))
    if not rows:
        print("  Skipped: no phase cohort data found.")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "cohort_phase_table.csv"
    fields = [
        "routing", "payment_rate", "packet_loss", "run_id", "phase",
        "payments_created", "payments_confirmed_by_run_end", "payments_accepted_by_run_end",
        "payments_censored_for_quorum", "confirmation_rate_by_run_end_percent",
        "acceptance_rate_by_run_end_percent", "avg_time_to_quorum_ms",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved: {csv_path}")

    lines = [
        "Payment cohorts are grouped by `payment_created` phase. Confirmation and latency values include only events observed by run end, so censored counts show how much each cohort is under-observed.",
        "",
        "| Routing | Loss | Phase | Created | Confirmed | Censored | Confirm Rate | Avg Quorum ms |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        avg_q = row.get("avg_time_to_quorum_ms")
        avg_q_text = "-" if avg_q is None else f"{float(avg_q):,.1f}"
        lines.append(
            "| "
            f"{ROUTING_LABELS.get(str(row['routing']), row['routing'])} | "
            f"{int(float(row['packet_loss']) * 100)}% | "
            f"{row['phase']} | "
            f"{row['payments_created']} | "
            f"{row['payments_confirmed_by_run_end']} | "
            f"{row['payments_censored_for_quorum']} | "
            f"{float(row['confirmation_rate_by_run_end_percent']):.1f}% | "
            f"{avg_q_text} |"
        )
    md_path = output_dir / "cohort_phase_table.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Saved: {md_path}")


def _attack_validation_row(run: Dict[str, Any]) -> Dict[str, Any] | None:
    benchmark = _run_benchmark(run)
    attack = benchmark.get("attack") if isinstance(benchmark.get("attack"), dict) else {}
    run_dir = _run_dir(run)
    if not attack and run_dir is not None:
        metadata_path = run_dir / "attack_metadata.json"
        if metadata_path.exists():
            try:
                attack = json.loads(metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                attack = {}
    if not attack:
        return None
    counters = attack.get("packet_loss_drop_counters") or {}
    totals = counters.get("totals") if isinstance(counters, dict) else {}
    if totals is None:
        totals = {}
    install = attack.get("packet_loss_installation") or {}
    if install is None:
        install = {}
    cleanup = attack.get("packet_loss_cleanup") or {}
    if cleanup is None:
        cleanup = {}
    return {
        "routing": run.get("param.routing", run.get("routing", "")),
        "payment_rate": run.get("param.payment_rate", run.get("payment_rate", "")),
        "packet_loss": normalise_loss(run.get("param.attack_loss_probability", attack.get("loss_probability", 0.0))),
        "run_id": run.get("run_id", ""),
        "attack_mode": attack.get("attack_mode", "endpoint_iptables_drop" if attack.get("attack") == "packetloss" else attack.get("attack", "")),
        "selected_target_count": attack.get("selected_target_count"),
        "target_fraction": attack.get("target_fraction"),
        "targets": ",".join(str(target) for target in attack.get("targets", [])) if isinstance(attack.get("targets"), list) else attack.get("targets", ""),
        "install_success": install.get("install_success"),
        "attempted_rules": install.get("attempted_rules"),
        "installed_rules": install.get("installed_rules"),
        "drop_packets": totals.get("drop_packets"),
        "drop_bytes": totals.get("drop_bytes"),
        "input_packets": totals.get("input_packets"),
        "output_packets": totals.get("output_packets"),
        "rules_before_cleanup": cleanup.get("rules_before_cleanup"),
        "removed_rules": cleanup.get("removed_rules"),
        "remaining_rules_after_cleanup": cleanup.get("remaining_rules", attack.get("packet_loss_rules_remaining_after_cleanup")),
        "cleanup_success": cleanup.get("cleanup_success"),
    }


def fig_attack_validation_table(runs: List[Dict], output_dir: Path) -> None:
    rows = [row for run in runs for row in [_attack_validation_row(run)] if row is not None]
    rows = sorted(rows, key=lambda r: (float(r.get("payment_rate", 0) or 0), str(r.get("routing", "")), float(r.get("packet_loss", 0))))
    if not rows:
        print("  Skipped: no attack validation data found.")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "attack_validation_table.csv"
    fields = [
        "routing", "payment_rate", "packet_loss", "run_id", "attack_mode", "selected_target_count",
        "target_fraction", "targets", "install_success", "attempted_rules", "installed_rules",
        "drop_packets", "drop_bytes", "input_packets", "output_packets",
        "rules_before_cleanup", "removed_rules", "remaining_rules_after_cleanup", "cleanup_success",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Saved: {csv_path}")

    lines = [
        "Packet-loss validation reports target fraction, iptables DROP counters, and cleanup verification when available. Older runs may lack cleanup columns and should be rerun before claiming rules were removed.",
        "",
        "| Routing | Loss | Mode | Targets | Install | Installed Rules | Drop Packets | Drop Bytes | Remaining Rules After Cleanup | Cleanup |",
        "|---|---:|---|---:|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        drop_packets = row.get("drop_packets")
        drop_bytes = row.get("drop_bytes")
        lines.append(
            "| "
            f"{ROUTING_LABELS.get(str(row['routing']), row['routing'])} | "
            f"{int(float(row['packet_loss']) * 100)}% | "
            f"{row.get('attack_mode') or '-'} | "
            f"{row.get('selected_target_count') or '-'} | "
            f"{row.get('install_success') if row.get('install_success') is not None else '-'} | "
            f"{row.get('installed_rules') if row.get('installed_rules') is not None else '-'} | "
            f"{drop_packets if drop_packets is not None else '-'} | "
            f"{drop_bytes if drop_bytes is not None else '-'} | "
            f"{row.get('remaining_rules_after_cleanup') if row.get('remaining_rules_after_cleanup') is not None else '-'} | "
            f"{row.get('cleanup_success') if row.get('cleanup_success') is not None else '-'} |"
        )
    md_path = output_dir / "attack_validation_table.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Saved: {md_path}")

def _account_host(account: Any) -> str:
    text = str(account or "")
    return text.split("/", 1)[0] if "/" in text else text


def _empty_funnel_counts() -> Dict[str, int]:
    return {
        "payment_created": 0,
        "transfer_order_delivered_to_authority": 0,
        "authority_signed_transfer": 0,
        "signed_transfer_order_delivered_to_sender": 0,
        "confirmation_created": 0,
        "payment_accepted": 0,
    }


def _derive_phase_funnel(events: List[Dict[str, Any]], phases: Dict[str, tuple[float, float]]) -> Dict[str, Dict[str, int]]:
    created_by_order = {
        str(e["order_id"]): e
        for e in events
        if e.get("event") == "payment_created" and "order_id" in e
    }
    confirmed = {
        str(e["order_id"])
        for e in events
        if e.get("event") == "confirmation_created" and "order_id" in e
    }
    accepted = {
        str(e["order_id"])
        for e in events
        if e.get("event") == "payment_accepted" and "order_id" in e
    }
    transfer_delivered: Dict[str, set[str]] = {}
    signed: Dict[str, set[str]] = {}
    signed_returned: Dict[str, set[str]] = {}

    for event in events:
        order_id = event.get("order_id")
        if not order_id:
            continue
        order_id = str(order_id)
        if event.get("event") == "payment_payload_delivered":
            node = str(event.get("node") or "")
            if event.get("payload_type") == "transfer_order":
                transfer_delivered.setdefault(order_id, set()).add(node)
            elif event.get("payload_type") == "signed_transfer_order":
                created = created_by_order.get(order_id, {})
                sender_host = str(created.get("sender_host") or _account_host(created.get("sender")))
                if node == sender_host:
                    signed_returned.setdefault(order_id, set()).add(node)
        elif event.get("event") == "authority_signed_transfer":
            authority = str(event.get("authority") or event.get("node") or "")
            signed.setdefault(order_id, set()).add(authority)

    result: Dict[str, Dict[str, int]] = {}
    for phase, (start, end) in phases.items():
        counts = _empty_funnel_counts()
        for order_id, created in created_by_order.items():
            if not (start <= float(created.get("time", 0.0)) < end):
                continue
            counts["payment_created"] += 1
            if transfer_delivered.get(order_id):
                counts["transfer_order_delivered_to_authority"] += 1
            if signed.get(order_id):
                counts["authority_signed_transfer"] += 1
            if signed_returned.get(order_id):
                counts["signed_transfer_order_delivered_to_sender"] += 1
            if order_id in confirmed:
                counts["confirmation_created"] += 1
            if order_id in accepted:
                counts["payment_accepted"] += 1
        result[phase] = counts
    return result


def _funnel_stop_hint(counts: Dict[str, int]) -> str:
    created = int(counts.get("payment_created", 0) or 0)
    delivered = int(counts.get("transfer_order_delivered_to_authority", 0) or 0)
    signed = int(counts.get("authority_signed_transfer", 0) or 0)
    returned = int(counts.get("signed_transfer_order_delivered_to_sender", 0) or 0)
    confirmed = int(counts.get("confirmation_created", 0) or 0)
    accepted = int(counts.get("payment_accepted", 0) or 0)
    if created == 0:
        return "no post-attack creations"
    if delivered < created:
        return "authority delivery"
    if signed < delivered:
        return "authority signing"
    if returned < signed:
        return "signed return"
    if confirmed < returned:
        return "quorum creation"
    if accepted < confirmed:
        return "recipient acceptance"
    return "complete"


def _post_attack_funnel_row(run: Dict[str, Any]) -> Dict[str, Any] | None:
    run_dir = _run_dir(run)
    if run_dir is None:
        return None

    benchmark = _run_benchmark(run)
    funnel = (
        benchmark.get("payment_metrics", {}).get("post_attack_funnel", {})
        if isinstance(benchmark.get("payment_metrics"), dict)
        else {}
    )
    counts = None
    source = "benchmark.json"
    if isinstance(funnel, dict):
        after = funnel.get("cohorts_by_created_phase", {}).get("after")
        if isinstance(after, dict) and isinstance(after.get("totals"), dict):
            counts = after["totals"]

    if counts is None:
        events = load_jsonl(run_dir / "payment.log")
        if not events:
            return None
        phase_result = _attack_phases(run, events)
        if phase_result is None:
            return None
        phases, _attack_event = phase_result
        counts = _derive_phase_funnel(events, phases).get("after")
        source = "payment.log"

    if not counts:
        return None

    return {
        "routing": run.get("param.routing", run.get("routing", "")),
        "payment_rate": run.get("param.payment_rate", run.get("payment_rate", "")),
        "packet_loss": normalise_loss(run.get("param.attack_loss_probability", run.get("attack_loss_probability", 0.0))),
        "run_id": run.get("run_id", run_dir.name),
        "source": source,
        **{key: counts.get(key, 0) for key in _empty_funnel_counts()},
        "stop_hint": _funnel_stop_hint(counts),
    }


def fig_post_attack_funnel_table(runs: List[Dict], output_dir: Path) -> None:
    rows = [row for run in runs for row in [_post_attack_funnel_row(run)] if row is not None]
    rows = sorted(rows, key=lambda r: (float(r.get("payment_rate", 0) or 0), str(r.get("routing", "")), float(r.get("packet_loss", 0)), str(r.get("run_id", ""))))
    if not rows:
        print("  Skipped: no post-attack funnel data found.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    fields = [
        "routing", "payment_rate", "packet_loss", "run_id", "source",
        "payment_created", "transfer_order_delivered_to_authority",
        "authority_signed_transfer", "signed_transfer_order_delivered_to_sender",
        "confirmation_created", "payment_accepted", "stop_hint",
    ]
    csv_path = output_dir / "post_attack_funnel_table.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in rows)
    print(f"  Saved: {csv_path}")

    lines = [
        "Post-attack funnel for payments created in the active after window (`attack_stopped` to `attack_stopped + attack_tpost`). Counts are unique orders reaching each stage by run end; `Source=payment.log` means the table was derived from old logs that do not yet contain `post_attack_funnel` in `benchmark.json`.",
        "",
        "| Routing | Loss | Created | TO to Authority | Signed | Signed Return | Confirmed | Accepted | Likely Stop | Source |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            f"{ROUTING_LABELS.get(str(row['routing']), row['routing'])} | "
            f"{int(float(row['packet_loss']) * 100)}% | "
            f"{row.get('payment_created', 0)} | "
            f"{row.get('transfer_order_delivered_to_authority', 0)} | "
            f"{row.get('authority_signed_transfer', 0)} | "
            f"{row.get('signed_transfer_order_delivered_to_sender', 0)} | "
            f"{row.get('confirmation_created', 0)} | "
            f"{row.get('payment_accepted', 0)} | "
            f"{row.get('stop_hint', '-')} | "
            f"{row.get('source', '-')} |"
        )
    md_path = output_dir / "post_attack_funnel_table.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Saved: {md_path}")



# ---------------------------------------------------------------------------
# Figure 0a — Confirmation rate by phase (before/during/after) vs. loss
# ---------------------------------------------------------------------------

def fig_cohort_phase_rate_vs_loss(runs: List[Dict], output_dir: Path) -> None:
    """Grouped bar chart: confirmation rate per phase (before/during/after) vs. packet loss.

    This is the primary figure for showing non-recovery: before≈100%, during
    drops with loss, after stays near 0 due to insufficient settle time.
    """
    # Collect (loss, phase, confirm_rate) triples from benchmark.json phase_cohorts
    records: List[Dict[str, Any]] = []
    for run in runs:
        run_dir = _run_dir(run)
        if run_dir is None:
            continue
        benchmark = _load_benchmark_json(run_dir)
        cohorts = (
            benchmark.get("payment_metrics", {})
            .get("phase_cohorts", {})
            .get("cohorts_by_created_phase", {})
        )
        if not cohorts:
            # Fall back to payment.log derivation
            events = load_jsonl(run_dir / "payment.log")
            phase_result = _attack_phases(run, events)
            if phase_result is None:
                continue
            phases, _ = phase_result
            created = {e["order_id"]: e for e in events if e.get("event") == "payment_created" and "order_id" in e}
            confirmed = {e["order_id"]: e for e in events if e.get("event") == "confirmation_created" and "order_id" in e}
            for phase, (start, end) in phases.items():
                ids = [oid for oid, ev in created.items() if start <= float(ev.get("time", 0.0)) < end]
                conf_ids = [oid for oid in ids if oid in confirmed]
                cohorts[phase] = {
                    "payments_created": len(ids),
                    "payments_confirmed_by_run_end": len(conf_ids),
                    "confirmation_rate_by_run_end_percent": (len(conf_ids) / len(ids) * 100.0) if ids else 0.0,
                    "time_to_quorum_ms": {},
                }

        loss = normalise_loss(run.get("param.attack_loss_probability", 0.0))
        routing = run.get("param.routing", run.get("routing", ""))
        for phase in ("before", "during", "after"):
            item = cohorts.get(phase)
            if not item:
                continue
            records.append({
                "loss": loss,
                "routing": routing,
                "phase": phase,
                "confirm_rate": float(item.get("confirmation_rate_by_run_end_percent", 0.0) or 0.0),
                "created": int(item.get("payments_created", 0) or 0),
                "ttq_avg": item.get("time_to_quorum_ms", {}).get("avg"),
            })

    if not records:
        print("  Skipped: no phase cohort data found for phase rate vs loss.")
        return

    losses = sorted({r["loss"] for r in records})
    phases = ["before", "during", "after"]
    phase_colors = {"before": "#1E88E5", "during": "#FB8C00", "after": "#E53935"}
    phase_hatches = {"before": "", "during": "//", "after": "xx"}

    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)

    n_losses = len(losses)
    n_phases = len(phases)
    group_width = 0.72
    bar_w = group_width / n_phases
    x = np.arange(n_losses)

    for pi, phase in enumerate(phases):
        vals = []
        for loss in losses:
            phase_recs = [r for r in records if abs(r["loss"] - loss) < 1e-6 and r["phase"] == phase]
            if phase_recs:
                vals.append(np.mean([r["confirm_rate"] for r in phase_recs]))
            else:
                vals.append(float("nan"))
        offset = (pi - (n_phases - 1) / 2.0) * bar_w
        bars = ax.bar(
            x + offset, vals, bar_w * 0.92,
            color=phase_colors[phase],
            hatch=phase_hatches[phase],
            label=phase.capitalize(),
            alpha=0.88,
            edgecolor="white",
            linewidth=0.6,
        )
        for bar, val in zip(bars, vals):
            if not math.isnan(val):
                ax.text(
                    bar.get_x() + bar.get_width() / 2.0,
                    bar.get_height() + 1.0,
                    f"{val:.0f}%",
                    ha="center", va="bottom", fontsize=8, color="#212121",
                )

    ax.set_xticks(x)
    ax.set_xticklabels([f"{int(l * 100)}%" for l in losses])
    ax.set_xlabel("Packet Loss Probability", fontsize=11, fontweight="bold")
    ax.set_ylabel("Confirmation Rate (%)", fontsize=11, fontweight="bold")
    ax.set_ylim(0, 115)
    ax.legend(title="Creation Phase", fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    _style_ax(ax)

    # Annotation explaining after≈0
    ax.annotate(
        "After-phase near 0:\nsettle window too short\nto drain attack backlog",
        xy=(losses.index(max(losses)), 5),
        xytext=(n_losses - 1 - 0.5, 45),
        fontsize=8,
        color="#B71C1C",
        arrowprops={"arrowstyle": "->", "color": "#B71C1C", "lw": 1.2},
        bbox={"facecolor": "#FFEBEE", "edgecolor": "#B71C1C", "alpha": 0.88, "pad": 3},
    )

    _save(fig, output_dir, "cohort_phase_rate_vs_loss")


# ---------------------------------------------------------------------------
# Figure 0b — Quorum latency by phase (before/during/after) vs. loss
# ---------------------------------------------------------------------------

def fig_quorum_latency_by_phase(runs: List[Dict], output_dir: Path) -> None:
    """Grouped bar chart: avg time-to-quorum (seconds) per phase vs. packet loss.

    Shows the dramatic TTQ explosion in the after-phase, explaining why
    the benchmark cannot confirm post-attack transactions within settle time.
    """
    records: List[Dict[str, Any]] = []
    for run in runs:
        run_dir = _run_dir(run)
        if run_dir is None:
            continue
        benchmark = _load_benchmark_json(run_dir)
        cohorts = (
            benchmark.get("payment_metrics", {})
            .get("phase_cohorts", {})
            .get("cohorts_by_created_phase", {})
        )
        if not cohorts:
            events = load_jsonl(run_dir / "payment.log")
            phase_result = _attack_phases(run, events)
            if phase_result is None:
                continue
            phases, _ = phase_result
            created_map = {e["order_id"]: e for e in events if e.get("event") == "payment_created" and "order_id" in e}
            confirmed_map = {e["order_id"]: e for e in events if e.get("event") == "confirmation_created" and "order_id" in e}
            for phase, (start, end) in phases.items():
                ids = [oid for oid, ev in created_map.items() if start <= float(ev.get("time", 0.0)) < end]
                lats = [
                    (float(confirmed_map[oid]["time"]) - float(created_map[oid]["time"])) * 1000.0
                    for oid in ids if oid in confirmed_map
                ]
                cohorts[phase] = {
                    "payments_created": len(ids),
                    "time_to_quorum_ms": {"avg": (sum(lats) / len(lats)) if lats else None},
                }

        loss = normalise_loss(run.get("param.attack_loss_probability", 0.0))
        routing = run.get("param.routing", run.get("routing", ""))
        for phase in ("before", "during", "after"):
            item = cohorts.get(phase)
            if not item:
                continue
            ttq = (item.get("time_to_quorum_ms") or {}).get("avg")
            if ttq is None:
                continue
            records.append({
                "loss": loss,
                "routing": routing,
                "phase": phase,
                "ttq_s": float(ttq) / 1000.0,
            })

    if not records:
        print("  Skipped: no phase TTQ data found for quorum latency by phase.")
        return

    losses = sorted({r["loss"] for r in records})
    phases = ["before", "during", "after"]
    phase_colors = {"before": "#1E88E5", "during": "#FB8C00", "after": "#E53935"}
    phase_hatches = {"before": "", "during": "//", "after": "xx"}

    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)

    n_losses = len(losses)
    n_phases = len(phases)
    group_width = 0.72
    bar_w = group_width / n_phases
    x = np.arange(n_losses)

    max_ttq = 0.0
    for pi, phase in enumerate(phases):
        vals = []
        for loss in losses:
            phase_recs = [r for r in records if abs(r["loss"] - loss) < 1e-6 and r["phase"] == phase]
            if phase_recs:
                v = np.mean([r["ttq_s"] for r in phase_recs])
                vals.append(v)
                max_ttq = max(max_ttq, v)
            else:
                vals.append(float("nan"))
        offset = (pi - (n_phases - 1) / 2.0) * bar_w
        bars = ax.bar(
            x + offset, vals, bar_w * 0.92,
            color=phase_colors[phase],
            hatch=phase_hatches[phase],
            label=phase.capitalize(),
            alpha=0.88,
            edgecolor="white",
            linewidth=0.6,
        )
        for bar, val in zip(bars, vals):
            if not math.isnan(val):
                ax.text(
                    bar.get_x() + bar.get_width() / 2.0,
                    bar.get_height() + max_ttq * 0.012,
                    f"{val:.1f}s",
                    ha="center", va="bottom", fontsize=7.5, color="#212121",
                )

    ax.set_xticks(x)
    ax.set_xticklabels([f"{int(l * 100)}%" for l in losses])
    ax.set_xlabel("Packet Loss Probability", fontsize=11, fontweight="bold")
    ax.set_ylabel("Avg. Time-to-Quorum (s)", fontsize=11, fontweight="bold")
    ax.set_ylim(0, max(max_ttq * 1.22, 5.0))
    ax.legend(title="Creation Phase", fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    _style_ax(ax)

    # Draw a horizontal dashed line at settle_time (10 s) if visible
    settle_s = 10.0
    if max_ttq > settle_s * 0.5:
        ax.axhline(
            settle_s, color="#9C27B0", linestyle="--", linewidth=1.5,
            label=f"settle-time = {settle_s:.0f} s",
        )
        ax.text(
            n_losses - 0.5, settle_s + max_ttq * 0.02,
            f"settle-time ({settle_s:.0f} s)",
            ha="right", va="bottom", fontsize=8, color="#9C27B0",
        )
        ax.legend(title="Creation Phase", fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)

    _save(fig, output_dir, "quorum_latency_by_phase")


# ---------------------------------------------------------------------------
# Figure 1 — Confirmation rate vs. loss
# ---------------------------------------------------------------------------

def fig_confirmation_rate_vs_loss(runs: List[Dict], output_dir: Path) -> None:
    """Line plot: confirmation_rate (%) vs. packet-loss, faceted by payment_rate."""
    rates = sorted({int(r["param.payment_rate"]) for r in runs})
    n = len(rates)
    fig, axes = plt.subplots(1, n, figsize=(4.5 * n, 5), constrained_layout=True, sharey=True)
    if n == 1:
        axes = [axes]

    for ax, rate in zip(axes, rates):
        subset = [r for r in runs if int(r["param.payment_rate"]) == rate]
        for routing in ROUTINGS:
            pts = sorted(
                [(normalise_loss(r["param.attack_loss_probability"]), r["payment_confirmation_rate_percent"])
                 for r in subset if r["param.routing"] == routing],
                key=lambda x: x[0],
            )
            if not pts:
                continue
            losses, vals = zip(*pts)
            ax.plot(
                losses, vals,
                color=ROUTING_COLORS[routing],
                marker=ROUTING_MARKERS[routing],
                linewidth=2.0, markersize=7,
                label=ROUTING_LABELS[routing],
            )
        ax.set_xlabel("Packet Loss Probability", fontsize=10)
        ax.set_xticks(LOSSES)
        _style_ax(ax)

    axes[0].set_ylabel("Confirmation Rate (%)", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    _save(fig, output_dir, "confirmation_rate_vs_loss")


# ---------------------------------------------------------------------------
# Figure 2 — Acceptance rate vs. loss
# ---------------------------------------------------------------------------

def fig_acceptance_rate_vs_loss(runs: List[Dict], output_dir: Path) -> None:
    rates = sorted({int(r["param.payment_rate"]) for r in runs})
    n = len(rates)
    fig, axes = plt.subplots(1, n, figsize=(4.5 * n, 5), constrained_layout=True, sharey=True)
    if n == 1:
        axes = [axes]

    for ax, rate in zip(axes, rates):
        subset = [r for r in runs if int(r["param.payment_rate"]) == rate]
        for routing in ROUTINGS:
            pts = sorted(
                [(normalise_loss(r["param.attack_loss_probability"]), r["payment_acceptance_rate_percent"])
                 for r in subset if r["param.routing"] == routing],
                key=lambda x: x[0],
            )
            if not pts:
                continue
            losses, vals = zip(*pts)
            ax.plot(
                losses, vals,
                color=ROUTING_COLORS[routing],
                marker=ROUTING_MARKERS[routing],
                linewidth=2.0, markersize=7,
                label=ROUTING_LABELS[routing],
            )
        _inset_label(ax, f"{rate} TPS")
        ax.set_xlabel("Packet Loss Probability", fontsize=10)
        ax.set_xticks(LOSSES)
        _style_ax(ax)

    axes[0].set_ylabel("Acceptance Rate (%)", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    _save(fig, output_dir, "acceptance_rate_vs_loss")


# ---------------------------------------------------------------------------
# Figure 3 — Heatmap of confirmation rate
# ---------------------------------------------------------------------------

def fig_heatmap_confirmation_rate(runs: List[Dict], output_dir: Path) -> None:
    rates = sorted({int(r["param.payment_rate"]) for r in runs})
    n = len(rates)
    fig, axes = plt.subplots(1, n, figsize=(4.5 * n, 4.5), constrained_layout=True)
    if n == 1:
        axes = [axes]

    for ax, rate in zip(axes, rates):
        subset = [r for r in runs if int(r["param.payment_rate"]) == rate]
        data = np.zeros((len(ROUTINGS), len(LOSSES)))
        for i, routing in enumerate(ROUTINGS):
            for j, loss in enumerate(LOSSES):
                match = [r["payment_confirmation_rate_percent"]
                         for r in subset
                         if r["param.routing"] == routing
                         and normalise_loss(r["param.attack_loss_probability"]) == loss]
                if match:
                    data[i, j] = match[0]

        im = ax.imshow(data, cmap="RdYlGn", vmin=0, vmax=100, aspect="auto")
        ax.set_xticks(range(len(LOSSES)))
        ax.set_xticklabels([f"{int(l*100)}%" for l in LOSSES])
        ax.set_yticks(range(len(ROUTINGS)))
        ax.set_yticklabels([ROUTING_LABELS[r] for r in ROUTINGS])
        _inset_label(ax, f"{rate} TPS")
        ax.set_xlabel("Packet Loss", fontsize=10)
        plt.colorbar(im, ax=ax, label="Confirmation Rate (%)")

        for i in range(len(ROUTINGS)):
            for j in range(len(LOSSES)):
                ax.text(j, i, f"{data[i, j]:.1f}%",
                        ha="center", va="center", fontsize=9,
                        color="black" if 20 < data[i, j] < 80 else "white")

    _save(fig, output_dir, "heatmap_confirmation_rate")


# ---------------------------------------------------------------------------
# Figure 4 — Network-layer throughput (TX/RX) vs. packet-loss probability
# ---------------------------------------------------------------------------

def fig_network_throughput_vs_loss(runs: List[Dict], output_dir: Path) -> None:
    rates = sorted({int(r["param.payment_rate"]) for r in runs})
    n = len(rates)
    fig, axes = plt.subplots(1, n, figsize=(4.5 * n, 5), constrained_layout=True, sharey=True)
    if n == 1:
        axes = [axes]

    for ax, rate in zip(axes, rates):
        subset = [r for r in runs if int(r["param.payment_rate"]) == rate]
        for routing in ROUTINGS:
            pts_tx = sorted(
                [(normalise_loss(r["param.attack_loss_probability"]),
                  r.get("network_tx_bytes_per_second", 0.0) / 1000.0)
                 for r in subset if r["param.routing"] == routing and r.get("network_tx_bytes_per_second") is not None],
                key=lambda x: x[0],
            )
            pts_rx = sorted(
                [(normalise_loss(r["param.attack_loss_probability"]),
                  r.get("network_rx_bytes_per_second", 0.0) / 1000.0)
                 for r in subset if r["param.routing"] == routing and r.get("network_rx_bytes_per_second") is not None],
                key=lambda x: x[0],
            )
            if pts_tx:
                losses_tx, vals_tx = zip(*pts_tx)
                ax.plot(
                    losses_tx, vals_tx,
                    color=ROUTING_COLORS[routing],
                    marker=ROUTING_MARKERS[routing],
                    linewidth=2.0, markersize=7,
                    linestyle="-",
                    label=f"{ROUTING_LABELS[routing]} (TX)",
                )
            if pts_rx:
                losses_rx, vals_rx = zip(*pts_rx)
                ax.plot(
                    losses_rx, vals_rx,
                    color=ROUTING_COLORS[routing],
                    marker=ROUTING_MARKERS[routing],
                    linewidth=1.5, markersize=5,
                    linestyle="--",
                    label=f"{ROUTING_LABELS[routing]} (RX)",
                )
        _inset_label(ax, f"{rate} TPS")
        ax.set_xlabel("Packet Loss Probability", fontsize=10)
        ax.set_xticks(LOSSES)
        _style_ax(ax)

    axes[0].set_ylabel("Network Throughput (KB/s)", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=8, edgecolor="#BDBDBD", framealpha=0.9)
    _save(fig, output_dir, "network_throughput_vs_loss")


# ---------------------------------------------------------------------------
# Figure 7 — Network throughput time series during 50% packet loss
# ---------------------------------------------------------------------------

def _attack_window(
    run: Dict[str, Any],
    events: List[Dict[str, Any]],
    benchmark: Dict[str, Any],
) -> tuple[float, float, float, float | None]:
    event_times = [float(e["time"]) for e in events if "time" in e]
    timing = benchmark.get("timing", {}) if isinstance(benchmark.get("timing"), dict) else {}
    config = benchmark.get("config", {}) if isinstance(benchmark.get("config"), dict) else {}
    attack = benchmark.get("attack", {}) if isinstance(benchmark.get("attack"), dict) else {}

    t0 = float(timing.get("started_at") or (min(event_times) if event_times else 0.0))
    duration_s = timing.get("ended_at")
    if duration_s is not None:
        duration_s = max(0.0, float(duration_s) - t0)
    else:
        duration_s = _run_param(run, "duration", config.get("duration"))
        duration_s = float(duration_s) if duration_s is not None else None

    started = [float(e["time"]) for e in events if e.get("event") == "attack_started" and "time" in e]
    stopped = [float(e["time"]) for e in events if e.get("event") == "attack_stopped" and "time" in e]
    if started and stopped:
        return t0, started[0] - t0, stopped[-1] - t0, duration_s

    tpre = float(
        attack.get("tpre")
        or config.get("attack_tpre")
        or _run_param(run, "attack_tpre", 0.0)
        or 0.0
    )
    tatk = float(
        attack.get("tatk")
        or config.get("attack_tatk")
        or _run_param(run, "attack_tatk", 0.0)
        or 0.0
    )
    return t0, tpre, tpre + tatk, duration_s


def _series_from_network_stats(
    events: List[Dict[str, Any]],
    t0: float,
) -> tuple[List[float], List[float], List[float]]:
    node_samples: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        if event.get("event") != "network_stats" or "time" not in event:
            continue
        node = str(event.get("node") or "")
        if not node:
            continue
        node_samples.setdefault(node, []).append(event)

    bins: Dict[int, List[float]] = {}
    for samples in node_samples.values():
        samples = sorted(samples, key=lambda e: float(e.get("time", 0.0)))
        for prev, cur in zip(samples, samples[1:]):
            prev_t = float(prev.get("time", 0.0))
            cur_t = float(cur.get("time", 0.0))
            dt = cur_t - prev_t
            if dt <= 0.0:
                continue
            tx_delta = max(0, int(cur.get("tx_bytes", 0) or 0) - int(prev.get("tx_bytes", 0) or 0))
            rx_delta = max(0, int(cur.get("rx_bytes", 0) or 0) - int(prev.get("rx_bytes", 0) or 0))
            bucket = int(math.floor(cur_t - t0))
            slot = bins.setdefault(bucket, [0.0, 0.0])
            slot[0] += tx_delta / dt
            slot[1] += rx_delta / dt

    times = sorted(bins)
    return [float(t) for t in times], [bins[t][0] for t in times], [bins[t][1] for t in times]


def _series_from_payload_events(
    events: List[Dict[str, Any]],
    t0: float,
) -> tuple[List[float], List[float], List[float]]:
    bins: Dict[int, List[float]] = {}
    for event in events:
        if "time" not in event:
            continue
        name = event.get("event")
        if name not in {"payload_injected", "payment_payload_delivered"}:
            continue
        bucket = int(math.floor(float(event["time"]) - t0))
        if bucket < 0:
            continue
        slot = bins.setdefault(bucket, [0.0, 0.0])
        payload_size = float(event.get("payload_size_bytes", 0.0) or 0.0)
        if name == "payload_injected":
            slot[0] += payload_size
        else:
            slot[1] += payload_size

    times = sorted(bins)
    return [float(t) + 0.5 for t in times], [bins[t][0] for t in times], [bins[t][1] for t in times]


def _throughput_timeseries(run: Dict[str, Any]) -> Dict[str, Any] | None:
    run_dir = _run_dir(run)
    if run_dir is None:
        return None

    events = load_jsonl(run_dir / "payment.log")
    if not events:
        return None

    benchmark = _load_benchmark_json(run_dir)
    t0, attack_start, attack_stop, duration_s = _attack_window(run, events, benchmark)
    times, tx_bps, rx_bps = _series_from_network_stats(events, t0)
    if not times:
        times, tx_bps, rx_bps = _series_from_payload_events(events, t0)
    if not times:
        return None

    routing = str(_run_param(run, "routing", benchmark.get("config", {}).get("routing", "")))
    payment_rate = _run_param(run, "payment_rate", benchmark.get("config", {}).get("payment_rate", ""))
    return {
        "routing": routing,
        "payment_rate": payment_rate,
        "times": times,
        "tx_bps": tx_bps,
        "rx_bps": rx_bps,
        "attack_start": attack_start,
        "attack_stop": attack_stop,
        "duration_s": duration_s,
    }


def fig_network_throghput_impact(runs: List[Dict], output_dir: Path) -> None:
    loss_runs = [
        run for run in runs
        if abs(normalise_loss(_run_param(run, "attack_loss_probability", 0.0)) - 0.5) < 1e-9
    ]
    series = [item for run in loss_runs for item in [_throughput_timeseries(run)] if item is not None]
    series = sorted(series, key=lambda item: (float(item.get("payment_rate") or 0.0), str(item.get("routing") or "")))

    if not series:
        print("  Skipped: no 50% packet-loss runs with throughput time-series data found.")
        return

    n = len(series)
    cols = min(3, n)
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(5.2 * cols, 3.4 * rows), constrained_layout=True, squeeze=False, sharey=True)

    for idx, item in enumerate(series):
        ax = axes[idx // cols][idx % cols]
        attack_start = float(item["attack_start"])
        attack_stop = float(item["attack_stop"])
        ax.axvspan(attack_start, attack_stop, color="#BDBDBD", alpha=0.38, label="Attack Window", zorder=0)
        ax.plot(item["times"], [v / 1000.0 for v in item["tx_bps"]], color="#1E88E5", linewidth=1.8, label="TX")
        ax.plot(item["times"], [v / 1000.0 for v in item["rx_bps"]], color="#E53935", linewidth=1.8, label="RX")

        route = ROUTING_LABELS.get(str(item["routing"]), str(item["routing"]).replace("-", " ").title())
        rate = item.get("payment_rate")
        try:
            rate_text = f"{int(float(rate))} tx"
        except (TypeError, ValueError):
            rate_text = str(rate)
        _inset_label(ax, f"{route} / {rate_text}")

        ymax = max([*[v / 1000.0 for v in item["tx_bps"]], *[v / 1000.0 for v in item["rx_bps"]], 1.0])
        ax.text(
            (attack_start + attack_stop) / 2.0,
            ymax * 0.92,
            f"50% loss attack ({attack_stop - attack_start:.0f}s)",
            ha="center",
            va="top",
            fontsize=9,
            fontweight="bold",
            color="#424242",
            bbox={"facecolor": "white", "edgecolor": "#9E9E9E", "alpha": 0.82, "pad": 2},
        )
        duration_s = item.get("duration_s")
        if duration_s:
            ax.set_xlim(0.0, float(duration_s))
        ax.set_ylim(bottom=0.0)
        ax.set_xlabel("Time (s)", fontsize=10, fontweight="bold")
        if idx % cols == 0:
            ax.set_ylabel("Network Throughput (KB/s)", fontsize=10, fontweight="bold")
        _style_ax(ax)
        ax.legend(fontsize=8, edgecolor="#BDBDBD", framealpha=0.9, loc="upper right")

    for idx in range(n, rows * cols):
        axes[idx // cols][idx % cols].axis("off")

    _save(fig, output_dir, "network_throghput_impact")



# ---------------------------------------------------------------------------
# Figure 5 — Quorum latency vs. loss
# ---------------------------------------------------------------------------

def fig_quorum_latency_vs_loss(runs: List[Dict], output_dir: Path) -> None:
    rates = sorted({int(r["param.payment_rate"]) for r in runs})
    n = len(rates)
    fig, axes = plt.subplots(1, n, figsize=(4.5 * n, 5), constrained_layout=True, sharey=True)
    if n == 1:
        axes = [axes]

    for ax, rate in zip(axes, rates):
        subset = [r for r in runs if int(r["param.payment_rate"]) == rate]
        for routing in ROUTINGS:
            pts = sorted(
                [(normalise_loss(r["param.attack_loss_probability"]),
                  ms_to_seconds(r["avg_time_to_quorum_ms"]))
                 for r in subset if r["param.routing"] == routing],
                key=lambda x: x[0],
            )
            if not pts:
                continue
            losses, vals = zip(*pts)
            ax.plot(
                losses, vals,
                color=ROUTING_COLORS[routing],
                marker=ROUTING_MARKERS[routing],
                linewidth=2.0, markersize=7,
                label=ROUTING_LABELS[routing],
            )
        _inset_label(ax, f"{rate} TPS")
        ax.set_xlabel("Packet Loss Probability", fontsize=10)
        ax.set_xticks(LOSSES)
        _style_ax(ax)

    axes[0].set_ylabel("Avg. Time-to-Quorum (s)", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    _save(fig, output_dir, "quorum_latency_vs_loss")



# ---------------------------------------------------------------------------
# Figure 6 — Avg hop count vs. loss (from summary.json hop_count fields)
# ---------------------------------------------------------------------------

def fig_hop_count_vs_loss(runs: List[Dict], output_dir: Path) -> None:
    """Line plot: avg hop count vs. packet-loss, faceted by payment_rate.

    Hop count is populated only when the IPC delivery socket forwards the
    `hops` list from the DTN router (available from new benchmark runs).
    If all values are None the figure is skipped gracefully.
    """
    # Filter to runs that have hop_count data.
    runs_with_hops = [
        r for r in runs
        if r.get("avg_hop_count") is not None
    ]
    if not runs_with_hops:
        print("  Skipped: no hop_count data in summary (old benchmark runs lack this field).")
        return

    rates = sorted({int(r["param.payment_rate"]) for r in runs_with_hops})
    n = len(rates)
    fig, axes = plt.subplots(1, n, figsize=(4.5 * n, 5), constrained_layout=True, sharey=True)
    if n == 1:
        axes = [axes]

    for ax, rate in zip(axes, rates):
        subset = [r for r in runs_with_hops if int(r["param.payment_rate"]) == rate]
        for routing in ROUTINGS:
            pts = sorted(
                [
                    (normalise_loss(r["param.attack_loss_probability"]), float(r["avg_hop_count"]))
                    for r in subset
                    if r["param.routing"] == routing
                    and r.get("avg_hop_count") is not None
                ],
                key=lambda x: x[0],
            )
            if not pts:
                continue
            losses, vals = zip(*pts)
            ax.plot(
                losses, vals,
                color=ROUTING_COLORS[routing],
                marker=ROUTING_MARKERS[routing],
                linewidth=2.0, markersize=7,
                label=ROUTING_LABELS[routing],
            )
        _inset_label(ax, f"{rate} TPS")
        ax.set_xlabel("Packet Loss Probability", fontsize=10)
        ax.set_xticks(LOSSES)
        _style_ax(ax)

    axes[0].set_ylabel("Avg. Hop Count", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    _save(fig, output_dir, "hop_count_vs_loss")



def fig_bandwidth_phase_table(runs: List[Dict], output_dir: Path) -> None:
    rows = [row for run in runs for row in [_bandwidth_phase_row(run)] if row is not None]
    rows = sorted(rows, key=lambda r: (float(r.get("payment_rate", 0)), str(r.get("routing", "")), float(r.get("packet_loss", 0))))

    if not rows:
        print("  Skipped: no attack runs with network_stats found.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_bandwidth_phase_csv(output_dir, rows)
    _write_bandwidth_phase_markdown(output_dir, rows)

    display_rows = []
    for row in rows:
        tx_before = row.get("tx_before_kib_s")
        rx_before = row.get("rx_before_kib_s")
        display_rows.append(
            [
                ROUTING_LABELS.get(str(row["routing"]), row["routing"]),
                f"{int(float(row['packet_loss']) * 100)}%",
                _format_delta(tx_before, None),
                _format_delta(row.get("tx_during_kib_s"), tx_before),
                _format_delta(row.get("tx_after_kib_s"), tx_before),
                _format_delta(rx_before, None),
                _format_delta(row.get("rx_during_kib_s"), rx_before),
                _format_delta(row.get("rx_after_kib_s"), rx_before),
            ]
        )

    phase_header = [
        "Routing",
        "Loss",
        "Before",
        "During (% Delta)",
        "After (% Delta)",
        "Before",
        "During (% Delta)",
        "After (% Delta)",
    ]
    table_rows = [phase_header, *display_rows]

    fig_height = max(2.4, 0.38 * len(display_rows) + 1.9)
    fig, ax = plt.subplots(figsize=(13.0, fig_height))
    ax.axis("off")
    table = ax.table(
        cellText=table_rows,
        loc="center",
        cellLoc="right",
        colLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.35)

    for (row_idx, col_idx), cell in table.get_celld().items():
        cell.set_edgecolor("#BDBDBD")
        cell.set_linewidth(0.6)
        if row_idx == 0:
            cell.set_facecolor("#E0E0E0")
            cell.set_text_props(weight="bold", color="black", ha="center")
        elif row_idx % 2 == 0:
            cell.set_facecolor("#F5F5F5")
        if row_idx >= 1 and col_idx in (0, 1):
            cell.set_text_props(ha="left" if col_idx == 0 else "center")

    fig.canvas.draw()

    def draw_column_group(label: str, first_col: int, last_col: int) -> None:
        first = table[(0, first_col)]
        last = table[(0, last_col)]
        x = first.get_x()
        y = first.get_y() + first.get_height()
        width = last.get_x() + last.get_width() - x
        height = first.get_height() * 0.88
        rect = Rectangle(
            (x, y),
            width,
            height,
            transform=ax.transAxes,
            facecolor="#E0E0E0",
            edgecolor="#BDBDBD",
            linewidth=0.6,
            clip_on=False,
        )
        ax.add_patch(rect)
        ax.text(
            x + width / 2.0,
            y + height / 2.0,
            label,
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=9,
            fontweight="bold",
            color="black",
            clip_on=False,
        )

    draw_column_group("App TX Goodput (KiB/s)", 2, 4)
    draw_column_group("App RX Goodput (KiB/s)", 5, 7)

    title = "Average MeshPay Application Goodput Before, During, and After Packet-Loss Attack"
    rates = sorted({int(float(row["payment_rate"])) for row in rows if row.get("payment_rate") not in (None, "")})
    if len(rates) == 1:
        title += f"\n{rates[0]} TPS"

    _save(fig, output_dir, "bandwidth_phase_table")


def fig_network_phase_table(runs: List[Dict], output_dir: Path) -> None:
    rows = [row for run in runs for row in [_network_phase_row(run)] if row is not None]
    rows = sorted(rows, key=lambda r: (float(r.get("payment_rate", 0)), str(r.get("routing", "")), float(r.get("packet_loss", 0))))

    if not rows:
        print("  Skipped: no attack runs with network_raw.jsonl samples found.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_network_phase_csv(output_dir, rows)
    _write_network_phase_markdown(output_dir, rows)

    display_rows = []
    for row in rows:
        total_before = row.get("network_total_before_kib_s")
        tx_before = row.get("network_tx_before_kib_s")
        rx_before = row.get("network_rx_before_kib_s")
        display_rows.append(
            [
                ROUTING_LABELS.get(str(row["routing"]), row["routing"]),
                f"{int(float(row['packet_loss']) * 100)}%",
                _format_delta(total_before, None),
                _format_delta(row.get("network_total_during_kib_s"), total_before),
                _format_delta(row.get("network_total_after_kib_s"), total_before),
                _format_delta(row.get("network_tx_after_kib_s"), tx_before),
                _format_delta(row.get("network_rx_after_kib_s"), rx_before),
                f"{row.get('before_sample_nodes', 0)}/{row.get('during_sample_nodes', 0)}/{row.get('after_sample_nodes', 0)}",
            ]
        )

    table_rows = [
        [
            "Routing",
            "Loss",
            "Total Before",
            "Total During",
            "Total After",
            "TX After",
            "RX After",
            "Nodes B/D/A",
        ],
        *display_rows,
    ]

    fig_height = max(2.4, 0.38 * len(display_rows) + 1.9)
    fig, ax = plt.subplots(figsize=(13.0, fig_height))
    ax.axis("off")
    table = ax.table(
        cellText=table_rows,
        loc="center",
        cellLoc="right",
        colLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.35)

    for (row_idx, col_idx), cell in table.get_celld().items():
        cell.set_edgecolor("#BDBDBD")
        cell.set_linewidth(0.6)
        if row_idx == 0:
            cell.set_facecolor("#E0E0E0")
            cell.set_text_props(weight="bold", color="black", ha="center")
        elif row_idx % 2 == 0:
            cell.set_facecolor("#F5F5F5")
        if row_idx >= 1 and col_idx in (0, 1, 7):
            cell.set_text_props(ha="left" if col_idx == 0 else "center")

    title = "Network Interface Throughput Before, During, and After Packet-Loss Attack"
    rates = sorted({int(float(row["payment_rate"])) for row in rows if row.get("payment_rate") not in (None, "")})
    if len(rates) == 1:
        title += f"\n{rates[0]} TPS"

    _save(fig, output_dir, "network_phase_table")


def fig_goodput_50_loss_table(runs: List[Dict], output_dir: Path) -> None:
    rows = [row for run in runs for row in [_bandwidth_phase_row(run)] if row is not None]
    rows = [row for row in rows if abs(float(row.get("packet_loss", 0.0)) - 0.5) < 1e-9]
    rows = sorted(rows, key=lambda r: (float(r.get("payment_rate", 0)), str(r.get("routing", ""))))

    if not rows:
        print("  Skipped: no 50% packet-loss attack rows found.")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / "goodput_50_loss_table.csv"
    csv_fields = [
        "routing",
        "payment_rate",
        "packet_loss",
        "tx_before_kib_s",
        "tx_during_kib_s",
        "tx_after_kib_s",
        "rx_before_kib_s",
        "rx_during_kib_s",
        "rx_after_kib_s",
        "before_duration_s",
        "during_duration_s",
        "after_duration_s",
        "payload_injected_before",
        "payload_injected_during",
        "payload_injected_after",
        "payload_delivered_before",
        "payload_delivered_during",
        "payload_delivered_after",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in csv_fields} for row in rows)
    print(f"  Saved: {csv_path}")

    md_path = output_dir / "goodput_50_loss_table.md"
    md_lines = [
        "Application goodput at 50% packet-loss attack. TX uses `payload_injected` bytes and RX uses delivered `payment_payload_delivered` bytes.",
        "",
        "| Routing | TX Before | TX During (% Delta) | TX After (% Delta) | RX Before | RX During (% Delta) | RX After (% Delta) | Durations B/D/A (s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    display_rows = []
    for row in rows:
        tx_before = row.get("tx_before_kib_s")
        rx_before = row.get("rx_before_kib_s")
        routing_label = ROUTING_LABELS.get(str(row["routing"]), row["routing"])
        tx_during = _format_delta(row.get("tx_during_kib_s"), tx_before)
        tx_after = _format_delta(row.get("tx_after_kib_s"), tx_before)
        rx_during = _format_delta(row.get("rx_during_kib_s"), rx_before)
        rx_after = _format_delta(row.get("rx_after_kib_s"), rx_before)
        md_lines.append(
            "| "
            f"{routing_label} | "
            f"{_format_delta(tx_before, None)} | "
            f"{tx_during} | "
            f"{tx_after} | "
            f"{_format_delta(rx_before, None)} | "
            f"{rx_during} | "
            f"{rx_after} | "
            f"{row.get('before_duration_s', 0):.1f}/{row.get('during_duration_s', 0):.1f}/{row.get('after_duration_s', 0):.1f} |"
        )
        display_rows.append([
            routing_label,
            _format_delta(tx_before, None),
            tx_during,
            tx_after,
            _format_delta(rx_before, None),
            rx_during,
            rx_after,
        ])

    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"  Saved: {md_path}")

    phase_header = [
        "Routing",
        "Before",
        "During (% Delta)",
        "After (% Delta)",
        "Before",
        "During (% Delta)",
        "After (% Delta)",
    ]
    table_rows = [phase_header, *display_rows]

    fig_height = max(2.2, 0.42 * len(display_rows) + 1.9)
    fig, ax = plt.subplots(figsize=(11.5, fig_height))
    ax.axis("off")
    table = ax.table(
        cellText=table_rows,
        loc="center",
        cellLoc="right",
        colLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.35)

    for (row_idx, col_idx), cell in table.get_celld().items():
        cell.set_edgecolor("#BDBDBD")
        cell.set_linewidth(0.6)
        if row_idx == 0:
            cell.set_facecolor("#E0E0E0")
            cell.set_text_props(weight="bold", color="black", ha="center")
        elif row_idx % 2 == 0:
            cell.set_facecolor("#F5F5F5")
        if row_idx >= 1 and col_idx == 0:
            cell.set_text_props(ha="left")

    fig.canvas.draw()

    def draw_column_group(label: str, first_col: int, last_col: int) -> None:
        first = table[(0, first_col)]
        last = table[(0, last_col)]
        x = first.get_x()
        y = first.get_y() + first.get_height()
        width = last.get_x() + last.get_width() - x
        height = first.get_height() * 0.88
        rect = Rectangle(
            (x, y),
            width,
            height,
            transform=ax.transAxes,
            facecolor="#E0E0E0",
            edgecolor="#BDBDBD",
            linewidth=0.6,
            clip_on=False,
        )
        ax.add_patch(rect)
        ax.text(
            x + width / 2.0,
            y + height / 2.0,
            label,
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=9,
            fontweight="bold",
            color="black",
            clip_on=False,
        )

    draw_column_group("App TX Goodput (KiB/s)", 1, 3)
    draw_column_group("App RX Goodput (KiB/s)", 4, 6)

    rates = sorted({int(float(row["payment_rate"])) for row in rows if row.get("payment_rate") not in (None, "")})
    title = "Average MeshPay Application Goodput Before, During, and After 50% Packet-Loss Attack"
    if len(rates) == 1:
        title += f"\n{rates[0]} TPS"

    _save(fig, output_dir, "goodput_50_loss_table")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("summary", help="Path to summary.json produced by the benchmark matrix.")
    p.add_argument("-o", "--output", default=None,
                   help="Output directory for figures (default: same dir as summary.json).")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    summary_path = Path(args.summary).resolve()
    if not summary_path.exists():
        print(f"Error: {summary_path} not found.", file=sys.stderr)
        return 1

    output_dir = Path(args.output).resolve() if args.output else summary_path.parent / "figures"
    print(f"Loading {summary_path} …")
    runs = load_summary(summary_path)
    print(f"  {len(runs)} benchmark runs loaded.")
    print(f"Output directory: {output_dir}\n")

    print("Figure 0a: Confirmation rate by phase vs. packet loss")
    fig_cohort_phase_rate_vs_loss(runs, output_dir)

    print("Figure 0b: Quorum latency by phase vs. packet loss")
    fig_quorum_latency_by_phase(runs, output_dir)

    print("Figure 1: Confirmation rate vs. packet loss")
    fig_confirmation_rate_vs_loss(runs, output_dir)

    print("Figure 2: Acceptance rate vs. packet loss")
    fig_acceptance_rate_vs_loss(runs, output_dir)

    print("Figure 3: Confirmation rate heatmap")
    fig_heatmap_confirmation_rate(runs, output_dir)

    print("Figure 4: Network Throughput vs. Packet Loss")
    fig_network_throughput_vs_loss(runs, output_dir)

    print("Figure 5: Network throughput time series at 50% packet loss")
    fig_network_throghput_impact(runs, output_dir)

    print("Figure 6: Quorum latency vs. rate")
    fig_quorum_latency_vs_loss(runs, output_dir)

    print("Figure 7: Avg hop count vs. packet loss")
    fig_hop_count_vs_loss(runs, output_dir)

    print("Figure 8: Application goodput before/during/after attack")
    fig_bandwidth_phase_table(runs, output_dir)

    print("Figure 9: Network throughput before/during/after attack")
    fig_network_phase_table(runs, output_dir)

    print("Figure 10: Application goodput at 50% packet loss")
    fig_goodput_50_loss_table(runs, output_dir)

    print("Table 11: Payment cohorts by creation phase")
    fig_cohort_phase_table(runs, output_dir)

    print("Table 12: Packet-loss attack validation")
    fig_attack_validation_table(runs, output_dir)

    print("Table 13: Post-attack payment funnel")
    fig_post_attack_funnel_table(runs, output_dir)

    print("\nAll figures generated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
