#!/usr/bin/env python3

"""Plot attack-impact time-series from MeshPay benchmark logs.

Reads payment.log (JSONL) and benchmark_config.json from one or more
benchmark output directories, computes per-second time-series for three
key metrics, and produces a publication-quality 3-panel stacked figure.

Metrics:
    1. Time to quorum (s)         — all payment_created orders, censored at observation end
    2. Network Throughput (KB/s)  — DTN exchange bytes when available
    3. Quorum Finality Rate (%)   — cumulative confirmed / created

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
from typing import Any, Dict, Iterable, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for headless environments.

import matplotlib.pyplot as plt
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
    benchmark_path = log_dir / "benchmark.json"

    if (log_dir / "benchmark_config.json").exists():
        with (log_dir / "benchmark_config.json").open("r", encoding="utf-8") as f:
            config = json.load(f)
        if benchmark_path.exists():
            with benchmark_path.open("r", encoding="utf-8") as f:
                benchmark = json.load(f)
            timing = benchmark.get("timing", {})
            if isinstance(config, dict) and isinstance(timing, dict):
                config = dict(config)
                config["_benchmark_ended_at"] = timing.get("ended_at")
        return config

    if benchmark_path.exists():
        with benchmark_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            config = data.get("config", data)
            timing = data.get("timing", {})
            if isinstance(config, dict) and isinstance(timing, dict):
                config = dict(config)
                config["_benchmark_ended_at"] = timing.get("ended_at")
            return config

    return {}


# ---------------------------------------------------------------------------
# Time-series computation
# ---------------------------------------------------------------------------

def compute_time_series(
    events: List[Dict[str, Any]],
    config: Dict[str, Any],
    window_size: int = 10,
    log_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compute quorum-only attack-impact time-series from payment.log."""

    created_events = [e for e in events if e.get("event") == "payment_created"]
    if not created_events:
        return _empty_series()

    t0 = min(float(e["time"]) for e in created_events)
    cfg_duration = float(config.get("duration", 0))
    last_event_time = max(float(e["time"]) for e in events)
    total_seconds = int(math.ceil(max(cfg_duration, last_event_time - t0))) + 1

    created_by_order: Dict[str, float] = {}
    confirmed_by_order: Dict[str, float] = {}

    for e in events:
        if e.get("event") == "payment_created" and "order_id" in e:
            created_by_order[e["order_id"]] = float(e["time"])
        elif e.get("event") == "confirmation_created" and "order_id" in e:
            order_id = e["order_id"]
            confirmed_at = float(e["time"])
            previous = confirmed_by_order.get(order_id)
            if previous is None or confirmed_at < previous:
                confirmed_by_order[order_id] = confirmed_at

    quorum_latency = _latency_series(
        completed_by_order=confirmed_by_order,
        created_by_order=created_by_order,
        t0=t0,
        total_seconds=total_seconds,
        window_size=window_size,
    )
    finality_quorum_pct = _cumulative_finality(
        created_by_order=created_by_order,
        completed_by_order=confirmed_by_order,
        t0=t0,
        total_seconds=total_seconds,
    )

    throughput_kbps, throughput_source = _router_exchange_rate_kbps(
        log_dir=log_dir,
        config=config,
        t0=t0,
        total_seconds=total_seconds,
    )
    if throughput_kbps is None:
        throughput_kbps = _application_activity_rate_kbps(
            events=events,
            t0=t0,
            total_seconds=total_seconds,
        )
        throughput_source = "Application activity"

    attack_start_s = None
    attack_stop_s = None
    for e in events:
        if e.get("event") == "attack_started":
            attack_start_s = float(e["time"]) - t0
        elif e.get("event") == "attack_stopped":
            attack_stop_s = float(e["time"]) - t0

    if attack_start_s is None and config.get("attack", "none") != "none":
        tpre = float(config.get("attack_tpre", 60))
        tatk = float(config.get("attack_tatk", 180))
        attack_start_s = tpre
        attack_stop_s = tpre + tatk

    return {
        "t_seconds": np.arange(total_seconds, dtype=float),
        "latency_quorum_s": quorum_latency,
        "latency_ms": quorum_latency,
        "throughput_kbps": throughput_kbps,
        "throughput_source": throughput_source,
        "finality_quorum_pct": finality_quorum_pct,
        "finality_pct": finality_quorum_pct,
        "attack_start_s": attack_start_s,
        "attack_stop_s": attack_stop_s,
        "duration_s": total_seconds,
    }


