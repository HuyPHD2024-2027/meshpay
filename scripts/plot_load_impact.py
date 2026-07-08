#!/usr/bin/env python3
"""Plot the impact of a targeted load attack on MeshPay throughput over time.

Reads payment.log (JSONL) and benchmark_config.json from one or more benchmark
output directories (or summary.json files) and generates a publication-quality
figure showing the transaction confirmation throughput (tx/s) over time.

Usage:
    python3 scripts/plot_load_impact.py \
        logs/benchmarks/targeted_load_epidemic_seed_2* \
        -o figures/load_impact/
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
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------
ROUTING_COLORS = {
    "epidemic":       "#1E88E5",   # Blue
    "spray-and-wait": "#FB8C00",   # Orange
    "prophet":        "#E53935",   # Red
}
ROUTING_LABELS = {
    "epidemic":       "Epidemic",
    "spray-and-wait": "Spray-and-Wait",
    "prophet":        "PRoPHET",
}
ROUTING_MARKERS = {
    "epidemic":       "o",
    "spray-and-wait": "s",
    "prophet":        "^",
}

FIGURE_DPI = 150

# ---------------------------------------------------------------------------
# Data loading helpers
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

def find_runs(paths: List[str]) -> List[Tuple[Path, Path]]:
    """Find all (payment.log, benchmark_config.json) pairs from input paths."""
    runs = []
    for p_str in paths:
        # Support glob expansion if not done by shell
        import glob
        expanded = glob.glob(p_str) if "*" in p_str or "?" in p_str else [p_str]
        for exp_path in expanded:
            p = Path(exp_path).resolve()
            if not p.exists():
                print(f"Warning: path {p} does not exist.")
                continue
            if p.is_file():
                if p.name == "summary.json":
                    try:
                        with p.open("r", encoding="utf-8") as f:
                            data = json.load(f)
                            if isinstance(data, list):
                                for r in data:
                                    rd = r.get("run_dir")
                                    if rd:
                                        rd_path = Path(rd)
                                        log_path = rd_path / "payment.log"
                                        cfg_path = rd_path / "benchmark_config.json"
                                        if log_path.exists() and cfg_path.exists():
                                            runs.append((log_path, cfg_path))
                    except Exception as e:
                        print(f"Error reading summary {p}: {e}")
            else:
                # It's a directory. Check if it's a direct run folder or contains runs.
                log_path = p / "payment.log"
                cfg_path = p / "benchmark_config.json"
                if log_path.exists() and cfg_path.exists():
                    runs.append((log_path, cfg_path))
                else:
                    for sub_log in p.rglob("payment.log"):
                        sub_cfg = sub_log.parent / "benchmark_config.json"
                        if sub_cfg.exists():
                            runs.append((sub_log, sub_cfg))
    return list(set(runs)) # Deduplicate just in case

def rolling_average(data: np.ndarray, window: int) -> np.ndarray:
    """Compute rolling average with truncated window at boundaries."""
    if window <= 1:
        return data
    result = np.zeros_like(data)
    n = len(data)
    for i in range(n):
        start = max(0, i - window // 2)
        end = min(n, i + window // 2 + 1)
        result[i] = np.mean(data[start:end])
    return result

def process_run(log_path: Path, config: Dict[str, Any], window_size: int) -> Dict[str, Any]:
    """Parse payment.log and compute throughput time-series."""
    events = load_jsonl(log_path)
    if not events:
        return {}

    # Determine t0
    t0 = None
    for e in events:
        if e.get("event") == "attack_configured" and "traffic_started_at" in e:
            t0 = float(e["traffic_started_at"])
            break
    if t0 is None:
        created_times = [float(e["time"]) for e in events if e.get("event") == "payment_created"]
        if created_times:
            t0 = min(created_times)
        else:
            t0 = min(float(e["time"]) for e in events)

    # Attack window
    attack_start_s = None
    attack_stop_s = None
    for e in events:
        if e.get("event") == "attack_started":
            attack_start_s = float(e["time"]) - t0
        elif e.get("event") == "attack_stopped":
            attack_stop_s = float(e["time"]) - t0

    if attack_start_s is None:
        tpre = float(config.get("attack_tpre", 10.0))
        tatk = float(config.get("attack_tatk", 20.0))
        attack_start_s = tpre
        attack_stop_s = tpre + tatk

    # Confirmed times
    confirmed_times = []
    for e in events:
        if e.get("event") == "confirmation_created":
            t_rel = float(e["time"]) - t0
            confirmed_times.append(t_rel)

    max_t = max(float(e["time"]) - t0 for e in events) if events else 0.0
    duration = int(math.ceil(max_t)) + 1

    # Bin confirmations into 1s intervals
    counts = np.zeros(duration)
    for t in confirmed_times:
        idx = int(math.floor(t))
        if 0 <= idx < duration:
            counts[idx] += 1

    smoothed = rolling_average(counts, window_size)

    return {
        "throughput": smoothed,
        "attack_start": attack_start_s,
        "attack_stop": attack_stop_s,
        "payment_rate": float(config.get("payment_rate", 10.0)),
        "attack_load_rate": float(config.get("attack_load_rate", config.get("param.attack_load_rate", 0.0)))
    }

# ---------------------------------------------------------------------------
# CLI & Plotting
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "inputs",
        nargs="+",
        help="Directories or summary.json files from run_meshpay_benchmark_matrix.py.",
    )
    p.add_argument(
        "-o", "--output-dir",
        default="figures/load_impact",
        help="Output directory for the generated figure (default: figures/load_impact).",
    )
    p.add_argument(
        "-w", "--window-size",
        type=int,
        default=5,
        help="Window size in seconds for rolling average smoothing (default: 5).",
    )
    p.add_argument(
        "--title",
        default="MeshPay Throughput Over Time Under Targeted Load Attack",
        help="Plot title",
    )
    return p.parse_args()

def main() -> int:
    args = parse_args()

    # Find all run logs and configs
    runs = find_runs(args.inputs)
    if not runs:
        print("Error: No benchmark runs found in the provided paths.", file=sys.stderr)
        return 1

    print(f"Found {len(runs)} benchmark run(s). Processing logs...")

    # Group runs by routing protocol
    runs_by_routing = defaultdict(list)
    for log_path, cfg_path in runs:
        try:
            with cfg_path.open("r", encoding="utf-8") as f:
                config = json.load(f)
            routing = config.get("routing", "epidemic")
            runs_by_routing[routing].append((log_path, config))
        except Exception as e:
            print(f"Error loading config {cfg_path}: {e}", file=sys.stderr)

    # Process logs and compute time series
    processed_by_routing = defaultdict(list)
    for routing, run_list in runs_by_routing.items():
        for log_path, config in run_list:
            res = process_run(log_path, config, args.window_size)
            if res:
                processed_by_routing[routing].append(res)

    if not any(processed_by_routing.values()):
        print("Error: Could not extract valid time series data from logs.", file=sys.stderr)
        return 1

    # Prepare plotting
    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)

    # Draw grid and layout styles
    ax.grid(True, alpha=0.3, linestyle="-", linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=10)

    # Collect parameters for lines/references
    all_payment_rates = []
    all_load_rates = []
    attack_starts = []
    attack_stops = []

    for routing, results in processed_by_routing.items():
        if not results:
            continue

        # Find maximum duration among runs for this routing
        max_duration = max(len(r["throughput"]) for r in results)
        
        # Pad all throughputs to max_duration
        padded_throughputs = []
        for r in results:
            th = r["throughput"]
            padded = np.zeros(max_duration)
            padded[:len(th)] = th
            padded_throughputs.append(padded)
            
            if r["attack_start"] is not None:
                attack_starts.append(r["attack_start"])
            if r["attack_stop"] is not None:
                attack_stops.append(r["attack_stop"])
            all_payment_rates.append(r["payment_rate"])
            all_load_rates.append(r["attack_load_rate"])

        # Compute average and CI/STD
        padded_throughputs = np.array(padded_throughputs)
        mean_throughput = np.mean(padded_throughputs, axis=0)
        
        if len(results) > 1:
            std_throughput = np.std(padded_throughputs, axis=0)
            ci = 1.96 * std_throughput / np.sqrt(len(results))
        else:
            ci = np.zeros(max_duration)

        t_axis = np.arange(max_duration)
        color = ROUTING_COLORS.get(routing, "#555555")
        marker = ROUTING_MARKERS.get(routing, "o")
        label = f"{ROUTING_LABELS.get(routing, routing.capitalize())} (Mean)"
        if len(results) > 1:
            label += f" [n={len(results)}]"

        # Plot line
        markevery = max(1, max_duration // 15)
        ax.plot(
            t_axis, mean_throughput,
            color=color, marker=marker, markevery=markevery,
            linewidth=2.0, markersize=5, label=label
        )
        
        # Fill variance
        if len(results) > 1:
            ax.fill_between(
                t_axis,
                np.maximum(0.0, mean_throughput - ci),
                mean_throughput + ci,
                color=color, alpha=0.15, label="95% Conf. Interval"
            )

    # Draw attack window highlight if available
    if attack_starts and attack_stops:
        avg_start = np.mean(attack_starts)
        avg_stop = np.mean(attack_stops)
        avg_load_rate = np.mean(all_load_rates) if all_load_rates else 0.0
        
        # Shaded area
        ax.axvspan(
            avg_start, avg_stop,
            color="#FFCDD2", alpha=0.25,
            label=f"Targeted Load Attack ({avg_load_rate:.0f} tx/s)" if avg_load_rate else "Targeted Load Attack"
        )
        
        # Vertical boundary lines
        ax.axvline(avg_start, color="#D32F2F", linestyle="--", linewidth=1.0, alpha=0.7)
        ax.axvline(avg_stop, color="#D32F2F", linestyle="--", linewidth=1.0, alpha=0.7)
        
        # Text label inside/above the attack span
        mid_time = (avg_start + avg_stop) / 2.0
        ax.text(
            mid_time, 0.95,
            f"Attack Active\n({avg_stop - avg_start:.0f}s)",
            transform=ax.get_xaxis_transform(),
            ha="center", va="top",
            fontsize=9.5, fontweight="bold",
            color="#C62828",
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#FFCDD2", alpha=0.9),
        )

    # Reference target payment rate line
    if all_payment_rates:
        avg_payment_rate = np.mean(all_payment_rates)
        ax.axhline(
            y=avg_payment_rate, color="#4CAF50", linestyle=":", linewidth=1.5, alpha=0.8,
            label=f"Baseline Target Rate ({avg_payment_rate:.0f} tx/s)"
        )

    # Label styling
    ax.set_xlabel("Time (s)", fontsize=11, fontweight="bold")
    ax.set_ylabel("Confirmed Throughput (tx/s)", fontsize=11, fontweight="bold")
    ax.set_title(args.title, fontsize=12, fontweight="bold", pad=15)
    
    # Legend settings
    ax.legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9, loc="upper right")
    
    # Save files
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    
    pdf_path = out_dir / "load_impact_throughput_over_time.pdf"
    png_path = out_dir / "load_impact_throughput_over_time.png"
    
    fig.savefig(str(pdf_path), dpi=FIGURE_DPI, bbox_inches="tight")
    fig.savefig(str(png_path), dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(fig)

    print(f"Successfully generated single throughput figure:")
    print(f"  PDF: {pdf_path}")
    print(f"  PNG: {png_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
