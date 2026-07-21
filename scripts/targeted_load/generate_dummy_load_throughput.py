#!/usr/bin/env python3
"""Generate dummy confirmed-throughput data and figures for a load attack.

The generated values are synthetic and are intended for figure drafting only.
They show confirmation throughput in transactions per second: high before the
attack, degraded during the attack, and recovered after the attack.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np


FIGURE_DPI = 150
DEFAULT_OUTPUT_DIR = "figures/dummy_targeted_load_throughput"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=180,
        help="Total time in seconds (default: 180).",
    )
    parser.add_argument(
        "--attack-start",
        type=int,
        default=30,
        help="Attack start time in seconds (default: 30).",
    )
    parser.add_argument(
        "--attack-stop",
        type=int,
        default=90,
        help="Attack stop time in seconds (default: 90).",
    )
    parser.add_argument(
        "--attack-load-rate",
        type=float,
        default=50.0,
        help="Dummy targeted attack load rate for the legend, in tx/s (default: 50).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20,
        help="Random seed for deterministic jitter (default: 20).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="Number of dummy runs to average (default: 5).",
    )
    return parser.parse_args()


def _phase_baseline(t: np.ndarray, attack_start: int, attack_stop: int) -> np.ndarray:
    """Return a smooth tx/s baseline with attack degradation and recovery."""
    throughput = np.zeros_like(t, dtype=float)

    before = t < attack_start
    during = (t >= attack_start) & (t < attack_stop)
    after = t >= attack_stop

    # Warm-up to a stable pre-attack confirmation rate around 9 tx/s.
    throughput[before] = 3.0 + 6.2 * (1.0 - np.exp(-(t[before] + 1.0) / 5.0))

    # Attack starts with a steep drop, then remains capacity-limited.
    attack_t = t[during] - attack_start
    throughput[during] = 2.0 + 1.0 * np.exp(-attack_t / 7.0)

    # After the attack stops, the system drains backlog and returns near normal.
    recover_t = t[after] - attack_stop
    throughput[after] = 2.4 + 6.3 * (1.0 - np.exp(-recover_t / 14.0))

    return throughput


def generate_dummy_runs(
    duration: int,
    attack_start: int,
    attack_stop: int,
    runs: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Generate per-run and mean dummy confirmation throughput."""
    if duration <= 0:
        raise ValueError("duration must be positive")
    if not 0 <= attack_start < attack_stop <= duration:
        raise ValueError("expected 0 <= attack_start < attack_stop <= duration")
    if runs <= 0:
        raise ValueError("runs must be positive")

    rng = np.random.default_rng(seed)
    t = np.arange(duration + 1)
    baseline = _phase_baseline(t, attack_start, attack_stop)
    samples = []

    for run_idx in range(runs):
        noise = rng.normal(0.0, 0.35, size=t.shape)
        slow_wave = 0.25 * np.sin((t + run_idx * 4) / 8.0)
        run = np.clip(baseline + noise + slow_wave, 0.0, None)
        samples.append(run)

    run_values = np.vstack(samples)
    mean = run_values.mean(axis=0)
    std = run_values.std(axis=0)
    ci95 = 1.96 * std / np.sqrt(runs)
    return t, run_values, mean, ci95