def _latency_series(
    completed_by_order: Dict[str, float],
    created_by_order: Dict[str, float],
    t0: float,
    total_seconds: int,
    window_size: int,
) -> np.ndarray:
    latency_bins: Dict[int, List[float]] = defaultdict(list)

    observation_end = t0 + max(total_seconds - 1, 0)

    for order_id, created_at in created_by_order.items():
        completed_at = completed_by_order.get(order_id, observation_end)
        second_bin = int(created_at - t0)
        if 0 <= second_bin < total_seconds:
            latency_bins[second_bin].append(max(0.0, completed_at - created_at))

    raw_latency = np.full(total_seconds, np.nan, dtype=float)
    for second, values in latency_bins.items():
        if values:
            raw_latency[second] = float(np.mean(values))

    return _rolling_mean_ignore_nan(raw_latency, window_size)


def _cumulative_finality(
    created_by_order: Dict[str, float],
    completed_by_order: Dict[str, float],
    t0: float,
    total_seconds: int,
) -> np.ndarray:
    created_times = sorted(created_at - t0 for created_at in created_by_order.values())
    completed_times = sorted(
        completed_at - t0
        for order_id, completed_at in completed_by_order.items()
        if order_id in created_by_order
    )

    result = np.zeros(total_seconds, dtype=float)
    created_idx = 0
    completed_idx = 0

    for second in range(total_seconds):
        while created_idx < len(created_times) and created_times[created_idx] <= second:
            created_idx += 1

        while completed_idx < len(completed_times) and completed_times[completed_idx] <= second:
            completed_idx += 1

        if created_idx > 0:
            result[second] = (completed_idx / created_idx) * 100.0
        elif second > 0:
            result[second] = result[second - 1]

    return np.clip(result, 0.0, 100.0)

def _router_exchange_rate_kbps(
    log_dir: Optional[Path],
    config: Dict[str, Any],
    t0: float,
    total_seconds: int,
) -> Tuple[Optional[np.ndarray], str]:
    if log_dir is None:
        return None, ""

    routing = str(config.get("routing", "epidemic"))
    stores_dir = log_dir / "stores" / routing
    if not stores_dir.exists():
        return None, ""

    throughput_bytes = np.zeros(total_seconds, dtype=float)
    found_byte_events = False

    for events_path in stores_dir.glob("*/events.jsonl"):
        for event in load_jsonl(events_path):
            if event.get("event") != "exchange":
                continue
            sent = event.get("sent_bytes")
            received = event.get("received_bytes")
            if sent is None and received is None:
                continue
            found_byte_events = True
            second_bin = int(float(event.get("time", 0.0)) - t0)
            if 0 <= second_bin < total_seconds:
                throughput_bytes[second_bin] += int(sent or 0) + int(received or 0)

    if not found_byte_events:
        return None, ""

    return throughput_bytes / 1024.0, "DTN exchange"


def _application_activity_rate_kbps(
    events: List[Dict[str, Any]],
    t0: float,
    total_seconds: int,
) -> np.ndarray:
    throughput_bytes = np.zeros(total_seconds, dtype=float)
    for e in events:
        ev = e.get("event")
        if ev not in ("payload_injected", "payment_payload_delivered"):
            continue
        second_bin = int(float(e["time"]) - t0)
        if 0 <= second_bin < total_seconds:
            throughput_bytes[second_bin] += int(e.get("payload_size_bytes", 0))
    return throughput_bytes / 1024.0


def _rolling_mean_ignore_nan(data: np.ndarray, window: int) -> np.ndarray:
    window = max(int(window), 1)
    valid = ~np.isnan(data)
    values = np.where(valid, data, 0.0)
    kernel = np.ones(window, dtype=float)
    sums = np.convolve(values, kernel, mode="same")
    counts = np.convolve(valid.astype(float), kernel, mode="same")

    result = np.full(len(data), np.nan, dtype=float)
    np.divide(sums, counts, out=result, where=counts > 0)
    return _fill_missing(result, fill=0.0)


