#!/usr/bin/env python3
"""Generate publication-quality figures from the benchmark summary.json.

Usage:
    python3 scripts/plot_summary.py \\
        logs/benchmarks/scripts/summary.json \\
        -o figures/summary/

Figures produced:
    1. confirmation_rate_vs_loss.{pdf,png}
       Confirmation rate (%) vs. packet-loss probability, one line per
       (routing, payment-rate) combination.

    2. acceptance_rate_vs_loss.{pdf,png}
       Payment acceptance rate (%) vs. packet-loss probability.

    3. quorum_latency_vs_loss.{pdf,png}
       Average time-to-quorum (s) vs. packet-loss probability.

    4. confirmed_tps_vs_rate.{pdf,png}
       Confirmed TPS vs. payment-rate for each routing protocol,
       grouped by loss probability.

    5. heatmap_confirmation_rate.{pdf,png}
       Heatmap of confirmation-rate: routing × loss_probability, for each rate.

    6. latency_vs_rate.{pdf,png}
       Quorum latency (s) vs. payment-rate (TPS) for each protocol.

    7. network_efficiency.{pdf,png}
       Confirmed TPS per KB/s of total network traffic (efficiency metric).

    8. confirmation_vs_acceptance_ratio.{pdf,png}
       Confirmation / Acceptance ratio.

    9. p95_vs_p50_latency.{pdf,png}
       P95 vs. P50 quorum latency scatter.

    10. quorum_latency_p50_p95_vs_loss.{pdf,png}
        Quorum latency P50 & P95 vs. packet-loss probability.

    11. network_throughput_vs_loss.{pdf,png}
        Network-layer throughput (TX/RX) vs. packet-loss probability.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
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

LOSS_COLORS = {
    0.0:  "#2196F3",
    0.25: "#4CAF50",
    0.5:  "#FF9800",
    0.8:  "#F44336",
}
LOSS_LABELS = {
    0.0:  "0% loss",
    0.25: "25% loss",
    0.5:  "50% loss",
    0.8:  "80% loss",
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


def normalise_loss(v: Any) -> float:
    if v is None:
        return 0.0
    return round(float(v), 4)


def ms_to_seconds(v: Any) -> float | None:
    if v is None:
        return None
    return float(v) / 1000.0


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
# Figure 3 — Quorum latency vs. loss
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
# Figure 4 — Confirmed TPS vs. payment-rate
# ---------------------------------------------------------------------------

def fig_confirmed_tps_vs_rate(runs: List[Dict], output_dir: Path) -> None:
    losses = sorted({normalise_loss(r["param.attack_loss_probability"]) for r in runs})
    n = len(losses)
    fig, axes = plt.subplots(1, n, figsize=(4.5 * n, 5), constrained_layout=True, sharey=True)
    if n == 1:
        axes = [axes]

    for ax, loss in zip(axes, losses):
        subset = [r for r in runs if normalise_loss(r["param.attack_loss_probability"]) == loss]
        for routing in ROUTINGS:
            pts = sorted(
                [(int(r["param.payment_rate"]), r["confirmed_tps"])
                 for r in subset if r["param.routing"] == routing],
                key=lambda x: x[0],
            )
            if not pts:
                continue
            rates, vals = zip(*pts)
            ax.plot(
                rates, vals,
                color=ROUTING_COLORS[routing],
                marker=ROUTING_MARKERS[routing],
                linewidth=2.0, markersize=7,
                label=ROUTING_LABELS[routing],
            )
        ax.set_title(f"Loss={int(loss * 100)}%", fontsize=12, fontweight="bold")
        ax.set_xlabel("Payment Rate (TPS)", fontsize=10)
        ax.set_xscale("log")
        ax.xaxis.set_major_formatter(mticker.ScalarFormatter())
        ax.set_xticks(RATES)
        _style_ax(ax)

    axes[0].set_ylabel("Confirmed TPS", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    fig.suptitle("Confirmed TPS vs. Payment Rate", fontsize=13, fontweight="bold")
    _save(fig, output_dir, "confirmed_tps_vs_rate")


# ---------------------------------------------------------------------------
# Figure 5 — Heatmap of confirmation rate
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
# Figure 6 — Latency vs. rate
# ---------------------------------------------------------------------------

def fig_latency_vs_rate(runs: List[Dict], output_dir: Path) -> None:
    losses = sorted({normalise_loss(r["param.attack_loss_probability"]) for r in runs})
    n = len(losses)
    fig, axes = plt.subplots(1, n, figsize=(4.5 * n, 5), constrained_layout=True, sharey=True)
    if n == 1:
        axes = [axes]

    for ax, loss in zip(axes, losses):
        subset = [r for r in runs if normalise_loss(r["param.attack_loss_probability"]) == loss]
        for routing in ROUTINGS:
            pts = sorted(
                [(int(r["param.payment_rate"]), latency_s)
                 for r in subset
                 if r["param.routing"] == routing
                 for latency_s in [ms_to_seconds(r.get("avg_time_to_quorum_ms"))]
                 if latency_s is not None],
                key=lambda x: x[0],
            )
            if not pts:
                continue
            rates, vals = zip(*pts)
            ax.plot(
                rates, vals,
                color=ROUTING_COLORS[routing],
                marker=ROUTING_MARKERS[routing],
                linewidth=2.0, markersize=7,
                label=ROUTING_LABELS[routing],
            )
        ax.set_title(f"Loss={int(loss * 100)}%", fontsize=12, fontweight="bold")
        ax.set_xlabel("Payment Rate (TPS)", fontsize=10)
        ax.set_xscale("log")
        ax.xaxis.set_major_formatter(mticker.ScalarFormatter())
        ax.set_xticks(RATES)
        _style_ax(ax)

    axes[0].set_ylabel("Avg. Time-to-Quorum (s)", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    fig.suptitle("Quorum Latency vs. Payment Rate", fontsize=13, fontweight="bold")
    _save(fig, output_dir, "latency_vs_rate")


# ---------------------------------------------------------------------------
# Figure 7 — Network efficiency (confirmed_tps / total_kbps)
# ---------------------------------------------------------------------------

def fig_network_efficiency(runs: List[Dict], output_dir: Path) -> None:
    losses = sorted({normalise_loss(r["param.attack_loss_probability"]) for r in runs})
    n = len(losses)
    fig, axes = plt.subplots(1, n, figsize=(4.5 * n, 5), constrained_layout=True, sharey=True)
    if n == 1:
        axes = [axes]

    for ax, loss in zip(axes, losses):
        subset = [r for r in runs if normalise_loss(r["param.attack_loss_probability"]) == loss]
        for routing in ROUTINGS:
            pts = []
            for r in subset:
                if r["param.routing"] != routing:
                    continue
                total_kbps = r["tx_plus_rx_bytes_per_second"] / 1024.0
                if total_kbps > 0:
                    eff = r["confirmed_tps"] / total_kbps
                    pts.append((int(r["param.payment_rate"]), eff))
            pts = sorted(pts, key=lambda x: x[0])
            if not pts:
                continue
            rates, vals = zip(*pts)
            ax.plot(
                rates, vals,
                color=ROUTING_COLORS[routing],
                marker=ROUTING_MARKERS[routing],
                linewidth=2.0, markersize=7,
                label=ROUTING_LABELS[routing],
            )
        ax.set_title(f"Loss={int(loss * 100)}%", fontsize=12, fontweight="bold")
        ax.set_xlabel("Payment Rate (TPS)", fontsize=10)
        ax.set_xscale("log")
        ax.xaxis.set_major_formatter(mticker.ScalarFormatter())
        ax.set_xticks(RATES)
        _style_ax(ax)

    axes[0].set_ylabel("Efficiency (confirmed TPS / KB/s)", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    fig.suptitle("Network Efficiency: Confirmed TPS per KB/s of Traffic", fontsize=13, fontweight="bold")
    _save(fig, output_dir, "network_efficiency")


# ---------------------------------------------------------------------------
# Figure 8 — Confirmation vs. Acceptance delta (overconfirmation anomaly)
# ---------------------------------------------------------------------------

def fig_confirmation_vs_acceptance(runs: List[Dict], output_dir: Path) -> None:
    """Show confirmation count vs. acceptance count to highlight the anomaly
    where confirmed > accepted (multiple authority votes per payment)."""
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
                  r["payments_confirmed"] / max(r["payments_accepted"], 1))
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
        ax.axhline(1.0, color="gray", linestyle="--", linewidth=1.2, label="1:1 baseline")
        ax.set_title(f"{rate} TPS", fontsize=12, fontweight="bold")
        ax.set_xlabel("Packet Loss Probability", fontsize=10)
        ax.set_xticks(LOSSES)
        _style_ax(ax)

    axes[0].set_ylabel("Confirmations / Acceptances", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    fig.suptitle("Confirmation / Acceptance Ratio\n(>1 means more confirmations than client-side acceptances)",
                 fontsize=13, fontweight="bold")
    _save(fig, output_dir, "confirmation_vs_acceptance_ratio")


# ---------------------------------------------------------------------------
# Figure 9 — P95 latency spread vs. median (tail-latency stability)
# ---------------------------------------------------------------------------

def fig_p95_vs_p50_latency(runs: List[Dict], output_dir: Path) -> None:
    """Scatter p95 vs. p50 quorum latency to show tail behaviour."""
    fig, axes = plt.subplots(1, len(ROUTINGS), figsize=(5.5 * len(ROUTINGS), 5),
                              constrained_layout=True, sharey=True, sharex=True)

    for ax, routing in zip(axes, ROUTINGS):
        subset = [r for r in runs if r["param.routing"] == routing]
        for loss in LOSSES:
            pts = [(p50_s, p95_s)
                   for r in subset
                   if normalise_loss(r["param.attack_loss_probability"]) == loss
                   for p50_s in [ms_to_seconds(r.get("p50_time_to_quorum_ms"))]
                   for p95_s in [ms_to_seconds(r.get("p95_time_to_quorum_ms"))]
                   if p50_s is not None and p95_s is not None]
            if not pts:
                continue
            p50s, p95s = zip(*pts)
            ax.scatter(p50s, p95s,
                       color=LOSS_COLORS.get(loss, "gray"),
                       label=LOSS_LABELS.get(loss, f"{loss}"),
                       s=80, edgecolors="white", linewidths=0.5, zorder=3)

        # Identity line
        lims = [0, max(ax.get_xlim()[1], ax.get_ylim()[1]) * 1.05]
        ax.plot(lims, lims, "k--", linewidth=0.8, alpha=0.4, label="P50=P95")
        ax.set_xlim(0, None)
        ax.set_ylim(0, None)
        ax.set_title(ROUTING_LABELS[routing], fontsize=12, fontweight="bold")
        ax.set_xlabel("P50 Quorum Latency (s)", fontsize=10)
        _style_ax(ax)

    axes[0].set_ylabel("P95 Quorum Latency (s)", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9)
    fig.suptitle("P95 vs. P50 Time-to-Quorum (Tail-Latency Analysis)", fontsize=13, fontweight="bold")
    _save(fig, output_dir, "p95_vs_p50_latency")


# ---------------------------------------------------------------------------
# Figure 10 — Quorum latency P50 & P95 vs. packet-loss probability
# ---------------------------------------------------------------------------

def fig_latency_p50_p95_vs_loss(runs: List[Dict], output_dir: Path) -> None:
    rates = sorted({int(r["param.payment_rate"]) for r in runs})
    n = len(rates)
    fig, axes = plt.subplots(1, n, figsize=(4.5 * n, 5), constrained_layout=True, sharey=True)
    if n == 1:
        axes = [axes]

    for ax, rate in zip(axes, rates):
        subset = [r for r in runs if int(r["param.payment_rate"]) == rate]
        for routing in ROUTINGS:
            pts_50 = sorted(
                [(normalise_loss(r["param.attack_loss_probability"]),
                  r.get("p50_time_to_quorum_ms", 0.0) / 1000.0)
                 for r in subset if r["param.routing"] == routing and r.get("p50_time_to_quorum_ms") is not None],
                key=lambda x: x[0],
            )
            pts_95 = sorted(
                [(normalise_loss(r["param.attack_loss_probability"]),
                  r.get("p95_time_to_quorum_ms", 0.0) / 1000.0)
                 for r in subset if r["param.routing"] == routing and r.get("p95_time_to_quorum_ms") is not None],
                key=lambda x: x[0],
            )
            if pts_50:
                losses_50, vals_50 = zip(*pts_50)
                ax.plot(
                    losses_50, vals_50,
                    color=ROUTING_COLORS[routing],
                    marker=ROUTING_MARKERS[routing],
                    linewidth=2.0, markersize=7,
                    linestyle="-",
                    label=f"{ROUTING_LABELS[routing]} (P50)",
                )
            if pts_95:
                losses_95, vals_95 = zip(*pts_95)
                ax.plot(
                    losses_95, vals_95,
                    color=ROUTING_COLORS[routing],
                    marker=ROUTING_MARKERS[routing],
                    linewidth=1.5, markersize=5,
                    linestyle="--",
                    label=f"{ROUTING_LABELS[routing]} (P95)",
                )
        ax.set_title(f"{rate} TPS", fontsize=12, fontweight="bold")
        ax.set_xlabel("Packet Loss Probability", fontsize=10)
        ax.set_xticks(LOSSES)
        _style_ax(ax)

    axes[0].set_ylabel("Quorum Latency P50/P95 (s)", fontsize=11, fontweight="bold")
    axes[-1].legend(fontsize=8, edgecolor="#BDBDBD", framealpha=0.9)
    fig.suptitle("Quorum Latency P50 & P95 vs. Packet-Loss Probability", fontsize=13, fontweight="bold")
    _save(fig, output_dir, "quorum_latency_p50_p95_vs_loss")


# ---------------------------------------------------------------------------
# Figure 11 — Network-layer throughput (TX/RX) vs. packet-loss probability
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

    print("Figure 3: Quorum latency vs. packet loss")
    fig_quorum_latency_vs_loss(runs, output_dir)

    print("Figure 4: Confirmed TPS vs. payment rate")
    fig_confirmed_tps_vs_rate(runs, output_dir)

    print("Figure 5: Confirmation rate heatmap")
    fig_heatmap_confirmation_rate(runs, output_dir)

    print("Figure 6: Latency vs. payment rate")
    fig_latency_vs_rate(runs, output_dir)

    print("Figure 7: Network efficiency")
    fig_network_efficiency(runs, output_dir)

    print("Figure 8: Confirmation / Acceptance ratio (overconfirmation anomaly)")
    fig_confirmation_vs_acceptance(runs, output_dir)

    print("Figure 9: P95 vs P50 tail-latency scatter")
    fig_p95_vs_p50_latency(runs, output_dir)

    print("Figure 10: Quorum Latency P50/P95 vs. Attack Intensity")
    fig_latency_p50_p95_vs_loss(runs, output_dir)

    print("Figure 11: Network Throughput vs. Packet Loss")
    fig_network_throughput_vs_loss(runs, output_dir)

    print("\nAll figures generated successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
