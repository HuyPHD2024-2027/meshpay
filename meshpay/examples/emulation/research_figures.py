"""Paper-ready figures for MeshPay emulation campaign summaries."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import matplotlib.pyplot as plt


PROTOCOL_ORDER = ("sdn_dtn", "epidemic", "prophet", "spray_and_wait")
COLORS = {
    "sdn_dtn": "#2563eb",
    "epidemic": "#dc2626",
    "prophet": "#059669",
    "spray_and_wait": "#7c3aed",
}
LABELS = {
    "sdn_dtn": "SDN-DTN",
    "epidemic": "Epidemic",
    "prophet": "PROPHET",
    "spray_and_wait": "Spray-and-Wait",
}


def _num(row: Dict[str, str], key: str) -> float:
    try:
        return float(row.get(key, 0) or 0)
    except ValueError:
        return 0.0


def load_summary(path: str | Path) -> List[Dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _save(fig, output_dir: Path, stem: str, formats: Sequence[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        fig.savefig(output_dir / f"{stem}.{fmt}", bbox_inches="tight", dpi=300)
    plt.close(fig)


def _plot_vs(rows: List[Dict[str, str]], *, campaign: str, x_key: str, y_key: str, ylabel: str, title: str, stem: str, output_dir: Path, formats: Sequence[str]) -> None:
    subset = [row for row in rows if row.get("campaign") == campaign]
    if not subset:
        return
    by_protocol = defaultdict(list)
    for row in subset:
        by_protocol[row.get("routing", "")].append(row)

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    for protocol in PROTOCOL_ORDER:
        grouped = defaultdict(list)
        for row in by_protocol.get(protocol, []):
            grouped[_num(row, x_key)].append(row)
        if not grouped:
            continue
        x_vals = sorted(grouped)
        y_vals = []
        err = []
        ci_key = y_key.replace("_mean", "_ci95")
        for x_val in x_vals:
            items = grouped[x_val]
            y_vals.append(sum(_num(row, y_key) for row in items) / len(items))
            err.append(sum(_num(row, ci_key) for row in items) / len(items))
        ax.errorbar(x_vals, y_vals, yerr=err, marker="o", linewidth=2, capsize=3, label=LABELS[protocol], color=COLORS[protocol])
    ax.set_title(title)
    ax.set_xlabel(x_key.replace("_mean", "").replace("_", " ").title())
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False)
    _save(fig, output_dir, stem, formats)


def _plot_placement(rows: List[Dict[str, str]], output_dir: Path, formats: Sequence[str]) -> None:
    subset = [row for row in rows if row.get("campaign") == "placement"]
    if not subset:
        return
    scenarios = sorted({row["scenario_name"] for row in subset})
    width = 0.18
    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    xs = list(range(len(scenarios)))
    for idx, protocol in enumerate(PROTOCOL_ORDER):
        vals = []
        for scenario in scenarios:
            match = next((row for row in subset if row.get("scenario_name") == scenario and row.get("routing") == protocol), None)
            vals.append(_num(match or {}, "finality_rate_mean"))
        offset = (idx - 1.5) * width
        ax.bar([x + offset for x in xs], vals, width=width, label=LABELS[protocol], color=COLORS[protocol])
    ax.set_xticks(xs)
    ax.set_xticklabels([s.replace("_", " ").title() for s in scenarios], rotation=15, ha="right")
    ax.set_ylabel("Finality rate (%)")
    ax.set_title("Placement Scenario Finality")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(frameon=False, ncol=2)
    _save(fig, output_dir, "placement_finality", formats)


def _plot_pareto(rows: List[Dict[str, str]], output_dir: Path, formats: Sequence[str]) -> None:
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(6.6, 4.4))
    for protocol in PROTOCOL_ORDER:
        items = [row for row in rows if row.get("routing") == protocol]
        if not items:
            continue
        x_vals = [_num(row, "total_bytes_per_success_mean") for row in items]
        y_vals = [_num(row, "finality_rate_mean") for row in items]
        ax.scatter(x_vals, y_vals, s=38, alpha=0.75, label=LABELS[protocol], color=COLORS[protocol])
    ax.set_xlabel("Bytes per successful payment")
    ax.set_ylabel("Finality rate (%)")
    ax.set_title("Finality vs Overhead")
    ax.grid(True, alpha=0.25)
    ax.legend(frameon=False)
    _save(fig, output_dir, "pareto_finality_overhead", formats)


def generate_research_figures(summary: str | Path, output_dir: str | Path, formats: Iterable[str] = ("png", "pdf")) -> None:
    rows = load_summary(summary)
    out = Path(output_dir)
    fmt = [item.strip() for item in formats if item.strip()]
    _plot_vs(rows, campaign="disruption", x_key="wireless_range", y_key="finality_rate_mean", ylabel="Finality rate (%)", title="Finality vs Wireless Range", stem="finality_vs_wireless_range", output_dir=out, formats=fmt)
    _plot_vs(rows, campaign="disruption", x_key="wireless_range", y_key="avg_latency_ms_mean", ylabel="Latency (ms)", title="Latency vs Wireless Range", stem="latency_vs_wireless_range", output_dir=out, formats=fmt)
    _plot_vs(rows, campaign="disruption", x_key="wireless_range", y_key="total_bytes_per_success_mean", ylabel="Bytes per success", title="Overhead vs Wireless Range", stem="bytes_per_success_vs_wireless_range", output_dir=out, formats=fmt)
    _plot_vs(rows, campaign="scalability", x_key="clients", y_key="finality_rate_mean", ylabel="Finality rate (%)", title="Finality vs Node Scale", stem="finality_vs_node_scale", output_dir=out, formats=fmt)
    _plot_vs(rows, campaign="scalability", x_key="clients", y_key="avg_latency_ms_mean", ylabel="Latency (ms)", title="Latency vs Node Scale", stem="latency_vs_node_scale", output_dir=out, formats=fmt)
    _plot_vs(rows, campaign="scalability", x_key="clients", y_key="avg_buffer_size_mean", ylabel="Buffer items", title="Buffer Occupancy vs Node Scale", stem="buffer_vs_node_scale", output_dir=out, formats=fmt)
    _plot_placement(rows, out, fmt)
    _plot_pareto(rows, out, fmt)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate MeshPay campaign research figures")
    parser.add_argument("--summary", required=True, help="Campaign summary.csv path")
    parser.add_argument("--output-dir", required=True, help="Figure output directory")
    parser.add_argument("--formats", default="png,pdf", help="Comma-separated formats")
    args = parser.parse_args()
    generate_research_figures(args.summary, args.output_dir, args.formats.split(","))


if __name__ == "__main__":
    main()
