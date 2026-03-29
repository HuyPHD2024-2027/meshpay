from __future__ import annotations

"""Plotting utilities for synthetic metrics.

This module provides figure generation for throughput and latency versus the number of
authorities. Figures are saved to files for downstream reporting.
"""

from pathlib import Path
from typing import Iterable, Tuple

import matplotlib.pyplot as plt
import numpy as np


def save_metric_plot(
    x: np.ndarray,
    y: np.ndarray,
    *,
    xlabel: str,
    ylabel: str,
    title: str,
    output_path: Path,
    annotate_extrema: bool = True,
) -> Path:
    """Save a line plot for a metric and return the path.

    Args:
        x: X-axis values (e.g., number of authorities).
        y: Y-axis values (metric).
        xlabel: Label for x-axis.
        ylabel: Label for y-axis.
        title: Figure title.
        output_path: Destination file path (parent directories will be created).
        annotate_extrema: Whether to annotate the global max and min points.

    Returns:
        The path to the saved figure file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _, ax = plt.subplots(figsize=(8, 4.5), constrained_layout=True)
    ax.plot(x, y, color="#1f77b4", linewidth=2.0)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, linestyle=":", linewidth=0.8, alpha=0.8)

    if annotate_extrema and len(x) > 0:
        max_idx = int(np.argmax(y))
        min_idx = int(np.argmin(y))
        ax.scatter([x[max_idx]], [y[max_idx]], color="green", zorder=5)
        ax.scatter([x[min_idx]], [y[min_idx]], color="red", zorder=5)
        ax.annotate(
            f"max @ {x[max_idx]}: {y[max_idx]:.3f}",
            (x[max_idx], y[max_idx]),
            textcoords="offset points",
            xytext=(10, 10),
            fontsize=9,
            color="green",
        )
        ax.annotate(
            f"min @ {x[min_idx]}: {y[min_idx]:.3f}",
            (x[min_idx], y[min_idx]),
            textcoords="offset points",
            xytext=(10, -15),
            fontsize=9,
            color="red",
        )

    plt.savefig(output_path, dpi=150)
    plt.close()
    return output_path


def save_throughput_and_latency_plots(
    authorities: np.ndarray,
    throughput: np.ndarray,
    latency: np.ndarray,
    *,
    output_dir: Path,
) -> Tuple[Path, Path]:
    """Save both throughput and latency plots and return their paths.

    Args:
        authorities: Number of authorities array.
        throughput: Throughput values (higher is better).
        latency: Latency values (lower is better, normalized to [0, 1]).
        output_dir: Directory to save the figures.

    Returns:
        Pair of paths: (throughput_path, latency_path).
    """
    output_dir = Path(output_dir)
    throughput_path = output_dir / "throughput_vs_authorities.png"
    latency_path = output_dir / "latency_vs_authorities.png"

    save_metric_plot(
        authorities,
        throughput,
        xlabel="Number of authorities",
        ylabel="Throughput (normalized)",
        title="Throughput vs Number of Authorities",
        output_path=throughput_path,
    )

    save_metric_plot(
        authorities,
        latency,
        xlabel="Number of authorities",
        ylabel="Latency (normalized, lower is better)",
        title="Latency vs Number of Authorities",
        output_path=latency_path,
    )

    return throughput_path, latency_path
