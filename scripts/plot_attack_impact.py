#!/usr/bin/env python3

"""Plot attack-impact time-series from MeshPay benchmark logs.

Reads payment.log (JSONL) and benchmark_config.json from one or more
benchmark output directories, computes per-second time-series for three
key metrics, and produces a publication-quality 3-panel stacked figure.

Metrics:
    1. Confirmation Latency (ms)  — rolling-window average
    2. Network Throughput (TX+RX KB/s)
    3. Finality Rate (%)          — cumulative confirmed / created

Usage:
    # Single run:
    python3 scripts/plot_attack_impact.py /path/to/benchmark_dir

    # Multiple runs overlaid for comparison:
    python3 scripts/plot_attack_impact.py \\
        --label "100 tx" /path/to/run_100 \\
        --label "500 tx" /path/to/run_500 \\
        --label "1000 tx" /path/to/run_1000 \\
        --label "2000 tx" /path/to/run_2000 \\
        -o figures/
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for headless environments.

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Load a JSONL file, skipping malformed lines."""
    records: List[Dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def load_config(log_dir: Path) -> Dict[str, Any]:
    """Load benchmark_config.json or benchmark.json for metadata."""
    config_path = log_dir / "benchmark_config.json"
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    benchmark_path = log_dir / "benchmark.json"
    if benchmark_path.exists():
        with benchmark_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("config", data)

    return {}


# ---------------------------------------------------------------------------
# Time-series computation
# ---------------------------------------------------------------------------

def compute_time_series(
    events: List[Dict[str, Any]],
    config: Dict[str, Any],
    window_size: int = 10,
) -> Dict[str, Any]:
    """Compute per-second time-series from raw payment.log events.

    Returns a dict with:
        t_seconds:       np.array of relative time values (seconds)
        latency_ms:      np.array of rolling-avg confirmation latency
        throughput_kbps:  np.array of TX+RX KB/s per second
        finality_pct:    np.array of cumulative finality rate (%)
        attack_start_s:  float or None — relative second attack started
        attack_stop_s:   float or None — relative second attack stopped
        duration_s:      total duration in seconds
    """

    # Determine t=0 as the time of the first payment_created event.
    created_events = [e for e in events if e.get("event") == "payment_created"]
    if not created_events:
        return _empty_series()

    t0 = min(float(e["time"]) for e in created_events)

    # Determine total duration from config or from event span.
    cfg_duration = float(config.get("duration", 0))
    last_event_time = max(float(e["time"]) for e in events)
    total_seconds = max(cfg_duration, last_event_time - t0)
    total_seconds = int(math.ceil(total_seconds)) + 1

    # ---- Index events by order_id ----
    created_by_order: Dict[str, float] = {}
    for e in events:
        if e.get("event") == "payment_created" and "order_id" in e:
            created_by_order[e["order_id"]] = float(e["time"])

    # ---- Per-second bins ----
    # Latency: collect confirmation latencies falling in each second bin.
    latency_bins: Dict[int, List[float]] = defaultdict(list)
    for e in events:
        if e.get("event") != "confirmation_created":
            continue
        order_id = e.get("order_id")
        if order_id is None or order_id not in created_by_order:
            continue
        t_confirm = float(e["time"])
        t_created = created_by_order[order_id]
        latency_s = (t_confirm - t_created)
        second_bin = int(t_created - t0)
        if 0 <= second_bin < total_seconds:
            latency_bins[second_bin].append(latency_s)

    # Throughput: sum payload bytes per second (TX + RX).
    throughput_bytes: Dict[int, int] = defaultdict(int)
    for e in events:
        ev = e.get("event")
        if ev not in ("payload_injected", "payment_payload_delivered"):
            continue
        t = float(e["time"])
        second_bin = int(t - t0)
        size = int(e.get("payload_size_bytes", 0))
        if 0 <= second_bin < total_seconds:
            throughput_bytes[second_bin] += size

    # Finality: cumulative created and confirmed counts.
    created_cumulative = np.zeros(total_seconds, dtype=float)
    confirmed_cumulative = np.zeros(total_seconds, dtype=float)

    for e in events:
        if e.get("event") == "payment_created":
            second_bin = int(float(e["time"]) - t0)
            if 0 <= second_bin < total_seconds:
                created_cumulative[second_bin] += 1

        elif e.get("event") == "confirmation_created":
            second_bin = int(float(e["time"]) - t0)
            if 0 <= second_bin < total_seconds:
                confirmed_cumulative[second_bin] += 1

    # Convert to cumulative sums.
    created_cumulative = np.cumsum(created_cumulative)
    confirmed_cumulative = np.cumsum(confirmed_cumulative)

    # Finality rate (%) = confirmed / created, avoiding division by zero.
    finality_pct = np.where(
        created_cumulative > 0,
        (confirmed_cumulative / created_cumulative) * 100.0,
        0.0,
    )

    # ---- Build arrays ----
    t_seconds = np.arange(total_seconds, dtype=float)

    # Raw per-second mean latency (forward-filled to prevent dropping to 0 in sparse regions).
    raw_latency = np.zeros(total_seconds, dtype=float)
    last_val = 0.0
    for s in range(total_seconds):
        if s in latency_bins and latency_bins[s]:
            last_val = np.mean(latency_bins[s])
        raw_latency[s] = last_val

    # Rolling-window average for latency.
    latency_smoothed = _rolling_mean(raw_latency, window_size)

    # Throughput in KB/s (bytes per second -> KB/s).
    throughput_kbps = np.zeros(total_seconds, dtype=float)
    for s, total_bytes in throughput_bytes.items():
        throughput_kbps[s] = total_bytes / 1024.0

    # ---- Attack window ----
    attack_start_s = None
    attack_stop_s = None
    for e in events:
        if e.get("event") == "attack_started":
            attack_start_s = float(e["time"]) - t0
        elif e.get("event") == "attack_stopped":
            attack_stop_s = float(e["time"]) - t0

    # Fallback: derive from config if events are missing.
    if attack_start_s is None and config.get("attack", "none") != "none":
        tpre = float(config.get("attack_tpre", 60))
        tatk = float(config.get("attack_tatk", 180))
        attack_start_s = tpre
        attack_stop_s = tpre + tatk

    return {
        "t_seconds": t_seconds,
        "latency_ms": latency_smoothed,
        "throughput_kbps": throughput_kbps,
        "finality_pct": finality_pct,
        "attack_start_s": attack_start_s,
        "attack_stop_s": attack_stop_s,
        "duration_s": total_seconds,
    }


def _rolling_mean(data: np.ndarray, window: int) -> np.ndarray:
    """Compute a centered rolling mean with edge handling."""
    if window <= 1:
        return data.copy()
    kernel = np.ones(window) / window
    # Use 'same' mode for centered window; pad edges with nearest values.
    padded = np.pad(data, (window // 2, window // 2), mode="edge")
    smoothed = np.convolve(padded, kernel, mode="valid")
    return smoothed[: len(data)]


def _empty_series() -> Dict[str, Any]:
    return {
        "t_seconds": np.array([]),
        "latency_ms": np.array([]),
        "throughput_kbps": np.array([]),
        "finality_pct": np.array([]),
        "attack_start_s": None,
        "attack_stop_s": None,
        "duration_s": 0,
    }


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

# Curated color palette for up to 6 overlaid lines.
LINE_COLORS = [
    "#E53935",  # Red
    "#1E88E5",  # Blue
    "#43A047",  # Green
    "#8E24AA",  # Purple
    "#FB8C00",  # Orange
    "#00897B",  # Teal
]

LINE_STYLES = ["-", "--", "-.", ":", "-", "--"]
LINE_MARKERS = ["o", "s", "^", "D", "v", "P"]


def plot_attack_impact(
    series_list: List[Dict[str, Any]],
    labels: List[str],
    output_dir: Path,
    title: str = "Impact of RF Jamming Attack (loss=80%)\non Offline Payment Performance",
    window_size: int = 10,
) -> Path:
    """Draw a 3-panel stacked figure and save to output_dir."""

    fig, axes = plt.subplots(
        3, 1,
        figsize=(12, 10),
        sharex=True,
        gridspec_kw={"hspace": 0.12},
        constrained_layout=True,
    )

    ax_latency: plt.Axes = axes[0]
    ax_throughput: plt.Axes = axes[1]
    ax_finality: plt.Axes = axes[2]

    # ---- Draw attack window from the first series that has one ----
    attack_start = None
    attack_stop = None
    for series in series_list:
        if series["attack_start_s"] is not None:
            attack_start = series["attack_start_s"]
            attack_stop = series["attack_stop_s"]
            break

    if attack_start is not None and attack_stop is not None:
        for ax in axes:
            ax.axvspan(
                attack_start, attack_stop,
                color="#BDBDBD", alpha=0.35,
                label=None,
            )
            ax.axvline(attack_start, color="#757575", linestyle="--", linewidth=0.8, alpha=0.6)
            ax.axvline(attack_stop, color="#757575", linestyle="--", linewidth=0.8, alpha=0.6)

        # Label the attack window at the top of the first subplot.
        mid = (attack_start + attack_stop) / 2.0
        ax_latency.text(
            mid, 0.95,
            f"Attack ({int(attack_stop - attack_start)}s)",
            transform=ax_latency.get_xaxis_transform(),
            ha="center", va="top",
            fontsize=10, fontweight="bold",
            color="#424242",
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#9E9E9E", alpha=0.85),
        )

    # ---- Plot each series ----
    for idx, (series, label) in enumerate(zip(series_list, labels)):
        color = LINE_COLORS[idx % len(LINE_COLORS)]
        style = LINE_STYLES[idx % len(LINE_STYLES)]
        t = series["t_seconds"]

        if len(t) == 0:
            continue

        # Downsample markers to avoid clutter.
        marker = LINE_MARKERS[idx % len(LINE_MARKERS)]
        markevery = max(1, len(t) // 20)

        ax_latency.plot(
            t, series["latency_ms"],
            color=color, linestyle=style, linewidth=1.8,
            marker=marker, markersize=4, markevery=markevery,
            label=label, alpha=0.9,
        )

        ax_throughput.plot(
            t, series["throughput_kbps"],
            color=color, linestyle=style, linewidth=1.8,
            marker=marker, markersize=4, markevery=markevery,
            label=label, alpha=0.9,
        )

        ax_finality.plot(
            t, series["finality_pct"],
            color=color, linestyle=style, linewidth=1.8,
            marker=marker, markersize=4, markevery=markevery,
            label=label, alpha=0.9,
        )

    # ---- Axis formatting ----
    ax_latency.set_ylabel("Latency (s)", fontsize=11, fontweight="bold")
    ax_throughput.set_ylabel("Network Rate\n(TX+RX B/s)", fontsize=11, fontweight="bold")
    ax_finality.set_ylabel("Finality Rate (%)", fontsize=11, fontweight="bold")
    ax_finality.set_xlabel("Time (s)", fontsize=11, fontweight="bold")

    ax_finality.set_ylim(-5, 105)

    for ax in axes:
        ax.grid(True, alpha=0.3, linestyle="-", linewidth=0.5)
        ax.tick_params(labelsize=10)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    # Legend in the top subplot.
    handles, legend_labels = ax_latency.get_legend_handles_labels()
    if handles:
        # Add a dummy entry for the attack window if present.
        if attack_start is not None:
            from matplotlib.patches import Patch
            attack_patch = Patch(
                facecolor="#BDBDBD", edgecolor="#757575",
                alpha=0.5, label="Attack Window",
            )
            handles.append(attack_patch)
            legend_labels.append("Attack Window")

        ax_latency.legend(
            handles, legend_labels,
            loc="upper right",
            fontsize=9,
            framealpha=0.9,
            edgecolor="#BDBDBD",
        )

    fig.suptitle(title, fontsize=13, fontweight="bold")

    # ---- Save ----
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / "attack_impact.pdf"
    png_path = output_dir / "attack_impact.png"

    fig.savefig(str(pdf_path), dpi=150, bbox_inches="tight")
    fig.savefig(str(png_path), dpi=150, bbox_inches="tight")
    plt.close(fig)

    print(f"Saved: {pdf_path}")
    print(f"Saved: {png_path}")

    # ---- Save 3 separate figures ----
    metrics = [
        ("latency_ms", "Latency (s)", "latency"),
        ("throughput_kbps", "Network Rate (TX+RX B/s)", "throughput"),
        ("finality_pct", "Finality Rate (%)", "finality"),
    ]

    for key, ylabel, name in metrics:
        fig_single, ax_single = plt.subplots(figsize=(10, 4), constrained_layout=True)
        
        if attack_start is not None and attack_stop is not None:
            ax_single.axvspan(
                attack_start, attack_stop,
                color="#BDBDBD", alpha=0.35,
                label=None,
            )
            ax_single.axvline(attack_start, color="#757575", linestyle="--", linewidth=0.8, alpha=0.6)
            ax_single.axvline(attack_stop, color="#757575", linestyle="--", linewidth=0.8, alpha=0.6)

            mid = (attack_start + attack_stop) / 2.0
            ax_single.text(
                mid, 0.95,
                f"Attack ({int(attack_stop - attack_start)}s)",
                transform=ax_single.get_xaxis_transform(),
                ha="center", va="top",
                fontsize=10, fontweight="bold",
                color="#424242",
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#9E9E9E", alpha=0.85),
            )

        for idx, (series, label) in enumerate(zip(series_list, labels)):
            color = LINE_COLORS[idx % len(LINE_COLORS)]
            style = LINE_STYLES[idx % len(LINE_STYLES)]
            t = series["t_seconds"]
            if len(t) == 0: continue
            marker = LINE_MARKERS[idx % len(LINE_MARKERS)]
            markevery = max(1, len(t) // 20)

            ax_single.plot(
                t, series[key],
                color=color, linestyle=style, linewidth=1.8,
                marker=marker, markersize=4, markevery=markevery,
                label=label, alpha=0.9,
            )

        ax_single.set_ylabel(ylabel, fontsize=11, fontweight="bold")
        ax_single.set_xlabel("Time (s)", fontsize=11, fontweight="bold")
        if name == "finality":
            ax_single.set_ylim(-5, 105)

        ax_single.grid(True, alpha=0.3, linestyle="-", linewidth=0.5)
        ax_single.tick_params(labelsize=10)
        ax_single.spines["top"].set_visible(False)
        ax_single.spines["right"].set_visible(False)

        handles, legend_labels = ax_single.get_legend_handles_labels()
        if handles:
            if attack_start is not None:
                from matplotlib.patches import Patch
                attack_patch = Patch(
                    facecolor="#BDBDBD", edgecolor="#757575",
                    alpha=0.5, label="Attack Window",
                )
                handles.append(attack_patch)
                legend_labels.append("Attack Window")
            ax_single.legend(handles, legend_labels, loc="upper right", fontsize=9, framealpha=0.9, edgecolor="#BDBDBD")

        fig_single.suptitle(f"{title} - {name.capitalize()}", fontsize=13, fontweight="bold")
        
        single_pdf = output_dir / f"{name}_impact.pdf"
        single_png = output_dir / f"{name}_impact.png"
        fig_single.savefig(str(single_pdf), dpi=150, bbox_inches="tight")
        fig_single.savefig(str(single_png), dpi=150, bbox_inches="tight")
        plt.close(fig_single)
        
        print(f"Saved: {single_pdf}")
        print(f"Saved: {single_png}")

    return png_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot attack-impact time-series from MeshPay benchmark logs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "dirs",
        nargs="*",
        help="Benchmark output directories to plot.",
    )

    parser.add_argument(
        "--label",
        action="append",
        default=[],
        dest="labels",
        help=(
            "Label for the next positional directory. "
            "Use once per directory, in order. "
            "Example: --label '100 tx' dir1 --label '500 tx' dir2"
        ),
    )

    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Output directory for figures. Defaults to the first input dir.",
    )

    parser.add_argument(
        "--window",
        type=int,
        default=10,
        help="Rolling-window size in seconds for latency smoothing (default: 10).",
    )

    parser.add_argument(
        "--title",
        default=None,
        help="Figure title. Auto-generated if not provided.",
    )

    return parser.parse_args()


def resolve_dirs_and_labels(
    raw_args: list[str],
    raw_labels: list[str],
) -> Tuple[List[Path], List[str]]:
    """Match --label flags to positional directories.

    argparse collects all --label values and all positional dirs separately.
    We pair them: label[i] goes with dir[i]. Missing labels get auto-names.
    """
    dirs = [Path(d).resolve() for d in raw_args]
    labels = list(raw_labels)

    # Pad labels if fewer than dirs.
    while len(labels) < len(dirs):
        labels.append(dirs[len(labels)].name)

    return dirs, labels


def main() -> int:
    args = parse_args()

    if not args.dirs:
        print("Error: provide at least one benchmark directory.", file=sys.stderr)
        return 1

    dirs, labels = resolve_dirs_and_labels(args.dirs, args.labels)

    series_list: List[Dict[str, Any]] = []

    for log_dir, label in zip(dirs, labels):
        if not log_dir.exists():
            print(f"Warning: {log_dir} does not exist, skipping.", file=sys.stderr)
            continue

        payment_log = log_dir / "payment.log"
        if not payment_log.exists():
            print(f"Warning: {payment_log} not found, skipping.", file=sys.stderr)
            continue

        events = load_jsonl(payment_log)
        config = load_config(log_dir)

        series = compute_time_series(events, config, window_size=args.window)
        series_list.append(series)
        print(f"Loaded {label}: {len(events)} events, {series['duration_s']}s duration")

    if not series_list:
        print("Error: no valid benchmark data found.", file=sys.stderr)
        return 1

    output_dir = Path(args.output).resolve() if args.output else dirs[0]

    title = args.title or (
        "Impact of RF Jamming Attack (loss=80%)\n"
        "on Offline Payment Performance"
    )

    plot_attack_impact(
        series_list=series_list,
        labels=labels,
        output_dir=output_dir,
        title=title,
        window_size=args.window,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
