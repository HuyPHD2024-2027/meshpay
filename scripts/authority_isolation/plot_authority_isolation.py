#!/usr/bin/env python3
"""Generate conference figures and companion tables for authority isolation."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROUTING_COLORS = {
    "epidemic": "#0072B2",
    "spray-and-wait": "#D55E00",
    "prophet": "#009E73",
}


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return rows


def write_csv(path: Path, rows: Iterable[dict]) -> None:
    rows = list(rows)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: Iterable[dict]) -> None:
    rows = list(rows)
    fields = sorted({key for row in rows for key in row})
    lines = ["| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
    lines.extend("| " + " | ".join(str(row.get(field, "")) for field in fields) + " |" for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def bootstrap_mean_ci(values: list[float], seed: int = 2026, samples: int = 2000) -> tuple[float, float, float]:
    if not values:
        return math.nan, math.nan, math.nan
    mean = statistics.mean(values)
    if len(values) == 1:
        return mean, mean, mean
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(samples))
    return mean, means[int(0.025 * (samples - 1))], means[int(0.975 * (samples - 1))]


def aggregate(rows: list[dict], dimensions: list[str], measures: list[str]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("exit_code") not in {None, 0}:
            continue
        key = tuple(row.get(field) for field in dimensions)
        groups[key].append(row)
    output = []
    for key, group in groups.items():
        result = dict(zip(dimensions, key))
        result["runs"] = len(group)
        result["seeds"] = ",".join(str(row.get("param.seed")) for row in group)
        for measure in measures:
            values = [float(row[measure]) for row in group if isinstance(row.get(measure), (int, float))]
            mean, low, high = bootstrap_mean_ci(values)
            result[measure] = mean
            result[f"{measure}_ci_low"] = low
            result[f"{measure}_ci_high"] = high
        output.append(result)
    return sorted(output, key=lambda row: tuple(str(row.get(field)) for field in dimensions))


def _save(fig, output: Path, name: str) -> None:
    fig.tight_layout()
    fig.savefig(output / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(output / f"{name}.png", dpi=240, bbox_inches="tight")


def _style(ax) -> None:
    ax.grid(alpha=0.25)
    ax.spines[["top", "right"]].set_visible(False)


def attack_schematic(output: Path) -> None:
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 3.8))
    nodes = [(0.7, 2.5, "Clients", "#56B4E9"), (2.8, 2.5, "DTN relays", "#999999"),
             (5.2, 3.0, "Reachable\nauthorities", "#009E73"), (5.2, 1.1, "Isolated\nauthorities", "#CC79A7")]
    for x, y, label, color in nodes:
        ax.scatter([x], [y], s=1800, color=color, edgecolor="black", zorder=3)
        ax.text(x, y, label, ha="center", va="center", fontsize=10, zorder=4)
    for left, right in [((1.2, 2.5), (2.3, 2.5)), ((3.3, 2.5), (4.6, 3.0))]:
        ax.annotate("", xy=right, xytext=left, arrowprops={"arrowstyle": "<->", "lw": 2})
    ax.plot([3.7, 6.1], [1.8, 1.8], "--", color="black")
    ax.text(6.15, 1.8, "strict > 2/3 reachable power", va="center")
    ax.annotate("network cut / loss / range", xy=(4.7, 1.3), xytext=(2.2, 0.55),
                arrowprops={"arrowstyle": "-|>", "color": "#CC79A7"}, color="#CC79A7")
    ax.set_xlim(-0.3, 8.5); ax.set_ylim(0, 4); ax.axis("off")
    _save(fig, output, "attack_schematic")


def primary_threshold(rows: list[dict], output: Path) -> None:
    import matplotlib.pyplot as plt
    data = aggregate(rows, ["param.routing", "actual_reachable_power"],
                     ["payment_confirmation_rate_percent", "payment_acceptance_rate_percent"])
    write_csv(output / "primary_threshold.csv", data)
    routings = sorted({str(row["param.routing"]) for row in data})
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharex=True, sharey=True)
    for ax, measure, title in zip(axes,
            ["payment_confirmation_rate_percent", "payment_acceptance_rate_percent"],
            ["Confirmation", "Acceptance"]):
        for routing in routings:
            points = sorted((row for row in data if row["param.routing"] == routing), key=lambda row: float(row["actual_reachable_power"]))
            x = [float(row["actual_reachable_power"]) for row in points]
            y = [row[measure] for row in points]
            low = [row[f"{measure}_ci_low"] for row in points]
            high = [row[f"{measure}_ci_high"] for row in points]
            ax.plot(x, y, marker="o", label=routing, color=ROUTING_COLORS.get(routing))
            ax.fill_between(x, low, high, alpha=0.18, color=ROUTING_COLORS.get(routing))
        ax.axvline(2 / 3, ls="--", color="black", label="strict 2/3 boundary")
        ax.set_title(title); ax.set_xlabel("Actual reachable voting power"); _style(ax)
    axes[0].set_ylabel("Rate (%)"); axes[-1].legend(fontsize=8)
    _save(fig, output, "primary_threshold")


def representative_run(rows: list[dict]) -> tuple[dict, dict] | None:
    candidates = []
    for row in rows:
        run_dir = Path(str(row.get("run_dir", "")))
        benchmark = load_json(run_dir / "benchmark.json", {})
        if benchmark.get("payment_metrics", {}).get("time_bins_10s"):
            distance = abs(float(row.get("actual_reachable_power", 1.0)) - 0.60)
            candidates.append((distance, str(run_dir), row, benchmark))
    if not candidates:
        return None
    _distance, _path, row, benchmark = min(candidates)
    return row, benchmark


def progress_timeline(rows: list[dict], output: Path) -> None:
    import matplotlib.pyplot as plt
    selected = representative_run(rows)
    if not selected:
        return
    row, benchmark = selected
    bins = benchmark["payment_metrics"]["time_bins_10s"]
    write_csv(output / "progress_timeline.csv", bins)
    attack = benchmark.get("attack", {})
    start = benchmark.get("timing", {}).get("started_at", bins[0]["start"])
    attack_start = next((event.get("time") for event in load_jsonl(Path(row["run_dir"]) / "payment.log") if event.get("event") == "attack_started"), None)
    attack_stop = next((event.get("time") for event in load_jsonl(Path(row["run_dir"]) / "payment.log") if event.get("event") == "attack_stopped"), None)
    x = [(item["start"] + item["end"]) / 2 - start for item in bins]
    fig, ax = plt.subplots(figsize=(9, 4)); latency = ax.twinx()
    ax.step(x, [item["confirmed_tps"] for item in bins], where="mid", color="#0072B2", label="Confirmed TPS")
    latency.plot(x, [item["time_to_quorum_ms_p50"] / 1000 if item["time_to_quorum_ms_p50"] is not None else math.nan for item in bins], "o-", color="#E69F00", label="p50 TTQ")
    latency.plot(x, [item["time_to_quorum_ms_p95"] / 1000 if item["time_to_quorum_ms_p95"] is not None else math.nan for item in bins], "s--", color="#D55E00", label="p95 TTQ")
    if attack_start is not None and attack_stop is not None:
        ax.axvspan(attack_start - start, attack_stop - start, color="gray", alpha=0.25)
    ax.set(xlabel="Time since traffic start (s)", ylabel="Confirmation TPS")
    latency.set_ylabel("Time to quorum (s)"); _style(ax)
    ax.legend(loc="upper left"); latency.legend(loc="upper right")
    _save(fig, output, "progress_timeline")


def network_timeline(rows: list[dict], output: Path) -> None:
    import matplotlib.pyplot as plt
    selected = representative_run(rows)
    if not selected:
        return
    row, benchmark = selected
    raw = load_jsonl(Path(row["run_dir"]) / "network_raw.jsonl")
    isolated = set(benchmark.get("attack", {}).get("targets", []))
    samples: dict[str, list[dict]] = defaultdict(list)
    for item in raw:
        samples[str(item.get("node"))].append(item)
    out = []
    for node, values in samples.items():
        values.sort(key=lambda item: float(item.get("time", 0)))
        role = "isolated authorities" if node in isolated and node.startswith("auth") else ("reachable authorities" if node.startswith("auth") else "clients/relays")
        for first, last in zip(values, values[1:]):
            duration = float(last["time"]) - float(first["time"])
            if duration <= 0:
                continue
            out.append({"time": float(last["time"]), "role": role,
                        "tx_bytes_per_second": max(int(last["tx_bytes"]) - int(first["tx_bytes"]), 0) / duration,
                        "rx_bytes_per_second": max(int(last["rx_bytes"]) - int(first["rx_bytes"]), 0) / duration})
    if not out:
        return
    t0 = min(item["time"] for item in out)
    grouped = defaultdict(lambda: {"tx_bytes_per_second": 0.0, "rx_bytes_per_second": 0.0})
    for item in out:
        key = (round(item["time"] - t0), item["role"])
        grouped[key]["tx_bytes_per_second"] += item["tx_bytes_per_second"]
        grouped[key]["rx_bytes_per_second"] += item["rx_bytes_per_second"]
    out = [{"relative_time_s": key[0], "role": key[1], **values} for key, values in sorted(grouped.items())]
    write_csv(output / "network_timeline.csv", out)
    fig, axes = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    for role in sorted({item["role"] for item in out}):
        values = [item for item in out if item["role"] == role]
        for ax, measure, label in zip(axes, ["tx_bytes_per_second", "rx_bytes_per_second"], ["TX", "RX"]):
            ax.plot([item["relative_time_s"] for item in values], [item[measure] for item in values], alpha=0.75, label=role)
            ax.set_ylabel(f"{label} bytes/s"); _style(ax)
    payment_events = load_jsonl(Path(row["run_dir"]) / "payment.log")
    attack_start = next((float(event["time"]) for event in payment_events if event.get("event") == "attack_started"), None)
    attack_stop = next((float(event["time"]) for event in payment_events if event.get("event") == "attack_stopped"), None)
    if attack_start is not None and attack_stop is not None:
        for ax in axes:
            ax.axvspan(attack_start - t0, attack_stop - t0, color="gray", alpha=0.25)
    axes[-1].set_xlabel("Time (s)"); axes[0].legend(fontsize=8)
    _save(fig, output, "network_timeline")


def recovery_figure(rows: list[dict], output: Path) -> None:
    import matplotlib.pyplot as plt
    measures = ["time_to_recover_90_percent_three_bins_s", "backlog_at_restoration", "attack_window_eventual_confirmation_percent"]
    data = aggregate(rows, ["param.routing", "actual_reachable_power"], measures)
    write_csv(output / "recovery.csv", data)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for ax, measure, title in zip(axes, measures, ["90% recovery time", "Backlog at restoration", "Eventual attack-cohort confirmation"]):
        for routing in sorted({str(row["param.routing"]) for row in data}):
            points = sorted((row for row in data if row["param.routing"] == routing), key=lambda row: float(row["actual_reachable_power"]))
            ax.plot([row["actual_reachable_power"] for row in points], [row[measure] for row in points], "o-", label=routing, color=ROUTING_COLORS.get(routing))
        ax.axvline(2 / 3, ls="--", color="black"); ax.set_title(title); ax.set_xlabel("Reachable power"); _style(ax)
    axes[0].set_ylabel("Seconds"); axes[1].set_ylabel("Payments"); axes[2].set_ylabel("Percent")
    axes[-1].legend(fontsize=8); _save(fig, output, "recovery")


def count_vs_power(rows: list[dict], output: Path) -> None:
    import matplotlib.pyplot as plt
    data = [row for row in rows if isinstance(row.get("payment_confirmation_rate_percent"), (int, float))]
    write_csv(output / "count_vs_power.csv", data)
    fig, axes = plt.subplots(1, 2, figsize=(9, 4), sharey=True)
    for routing in sorted({str(row.get("param.routing")) for row in data}):
        points = [row for row in data if row.get("param.routing") == routing]
        axes[0].scatter([row.get("reachable_authority_count") for row in points], [row["payment_confirmation_rate_percent"] for row in points], label=routing, alpha=0.75, color=ROUTING_COLORS.get(routing))
        axes[1].scatter([row.get("actual_reachable_power") for row in points], [row["payment_confirmation_rate_percent"] for row in points], label=routing, alpha=0.75, color=ROUTING_COLORS.get(routing))
    axes[0].set_xlabel("Reachable authority count"); axes[1].set_xlabel("Reachable voting power")
    axes[0].set_ylabel("Confirmation rate (%)"); axes[1].axvline(2 / 3, ls="--", color="black")
    for ax in axes: _style(ax)
    axes[-1].legend(fontsize=8); _save(fig, output, "count_vs_power")


def grouped_validation(rows: list[dict], output: Path, dimension: str, name: str, xlabel: str) -> None:
    import matplotlib.pyplot as plt
    data = aggregate(rows, [dimension, "actual_reachable_power"], ["payment_confirmation_rate_percent"])
    write_csv(output / f"{name}.csv", data)
    fig, ax = plt.subplots(figsize=(6, 4))
    for value in sorted({str(row[dimension]) for row in data}):
        points = sorted((row for row in data if str(row[dimension]) == value), key=lambda row: float(row["actual_reachable_power"]))
        ax.plot([row["actual_reachable_power"] for row in points], [row["payment_confirmation_rate_percent"] for row in points], "o-", label=value)
    ax.axvline(2 / 3, ls="--", color="black"); ax.set_xlabel("Actual reachable voting power")
    ax.set_ylabel("Confirmation rate (%)"); ax.legend(title=xlabel, fontsize=8); _style(ax); _save(fig, output, name)


def tables(rows: list[dict], output: Path) -> None:
    funnel_rows, validation_rows = [], []
    for row in rows:
        run_dir = Path(str(row.get("run_dir", "")))
        benchmark = load_json(run_dir / "benchmark.json", {})
        attack = benchmark.get("attack", {})
        for phase, phase_data in benchmark.get("payment_metrics", {}).get("post_attack_funnel", {}).get("cohorts_by_created_phase", {}).items():
            funnel_rows.append({"run_id": row.get("run_id"), "routing": row.get("param.routing"), "phase": phase,
                                "actual_reachable_power": row.get("actual_reachable_power"), **phase_data.get("totals", {})})
        validation_rows.append({
            "run_id": row.get("run_id"), "exit_code": row.get("exit_code"), "targets": attack.get("targets"),
            "requested_power": attack.get("requested_reachable_power"), "actual_power": attack.get("actual_reachable_power"),
            "weight_epoch": attack.get("pre_attack_weight_epoch"), "mode": attack.get("isolation_mode"),
            "installed_rules": (attack.get("isolation_installation") or {}).get("installed_rules"),
            "drop_packets": (attack.get("isolation_rule_counters") or {}).get("totals", {}).get("drop_packets"),
            "pre_probe_success": (attack.get("connectivity_probes") or {}).get("pre", {}).get("succeeded"),
            "during_probe_success": (attack.get("connectivity_probes") or {}).get("during", {}).get("succeeded"),
            "post_probe_success": (attack.get("connectivity_probes") or {}).get("post", {}).get("succeeded"),
            "cleanup_success": (attack.get("isolation_cleanup") or {}).get("cleanup_success"),
            "validation_success": attack.get("attack_validation_success"),
        })
    for name, data in [("phase_funnel_table", funnel_rows), ("attack_validation_table", validation_rows)]:
        write_csv(output / f"{name}.csv", data); write_markdown(output / f"{name}.md", data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path, nargs="+", help="One or more matrix summary.json files.")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for summary in args.summary:
        loaded = load_json(summary, [])
        if not isinstance(loaded, list):
            parser.error(f"{summary} must contain a JSON list")
        rows.extend(loaded)
    normalized = []
    for original in rows:
        row = dict(original)
        if row.get("param.attack") == "none":
            row.setdefault("actual_reachable_power", 1.0)
            if row.get("actual_reachable_power") is None:
                row["actual_reachable_power"] = 1.0
            row["requested_reachable_power"] = 1.0
            row["isolation_mode"] = "no_attack"
            row["reachable_authority_count"] = row.get("param.authorities")
        if isinstance(row.get("actual_reachable_power"), (int, float)):
            normalized.append(row)
    rows = normalized
    args.output.mkdir(parents=True, exist_ok=True)
    attack_schematic(args.output)
    primary_threshold(rows, args.output)
    progress_timeline(rows, args.output)
    network_timeline(rows, args.output)
    recovery_figure(rows, args.output)
    count_vs_power(rows, args.output)
    grouped_validation(rows, args.output, "isolation_mode", "mechanism_sensitivity", "Mechanism")
    grouped_validation(rows, args.output, "param.authorities", "committee_size_validation", "Committee size")
    tables(rows, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
