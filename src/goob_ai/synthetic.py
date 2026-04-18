from __future__ import annotations

"""Synthetic data generation for throughput and latency vs authorities.

This module provides parametric generators that model:
- Throughput that increases with the number of authorities until a saturation point, then decreases
  due to quorum signature overhead.
- Latency that improves (decreases) up to an optimal authority count then worsens as quorum
  becomes more expensive.

The functions return NumPy arrays for convenient plotting and downstream processing.
"""

from dataclasses import dataclass
from typing import Tuple

import numpy as np


@dataclass(frozen=True)
class MetricParams:
    """Parameters controlling synthetic metric shapes.

    Attributes:
        min_authorities: Smallest authority count to simulate (inclusive).
        max_authorities: Largest authority count to simulate (inclusive).
        step: Step size for authority counts.
        throughput_peak_at: Authority count where throughput is maximal.
        latency_best_at: Authority count where latency is minimal.
        noise_scale: Standard deviation of Gaussian noise added proportionally.
    """

    min_authorities: int = 1
    max_authorities: int = 200
    step: int = 1
    throughput_peak_at: int = 50
    latency_best_at: int = 50
    noise_scale: float = 0.02


def generate_authority_counts(params: MetricParams) -> np.ndarray:
    """Generate authority counts as an integer NumPy array.

    Args:
        params: Parameterization for range and step.

    Returns:
        A 1-D NumPy array of authority counts (dtype=int).
    """
    return np.arange(params.min_authorities, params.max_authorities + 1, params.step, dtype=int)


def synthetic_throughput(authorities: np.ndarray, params: MetricParams) -> np.ndarray:
    """Generate synthetic throughput curve vs authorities.

    The shape is a smooth rise up to `throughput_peak_at` then a decay caused by quorum overhead.
    We model this with a log-growth multiplied by a Gaussian-like decay around the peak.

    Args:
        authorities: Array of authority counts.
        params: Generation parameters.

    Returns:
        Throughput values normalized to approximately [0, 1], with small noise.
    """
    a = authorities.astype(float)
    peak = float(params.throughput_peak_at)

    # Log-like growth term (shifted to avoid log(0)).
    growth = np.log1p(a)
    growth /= growth.max() if growth.max() > 0 else 1.0

    # Symmetric decay around the peak using a Gaussian envelope.
    width = max(10.0, 0.3 * peak)
    decay = np.exp(-((a - peak) ** 2) / (2 * width**2))

    # Combine and normalize.
    curve = growth * (0.6 + 0.4 * decay)
    curve /= curve.max() if curve.max() > 0 else 1.0

    # Add proportional noise.
    rng = np.random.default_rng(1337)
    noise = rng.normal(0.0, params.noise_scale, size=curve.shape)
    curve_noisy = np.clip(curve + noise, 0.0, None)
    return curve_noisy


def synthetic_latency(authorities: np.ndarray, params: MetricParams) -> np.ndarray:
    """Generate synthetic latency curve vs authorities (lower is better).

    The shape improves until `latency_best_at` (decreasing), then increases due to quorum cost.
    We model this as a U-shaped curve centered at the best point, with asymmetry for small-N edge
    cases where finality takes longer.

    Args:
        authorities: Array of authority counts.
        params: Generation parameters.

    Returns:
        Latency values normalized to approximately [0, 1], with small noise, lower is better.
    """
    a = authorities.astype(float)
    best = float(params.latency_best_at)

    # Asymmetric U-shape: quadratic distance from best, with steeper slope on the left side.
    left_slope = 1.3
    right_slope = 1.0
    slope = np.where(a < best, left_slope, right_slope)
    u_shape = ((a - best) / (0.6 * best)) ** 2 * slope

    # Add baseline and invert to make lower near the best.
    baseline = 0.2
    curve = baseline + u_shape

    # Normalize to [0, 1]. Smaller is better; keep direct scale for plotting without inversion.
    curve = (curve - curve.min()) / (curve.max() - curve.min() + 1e-12)

    # Add proportional noise and clip.
    rng = np.random.default_rng(4242)
    noise = rng.normal(0.0, params.noise_scale, size=curve.shape)
    curve_noisy = np.clip(curve + noise, 0.0, 1.0)
    return curve_noisy


def generate_metrics(params: MetricParams) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Generate authority counts and corresponding throughput and latency arrays.

    Args:
        params: Generation parameters.

    Returns:
        Tuple of (authorities, throughput, latency).
    """
    counts = generate_authority_counts(params)
    throughput = synthetic_throughput(counts, params)
    latency = synthetic_latency(counts, params)
    return counts, throughput, latency
