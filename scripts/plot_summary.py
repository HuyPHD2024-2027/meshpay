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

    6. bandwidth_phase_table.{pdf,png,csv,md}
       Average MeshPay application TX/RX goodput before, during, and after each attack.
"""

from __future__ import annotations

import argparse
import csv
import json
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

    phases = {
        "before": (attack_start - max(tpre, 0.0), attack_start),
        "during": (attack_start, attack_stop),
        "after": (attack_stop, attack_stop + max(tpost, 0.0)),
    }

    attack_targets = attack_event.get("targets", run.get("attack_targets", ""))
    if isinstance(attack_targets, list):
        attack_targets_text = ",".join(str(target) for target in attack_targets)
    else:
        attack_targets_text = str(attack_targets)

    row: Dict[str, Any] = {
        "routing": run.get("param.routing", run.get("routing", "")),
        "payment_rate": run.get("param.payment_rate", run.get("payment_rate", "")),
        "packet_loss": normalise_loss(run.get("param.attack_loss_probability", 0.0)),
        "run_id": run.get("run_id", Path(run_dir_raw).name),
        "attack_targets": attack_targets_text,
        "network_stat_nodes": len({
            str(event.get("node"))
            for event in events
            if event.get("event") == "network_stats" and event.get("node")
        }),
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
        "network_stat_nodes",
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
        ax.set_title(f"{rate} TPS", fontsize=12, fontweight="bold")
        ax.set_xlabel("Packet Loss Probability", fontsize=10)
        ax.set_xticks(LOSSES)
        _style_ax(ax)

    axes[0].set_ylabel("Confirmation Rate (%)", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    fig.suptitle("Confirmation Rate vs. Packet-Loss Probability", fontsize=13, fontweight="bold")
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
        ax.set_title(f"{rate} TPS", fontsize=12, fontweight="bold")
        ax.set_xlabel("Packet Loss Probability", fontsize=10)
        ax.set_xticks(LOSSES)
        _style_ax(ax)

    axes[0].set_ylabel("Acceptance Rate (%)", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    fig.suptitle("Payment Acceptance Rate vs. Packet-Loss Probability", fontsize=13, fontweight="bold")
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
        ax.set_title(f"{rate} TPS", fontsize=12, fontweight="bold")
        ax.set_xlabel("Packet Loss", fontsize=10)
        plt.colorbar(im, ax=ax, label="Confirmation Rate (%)")

        for i in range(len(ROUTINGS)):
            for j in range(len(LOSSES)):
                ax.text(j, i, f"{data[i, j]:.1f}%",
                        ha="center", va="center", fontsize=9,
                        color="black" if 20 < data[i, j] < 80 else "white")

    fig.suptitle("Confirmation Rate Heatmap (Routing × Loss × Rate)", fontsize=13, fontweight="bold")
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
                  r.get("network_tx_bytes_per_second", 0.0) / 1024.0)
                 for r in subset if r["param.routing"] == routing and r.get("network_tx_bytes_per_second") is not None],
                key=lambda x: x[0],
            )
            pts_rx = sorted(
                [(normalise_loss(r["param.attack_loss_probability"]),
                  r.get("network_rx_bytes_per_second", 0.0) / 1024.0)
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
        ax.set_title(f"{rate} TPS", fontsize=12, fontweight="bold")
        ax.set_xlabel("Packet Loss Probability", fontsize=10)
        ax.set_xticks(LOSSES)
        _style_ax(ax)

    axes[0].set_ylabel("Network Throughput (KB/s)", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=8, edgecolor="#BDBDBD", framealpha=0.9)
    fig.suptitle("Network-Layer Throughput (TX/RX) vs. Packet-Loss Probability", fontsize=13, fontweight="bold")
    _save(fig, output_dir, "network_throughput_vs_loss")


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
        ax.set_title(f"{rate} TPS", fontsize=12, fontweight="bold")
        ax.set_xlabel("Packet Loss Probability", fontsize=10)
        ax.set_xticks(LOSSES)
        _style_ax(ax)

    axes[0].set_ylabel("Avg. Time-to-Quorum (s)", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    fig.suptitle("Average Quorum Latency vs. Packet-Loss Probability", fontsize=13, fontweight="bold")
    _save(fig, output_dir, "quorum_latency_vs_loss")



# ---------------------------------------------------------------------------
# Figure 6 — Application goodput before/during/after attack
# ---------------------------------------------------------------------------

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

    draw_column_group("Avg. Peer TX Rate (KiB/s)", 2, 4)
    draw_column_group("Avg. Peer RX Rate (KiB/s)", 5, 7)

    title = "Average MeshPay Application Goodput Before, During, and After Packet-Loss Attack"
    rates = sorted({int(float(row["payment_rate"])) for row in rows if row.get("payment_rate") not in (None, "")})
    if len(rates) == 1:
        title += f"\n{rates[0]} TPS"
    fig.suptitle(title, fontsize=13, fontweight="bold", y=0.98)

    _save(fig, output_dir, "bandwidth_phase_table")


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

    draw_column_group("Avg. Peer TX Rate (KiB/s)", 1, 3)
    draw_column_group("Avg. Peer RX Rate (KiB/s)", 4, 6)

    rates = sorted({int(float(row["payment_rate"])) for row in rows if row.get("payment_rate") not in (None, "")})
    title = "Average MeshPay Application Goodput Before, During, and After 50% Packet-Loss Attack"
    if len(rates) == 1:
        title += f"\n{rates[0]} TPS"
    fig.suptitle(title, fontsize=13, fontweight="bold", y=0.98)

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

    print("Figure 1: Confirmation rate vs. packet loss")
    fig_confirmation_rate_vs_loss(runs, output_dir)

    print("Figure 2: Acceptance rate vs. packet loss")
    fig_acceptance_rate_vs_loss(runs, output_dir)

    print("Figure 3: Confirmation rate heatmap")
    fig_heatmap_confirmation_rate(runs, output_dir)

    print("Figure 4: Network Throughput vs. Packet Loss")
    fig_network_throughput_vs_loss(runs, output_dir)
    
    print("Figure 5: Quorum latency vs. rate")
    fig_quorum_latency_vs_loss(runs, output_dir)

    print("Figure 6: Application goodput before/during/after attack")
    fig_bandwidth_phase_table(runs, output_dir)

    print("Figure 7: Application goodput at 50% packet loss")
    fig_goodput_50_loss_table(runs, output_dir)

    print("\nAll figures generated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