def write_csv(path: Path, t: np.ndarray, run_values: np.ndarray, mean: np.ndarray, ci95: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        header = ["time_s", "mean_confirmed_tx_per_s", "ci95_confirmed_tx_per_s"]
        header.extend(f"run_{idx + 1}_confirmed_tx_per_s" for idx in range(run_values.shape[0]))
        writer.writerow(header)
        for i, second in enumerate(t):
            writer.writerow(
                [int(second), f"{mean[i]:.3f}", f"{ci95[i]:.3f}"]
                + [f"{run_values[idx, i]:.3f}" for idx in range(run_values.shape[0])]
            )


def write_json(path: Path, args: argparse.Namespace, t: np.ndarray, mean: np.ndarray, ci95: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": "Synthetic dummy data for figure drafting, not benchmark output.",
        "duration_s": args.duration,
        "attack_start_s": args.attack_start,
        "attack_stop_s": args.attack_stop,
        "attack_load_rate_tx_s": args.attack_load_rate,
        "mean_confirmed_tx_per_s": [
            {
                "time_s": int(second),
                "throughput_tx_s": round(float(value), 3),
                "ci95_tx_s": round(float(ci), 3),
            }
            for second, value, ci in zip(t, mean, ci95)
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def plot_figure(
    output_dir: Path,
    t: np.ndarray,
    mean: np.ndarray,
    ci95: np.ndarray,
    attack_start: int,
    attack_stop: int,
    attack_load_rate: float,
) -> None:
    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)

    color = "#1E88E5"
    ax.grid(True, alpha=0.3, linestyle="-", linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(labelsize=10)

    ax.axvspan(
        attack_start,
        attack_stop,
        color="#FFCDD2",
        alpha=0.28,
        label=f"Targeted Load Attack ({attack_load_rate:.0f} tx/s)",
    )
    ax.axvline(attack_start, color="#D32F2F", linestyle="--", linewidth=1.0, alpha=0.75)
    ax.axvline(attack_stop, color="#D32F2F", linestyle="--", linewidth=1.0, alpha=0.75)

    ax.fill_between(
        t,
        np.maximum(0.0, mean - ci95),
        mean + ci95,
        color=color,
        alpha=0.16,
        label="95% confidence interval",
    )
    ax.plot(
        t,
        mean,
        color=color,
        marker="o",
        markevery=max(1, len(t) // 14),
        linewidth=2.3,
        markersize=5,
        label="Epidemic confirmed throughput (mean)",
    )

    mid_time = (attack_start + attack_stop) / 2.0
    ax.text(
        mid_time,
        0.95,
        f"Attack Active\n({attack_stop - attack_start}s)",
        transform=ax.get_xaxis_transform(),
        ha="center",
        va="top",
        fontsize=9.5,
        fontweight="bold",
        color="#C62828",
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#FFCDD2", alpha=0.9),
    )

    ax.set_xlabel("Time (s)", fontsize=11, fontweight="bold")
    ax.set_ylabel("Throughput (confirmed tx/s)", fontsize=11, fontweight="bold")
    ax.set_title(
        "Confirmed Transaction Throughput Under Targeted Load Attack",
        fontsize=12,
        fontweight="bold",
        pad=15,
    )
    ax.set_xlim(0, int(t[-1]))
    ax.set_ylim(0, max(10.5, float((mean + ci95).max()) + 0.8))
    ax.legend(fontsize=9, edgecolor="#BDBDBD", framealpha=0.9, loc="upper right")

    png_path = output_dir / "dummy_load_confirmed_throughput_over_time.png"
    pdf_path = output_dir / "dummy_load_confirmed_throughput_over_time.pdf"
    fig.savefig(png_path, dpi=FIGURE_DPI, bbox_inches="tight")
    fig.savefig(pdf_path, dpi=FIGURE_DPI, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    t, run_values, mean, ci95 = generate_dummy_runs(
        duration=args.duration,
        attack_start=args.attack_start,
        attack_stop=args.attack_stop,
        runs=args.runs,
        seed=args.seed,
    )

    write_csv(output_dir / "dummy_load_confirmed_throughput.csv", t, run_values, mean, ci95)
    write_json(output_dir / "dummy_load_confirmed_throughput.json", args, t, mean, ci95)
    plot_figure(output_dir, t, mean, ci95, args.attack_start, args.attack_stop, args.attack_load_rate)

    print("Generated dummy confirmed-throughput data and figures:")
    print(f"  CSV: {output_dir / 'dummy_load_confirmed_throughput.csv'}")
    print(f"  JSON: {output_dir / 'dummy_load_confirmed_throughput.json'}")
    print(f"  PNG: {output_dir / 'dummy_load_confirmed_throughput_over_time.png'}")
    print(f"  PDF: {output_dir / 'dummy_load_confirmed_throughput_over_time.pdf'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