def _fill_missing(data: np.ndarray, fill: float = 0.0) -> np.ndarray:
    result = data.copy()
    last = fill
    for i, value in enumerate(result):
        if np.isnan(value):
            result[i] = last
        else:
            last = float(value)
    return result


def _empty_series() -> Dict[str, Any]:
    empty = np.array([])
    return {
        "t_seconds": empty,
        "latency_quorum_s": empty,
        "latency_ms": empty,
        "throughput_kbps": empty,
        "throughput_source": "",
        "finality_quorum_pct": empty,
        "finality_pct": empty,
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
    filename_prefix: str = "attack_impact",
) -> Path:
    """Draw a quorum-only 3-panel stacked figure and save to output_dir."""

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

    attack_start, attack_stop = _series_attack_window(series_list)
    _draw_attack_window(axes, attack_start, attack_stop, ax_latency)

    for idx, (series, label) in enumerate(zip(series_list, labels)):
        color = LINE_COLORS[idx % len(LINE_COLORS)]
        style = LINE_STYLES[idx % len(LINE_STYLES)]
        marker = LINE_MARKERS[idx % len(LINE_MARKERS)]
        t = series["t_seconds"]
        if len(t) == 0:
            continue

        markevery = max(1, len(t) // 20)
        line_args = {
            "color": color,
            "linestyle": style,
            "linewidth": 1.8,
            "marker": marker,
            "markersize": 4,
            "markevery": markevery,
            "label": label,
            "alpha": 0.9,
        }

        ax_latency.plot(t, series["latency_quorum_s"], **line_args)
        ax_throughput.plot(t, series["throughput_kbps"], **line_args)
        ax_finality.plot(t, series["finality_quorum_pct"], **line_args)

    throughput_sources = {s.get("throughput_source", "") for s in series_list if s.get("throughput_source")}
    throughput_label = "Network Rate\n(TX+RX KB/s)"
    if len(throughput_sources) == 1:
        source = next(iter(throughput_sources))
        if source:
            throughput_label = f"Network Rate\n({source} KB/s)"

    ax_latency.set_ylabel(f"Time to Quorum", fontsize=11, fontweight="bold")
    ax_throughput.set_ylabel(throughput_label, fontsize=11, fontweight="bold")
    ax_finality.set_ylabel("Cumulative Finality (%)", fontsize=11, fontweight="bold")
    ax_finality.set_xlabel("Time (s)", fontsize=11, fontweight="bold")
    ax_finality.set_ylim(-5, 105)

    for ax in axes:
        ax.grid(True, alpha=0.3, linestyle="-", linewidth=0.5)
        ax.tick_params(labelsize=10)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    _add_legend(ax_latency, attack_start is not None)
    fig.suptitle(title, fontsize=13, fontweight="bold")

    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{filename_prefix}.pdf"
    png_path = output_dir / f"{filename_prefix}.png"

    fig.savefig(str(pdf_path), dpi=150, bbox_inches="tight")
    fig.savefig(str(png_path), dpi=150, bbox_inches="tight")
    plt.close(fig)

    print(f"Saved: {pdf_path}")
    print(f"Saved: {png_path}")

    single_metrics = [
        ("latency_quorum_s", f"Time to Quorum", "latency"),
        ("throughput_kbps", throughput_label.replace("\n", " "), "throughput"),
        ("finality_quorum_pct", "Cumulative Finality (%)", "finality"),
    ]

    for key, ylabel, name in single_metrics:
        fig_single, ax_single = plt.subplots(figsize=(10, 4), constrained_layout=True)
        _draw_attack_window([ax_single], attack_start, attack_stop, ax_single)
        _plot_single_metric(ax_single, series_list, labels, key)

        ax_single.set_ylabel(ylabel, fontsize=11, fontweight="bold")
        ax_single.set_xlabel("Time (s)", fontsize=11, fontweight="bold")
        if name == "finality":
            ax_single.set_ylim(-5, 105)

        ax_single.grid(True, alpha=0.3, linestyle="-", linewidth=0.5)
        ax_single.tick_params(labelsize=10)
        ax_single.spines["top"].set_visible(False)
        ax_single.spines["right"].set_visible(False)
        _add_legend(ax_single, attack_start is not None)

        fig_single.suptitle(f"{title} - {name.capitalize()}", fontsize=13, fontweight="bold")

        single_pdf = output_dir / f"{filename_prefix}_{name}.pdf"
        single_png = output_dir / f"{filename_prefix}_{name}.png"
        fig_single.savefig(str(single_pdf), dpi=150, bbox_inches="tight")
        fig_single.savefig(str(single_png), dpi=150, bbox_inches="tight")
        plt.close(fig_single)

        print(f"Saved: {single_pdf}")
        print(f"Saved: {single_png}")

    return png_path


def _series_attack_window(series_list: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    for series in series_list:
        if series["attack_start_s"] is not None:
            return series["attack_start_s"], series["attack_stop_s"]
    return None, None


def _draw_attack_window(
    axes: Iterable[plt.Axes],
    attack_start: Optional[float],
    attack_stop: Optional[float],
    label_axis: plt.Axes,
) -> None:
    if attack_start is None or attack_stop is None:
        return

    for ax in axes:
        ax.axvspan(attack_start, attack_stop, color="#BDBDBD", alpha=0.35, label=None)
        ax.axvline(attack_start, color="#757575", linestyle="--", linewidth=0.8, alpha=0.6)
        ax.axvline(attack_stop, color="#757575", linestyle="--", linewidth=0.8, alpha=0.6)

    mid = (attack_start + attack_stop) / 2.0
    label_axis.text(
        mid, 0.95,
        f"Attack ({int(attack_stop - attack_start)}s)",
        transform=label_axis.get_xaxis_transform(),
        ha="center", va="top",
        fontsize=10, fontweight="bold",
        color="#424242",
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#9E9E9E", alpha=0.85),
    )


def _plot_single_metric(
    ax: plt.Axes,
    series_list: List[Dict[str, Any]],
    labels: List[str],
    key: str,
) -> None:
    for idx, (series, label) in enumerate(zip(series_list, labels)):
        color = LINE_COLORS[idx % len(LINE_COLORS)]
        style = LINE_STYLES[idx % len(LINE_STYLES)]
        marker = LINE_MARKERS[idx % len(LINE_MARKERS)]
        t = series["t_seconds"]
        if len(t) == 0:
            continue

        ax.plot(
            t, series[key],
            color=color, linestyle=style, linewidth=1.8,
            marker=marker, markersize=4, markevery=max(1, len(t) // 20),
            label=label, alpha=0.9,
        )


def _add_legend(ax: plt.Axes, include_attack: bool) -> None:
    handles, legend_labels = ax.get_legend_handles_labels()
    if not handles:
        return

    if include_attack:
        from matplotlib.patches import Patch
        handles.append(Patch(facecolor="#BDBDBD", edgecolor="#757575", alpha=0.5, label="Attack Window"))
        legend_labels.append("Attack Window")

    ax.legend(
        handles, legend_labels,
        loc="upper right",
        fontsize=9,
        framealpha=0.9,
        edgecolor="#BDBDBD",
    )


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
        help="Smoothing window in seconds for time-to-quorum latency only (default: 10).",
    )

    parser.add_argument(
        "--title",
        default=None,
        help="Figure title. Auto-generated if not provided.",
    )

    if hasattr(parser, "parse_intermixed_args"):
        return parser.parse_intermixed_args()
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


def plot_latency_vs_loss(runs_data: List[Dict[str, Any]], output_dir: Path) -> None:
    # Group runs by (routing, payment_rate)
    groups = defaultdict(list)
    for run in runs_data:
        cfg = run["config"]
        routing = cfg.get("routing", "unknown")
        rate = cfg.get("payment_rate", 0.0)
        loss = cfg.get("attack_loss_probability", 0.0)
        
        events = run["events"]
        created = {e["order_id"]: float(e["time"]) for e in events if e.get("event") == "payment_created"}
        confirmed = {}
        for e in events:
            if e.get("event") == "confirmation_created" and "order_id" in e:
                oid = e["order_id"]
                t = float(e["time"])
                if oid not in confirmed or t < confirmed[oid]:
                    confirmed[oid] = t
                    
        if created:
            t0 = min(created.values())
            cfg_duration = float(cfg.get("duration", 0.0) or 0.0)
            cfg_ended_at = cfg.get("_benchmark_ended_at")
            if cfg_ended_at is not None:
                observation_end = float(cfg_ended_at)
            else:
                last_event_time = max(float(e.get("time", t0)) for e in events)
                observation_end = max(t0 + cfg_duration, last_event_time)
            latencies = [
                max(0.0, confirmed.get(oid, observation_end) - created_at)
                for oid, created_at in created.items()
            ]
        else:
            latencies = []
        avg_latency = np.mean(latencies) if latencies else np.nan
        
        groups[(routing, rate)].append((loss, avg_latency))
        
    fig, ax = plt.subplots(figsize=(10, 6), constrained_layout=True)
    
    # Sort groups to ensure consistent plotting order
    sorted_groups = sorted(groups.items(), key=lambda x: (x[0][0], x[0][1]))
    
    for idx, ((routing, rate), points) in enumerate(sorted_groups):
        # Sort points by loss probability
        points = sorted(points, key=lambda x: x[0])
        losses = [p[0] for p in points]
        latencies = [p[1] for p in points]
        
        # Format label
        lbl_route = routing.replace("-", " ").title()
        if routing == "spray-and-wait":
            lbl_route = "Spray-and-Wait"
        elif routing == "prophet":
            lbl_route = "PRoPHET"
            
        label = f"{lbl_route} ({int(rate)} TPS)"
        
        # Color mapping
        if "epi" in routing.lower():
            color = "#1E88E5" # Blue
        elif "snw" in routing.lower() or "spray" in routing.lower():
            color = "#FB8C00" # Orange
        elif "prophet" in routing.lower():
            color = "#E53935" # Red
        else:
            color = LINE_COLORS[idx % len(LINE_COLORS)]
            
        linestyle = "--" if rate > 10.0 else "-"
        marker = "s" if rate > 10.0 else "o"
        
        ax.plot(losses, latencies, label=label, color=color, linestyle=linestyle, marker=marker, linewidth=2.0, markersize=6)
        
    ax.set_xlabel("Packet Loss Probability", fontsize=11, fontweight="bold")
    ax.set_ylabel("Time to Quorum", fontsize=11, fontweight="bold")
    ax.set_title("Average Quorum Latency vs. Packet Loss under Attack", fontsize=13, fontweight="bold")
    ax.grid(True, alpha=0.3, linestyle="-", linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", fontsize=10, edgecolor="#BDBDBD", framealpha=0.9)
    
    pdf_path = output_dir / "latency_vs_packet_loss.pdf"
    png_path = output_dir / "latency_vs_packet_loss.png"
    
    fig.savefig(str(pdf_path), dpi=150)
    fig.savefig(str(png_path), dpi=150)
    plt.close(fig)
    print(f"Saved: {pdf_path}")
    print(f"Saved: {png_path}")


def generate_traffic_table(runs_data: List[Dict[str, Any]], output_dir: Path) -> None:
    table_rows = []
    
    for run in runs_data:
        cfg = run["config"]
        events = run["events"]
        run_dir = run["dir"]
        
        routing = cfg.get("routing", "epidemic")
        rate = cfg.get("payment_rate", 0.0)
        loss = cfg.get("attack_loss_probability", 0.0)
        
        tpre = float(cfg.get("attack_tpre", 60.0))
        tatk = float(cfg.get("attack_tatk", 60.0))
        tpost = float(cfg.get("attack_tpost", 240.0))
        
        t0 = None
        created_events = [e for e in events if e.get("event") == "payment_created"]
        if created_events:
            t0 = min(float(e["time"]) for e in created_events)
        else:
            all_times = [float(e["time"]) for e in events if "time" in e]
            t0 = min(all_times) if all_times else 0.0
            
        stores_dir = run_dir / "stores" / routing
        if not stores_dir.exists():
            base_stores = run_dir / "stores"
            if base_stores.exists():
                subdirs = [d for d in base_stores.iterdir() if d.is_dir()]
                if subdirs:
                    stores_dir = subdirs[0]
                    
        tx_bytes_pre = 0.0
        tx_bytes_during = 0.0
        tx_bytes_post = 0.0
        
        rx_bytes_pre = 0.0
        rx_bytes_during = 0.0
        rx_bytes_post = 0.0
        
        num_nodes = 0
        if stores_dir.exists():
            node_dirs = [d for d in stores_dir.iterdir() if d.is_dir()]
            num_nodes = len(node_dirs)
            for node_dir in node_dirs:
                events_path = node_dir / "events.jsonl"
                if events_path.exists():
                    for event in load_jsonl(events_path):
                        if event.get("event") != "exchange":
                            continue
                        sent = event.get("sent_bytes")
                        received = event.get("received_bytes")
                        time_val = float(event.get("time", 0.0))
                        
                        rel_time = time_val - t0
                        if rel_time < tpre:
                            tx_bytes_pre += sent or 0
                            rx_bytes_pre += received or 0
                        elif rel_time < tpre + tatk:
                            tx_bytes_during += sent or 0
                            rx_bytes_during += received or 0
                        else:
                            tx_bytes_post += sent or 0
                            rx_bytes_post += received or 0
                            
        tpre_dur = tpre if tpre > 0 else 1.0
        tatk_dur = tatk if tatk > 0 else 1.0
        tpost_dur = tpost if tpost > 0 else 1.0
        nodes_count = num_nodes if num_nodes > 0 else 1
        
        tx_rate_pre = (tx_bytes_pre / tpre_dur) / nodes_count / 1024.0
        rx_rate_pre = (rx_bytes_pre / tpre_dur) / nodes_count / 1024.0
        
        tx_rate_during = (tx_bytes_during / tatk_dur) / nodes_count / 1024.0
        rx_rate_during = (rx_bytes_during / tatk_dur) / nodes_count / 1024.0
        
        tx_rate_post = (tx_bytes_post / tpost_dur) / nodes_count / 1024.0
        rx_rate_post = (rx_bytes_post / tpost_dur) / nodes_count / 1024.0
        
        # Pretty names
        lbl_route = routing.replace("-", " ").title()
        if routing == "spray-and-wait":
            lbl_route = "Spray-and-Wait"
        elif routing == "prophet":
            lbl_route = "PRoPHET"
            
        table_rows.append({
            "routing": lbl_route,
            "rate": int(rate),
            "loss": loss,
            "pre_tx": tx_rate_pre,
            "pre_rx": rx_rate_pre,
            "dur_tx": tx_rate_during,
            "dur_rx": rx_rate_during,
            "post_tx": tx_rate_post,
            "post_rx": rx_rate_post
        })
        
    # Sort table rows by routing, rate, loss
    table_rows.sort(key=lambda r: (r["routing"], r["rate"], r["loss"]))
    
    # Generate Markdown Table
    md = []
    md.append("# Peer Traffic Rates Before, During, and After Attack")
    md.append("")
    md.append("This table shows the **Average Peer TX and RX Rates (KiB/s)** computed across three distinct phases of each benchmark run:")
    md.append("1. **Before Attack**: Pre-attack baseline phase.")
    md.append("2. **During Attack**: Jamming/attack phase.")
    md.append("3. **After Attack**: Recovery phase after attack stops.")
    md.append("")
    md.append("| Routing Protocol | Workload (TPS) | Loss Probability | Pre-Attack TX / RX (KiB/s) | During-Attack TX / RX (KiB/s) | Post-Attack TX / RX (KiB/s) |")
    md.append("|:---|:---:|:---:|:---:|:---:|:---:|")
    
    for r in table_rows:
        md.append(f"| {r['routing']} | {r['rate']} | {r['loss']:.2f} | {r['pre_tx']:.3f} / {r['pre_rx']:.3f} | {r['dur_tx']:.3f} / {r['dur_rx']:.3f} | {r['post_tx']:.3f} / {r['post_rx']:.3f} |")
        
    md_content = "\n".join(md)
    
    table_path = output_dir / "traffic_rates_table.md"
    with open(table_path, "w", encoding="utf-8") as f:
        f.write(md_content)
        
    print(f"Saved: {table_path}")
    print("\n" + md_content + "\n")


def run_multi_analysis(parent_dir: Path, output_dir: Path) -> int:
    print(f"Running Multi-Run Analysis on: {parent_dir}")
    
    # Scan and load all run directories
    runs_data = []
    for d in sorted(parent_dir.iterdir()):
        if d.is_dir() and (d / "benchmark_config.json").exists() and (d / "payment.log").exists():
            config = load_config(d)
            events = load_jsonl(d / "payment.log")
            if events:
                runs_data.append({
                    "dir": d,
                    "config": config,
                    "events": events
                })
                
    if not runs_data:
        print("Error: No valid run directories found under parent directory.", file=sys.stderr)
        return 1
        
    print(f"Found {len(runs_data)} valid run directories.")
    
    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # --- Generate Figure 1: Stacked Time-Series Comparison ---
    # We want to find the runs with payment_rate = 10.0 and attack_loss_probability = 0.8
    # (or fallback to the maximum loss probability run for each protocol at the lowest rate)
    target_loss = 0.8
    target_rate = 10.0
    
    fig1_runs = []
    fig1_labels = []
    
    for routing in ["epidemic", "spray-and-wait", "prophet"]:
        match = None
        for run in runs_data:
            cfg = run["config"]
            r_val = cfg.get("routing", "")
            rate_val = cfg.get("payment_rate", 0.0)
            loss_val = cfg.get("attack_loss_probability", 0.0)
            if r_val == routing and abs(rate_val - target_rate) < 0.1 and abs(loss_val - target_loss) < 0.1:
                match = run
                break
        
        if not match:
            candidates = [r for r in runs_data if r["config"].get("routing", "") == routing]
            if candidates:
                candidates.sort(key=lambda r: (-float(r["config"].get("attack_loss_probability", 0.0)), float(r["config"].get("payment_rate", 0.0))))
                match = candidates[0]
                
        if match:
            fig1_runs.append(match)
            lbl = routing.replace("-", " ").title()
            if routing == "spray-and-wait":
                lbl = "Spray-and-Wait"
            elif routing == "prophet":
                lbl = "PRoPHET"
            fig1_labels.append(lbl)
            
    if fig1_runs:
        print(f"Generating Figure 1: Time-series comparison under attack for: {[r['dir'].name for r in fig1_runs]}")
        series_list = []
        for run in fig1_runs:
            series = compute_time_series(run["events"], run["config"], window_size=10, log_dir=run["dir"])
            series_list.append(series)
            
        plot_attack_impact(
            series_list=series_list,
            labels=fig1_labels,
            output_dir=output_dir,
            title="Comparison of Routing Protocols under RF Jamming Attack (loss=80%)",
            window_size=10,
            filename_prefix="attack_impact_comparison"
        )
    else:
        print("Warning: Could not find suitable runs for Figure 1 (Time-series comparison under attack).")
        
    # --- Generate Figure 2: Latency vs. Packet Loss ---
    print("Generating Figure 2: Latency vs. Packet Loss curves...")
    plot_latency_vs_loss(runs_data, output_dir)
    
    # --- Generate Table 3: Average Peer Traffic Rates ---
    print("Generating Table 3: Average Peer TX/RX Traffic Rates...")
    generate_traffic_table(runs_data, output_dir)
    
    return 0


def main() -> int:
    args = parse_args()

    if not args.dirs:
        print("Error: provide at least one benchmark directory.", file=sys.stderr)
        return 1

    dirs, labels = resolve_dirs_and_labels(args.dirs, args.labels)

    # Check if we should automatically enter Multi-Run Analysis Mode
    if len(dirs) == 1:
        parent_dir = dirs[0]
        subdirs = sorted(list(parent_dir.glob("*/benchmark_config.json")))
        if subdirs or (parent_dir / "summary.json").exists():
            output_dir = Path(args.output).resolve() if args.output else parent_dir
            return run_multi_analysis(parent_dir, output_dir)

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

        series = compute_time_series(
            events,
            config,
            window_size=args.window,
            log_dir=log_dir,
        )
        series_list.append(series)
        print(
            f"Loaded {label}: {len(events)} events, "
            f"{series['duration_s']}s duration, "
            f"rate_source={series.get('throughput_source', 'unknown')}"
        )

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
