"""Comparison figure generation for MeshPay emulation benchmarks."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from mininet.log import info

from meshpay.examples.emulation.config import BenchmarkStats


def workspace_root() -> Path:
    """Return the repository root for default benchmark artifacts."""

    return Path(__file__).resolve().parents[3]


def _as_dict(stats: BenchmarkStats | Mapping[str, Any]) -> Dict[str, Any]:
    if isinstance(stats, BenchmarkStats):
        return stats.to_dict()
    return dict(stats)


def resolve_plot_output(plot_output: str = "") -> Path:
    """Resolve the comparison plot output path."""

    if plot_output:
        return Path(plot_output).expanduser()
    return workspace_root() / "meshpay_routing_comparison.png"


def generate_plots(
    epidemic_stats: BenchmarkStats | Mapping[str, Any],
    sdn_stats: BenchmarkStats | Mapping[str, Any],
    plot_output: str = "",
) -> Path:
    """Generate Matplotlib figures comparing Epidemic vs SDN-DTN."""

    import matplotlib.pyplot as plt

    epidemic = _as_dict(epidemic_stats)
    sdn = _as_dict(sdn_stats)

    plt.rcParams.update(
        {
            "font.size": 11,
            "axes.labelsize": 12,
            "axes.titlesize": 13,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "figure.titlesize": 16,
            "grid.alpha": 0.3,
        }
    )

    fig, axs = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle("MeshPay Performance Evaluation: Epidemic vs. SDN-DTN", fontweight="bold")

    colors = ["#F25C54", "#3A86C8"]
    labels = ["Epidemic Baseline", "SDN-DTN Routing"]

    axs[0, 0].bar(labels, [epidemic["finality_rate"], sdn["finality_rate"]], color=colors, width=0.5, edgecolor="black", alpha=0.9)
    axs[0, 0].set_title("Payment Transaction Finality Rate")
    axs[0, 0].set_ylabel("Finality Rate (%)")
    axs[0, 0].set_ylim(0, 105)
    axs[0, 0].grid(axis="y", linestyle="--")
    for i, val in enumerate([epidemic["finality_rate"], sdn["finality_rate"]]):
        axs[0, 0].text(i, val + 2, f"{val:.1f}%", ha="center", fontweight="bold")

    axs[0, 1].bar(labels, [epidemic["avg_latency_ms"], sdn["avg_latency_ms"]], color=colors, width=0.5, edgecolor="black", alpha=0.9)
    axs[0, 1].set_title("Average End-to-End Latency")
    axs[0, 1].set_ylabel("Latency (ms)")
    axs[0, 1].grid(axis="y", linestyle="--")
    max_lat = max(epidemic["avg_latency_ms"], sdn["avg_latency_ms"])
    axs[0, 1].set_ylim(0, max_lat * 1.2 if max_lat > 0 else 100)
    for i, val in enumerate([epidemic["avg_latency_ms"], sdn["avg_latency_ms"]]):
        axs[0, 1].text(i, val + (max_lat * 0.03 if max_lat > 0 else 2), f"{val:.1f} ms", ha="center", fontweight="bold")

    axs[1, 0].bar(labels, [epidemic["control_bytes"] / 1024.0, sdn["control_bytes"] / 1024.0], color=colors, width=0.5, edgecolor="black", alpha=0.9)
    axs[1, 0].set_title("Routing & Control Overhead")
    axs[1, 0].set_ylabel("Overhead (KB)")
    axs[1, 0].grid(axis="y", linestyle="--")
    max_ctrl = max(epidemic["control_bytes"], sdn["control_bytes"]) / 1024.0
    axs[1, 0].set_ylim(0, max_ctrl * 1.2 if max_ctrl > 0 else 10)
    for i, val in enumerate([epidemic["control_bytes"] / 1024.0, sdn["control_bytes"] / 1024.0]):
        axs[1, 0].text(i, val + (max_ctrl * 0.03 if max_ctrl > 0 else 0.5), f"{val:.2f} KB", ha="center", fontweight="bold")

    axs[1, 1].bar(labels, [epidemic["avg_buffer_size"], sdn["avg_buffer_size"]], color=colors, width=0.5, edgecolor="black", alpha=0.9)
    axs[1, 1].set_title("Client Remaining Buffer Occupancy")
    axs[1, 1].set_ylabel("Average Items in Buffer")
    axs[1, 1].grid(axis="y", linestyle="--")
    max_buf = max(epidemic["avg_buffer_size"], sdn["avg_buffer_size"])
    axs[1, 1].set_ylim(0, max_buf * 1.2 if max_buf > 0 else 5)
    for i, val in enumerate([epidemic["avg_buffer_size"], sdn["avg_buffer_size"]]):
        axs[1, 1].text(i, val + (max_buf * 0.03 if max_buf > 0 else 0.2), f"{val:.1f} items", ha="center", fontweight="bold")

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plot_path = resolve_plot_output(plot_output)
    plot_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(plot_path, dpi=300)
    plt.close()
    info(f"\n📊 Comparative metrics graph saved successfully to:\n   {plot_path}\n")
    return plot_path

