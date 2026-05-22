"""Deterministic scenario placement and campaign mobility profiles."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Tuple


Position = Tuple[float, float, float]


@dataclass(frozen=True)
class MobilityProfile:
    """Client mobility speed bounds used by a campaign scenario."""

    min_v: int
    max_v: int

    @property
    def label(self) -> str:
        return f"{self.min_v}-{self.max_v}"


PLACEMENT_SCENARIOS = ("uniform", "clustered", "corridor", "edge_authorities")


def _jitter(rng: random.Random, amount: float) -> float:
    return rng.uniform(-amount, amount)


def deterministic_positions(
    count: int,
    *,
    layout: str,
    seed: int,
    role: str,
    max_x: int = 200,
    max_y: int = 150,
) -> List[Position]:
    """Return stable station positions for a role/layout pair."""

    rng = random.Random(f"{seed}:{role}:{layout}:{count}:{max_x}:{max_y}")
    if count <= 0:
        return []

    layout = layout or "uniform"
    positions: List[Position] = []

    if layout == "clustered":
        centers = [(max_x * 0.32, max_y * 0.42), (max_x * 0.68, max_y * 0.58)]
        for index in range(count):
            cx, cy = centers[index % len(centers)]
            positions.append((max(5.0, min(max_x - 5.0, cx + _jitter(rng, 18))), max(5.0, min(max_y - 5.0, cy + _jitter(rng, 14))), 0.0))
        return positions

    if layout == "corridor":
        y = max_y * 0.5
        step = max_x / float(count + 1)
        for index in range(count):
            positions.append(((index + 1) * step, max(5.0, min(max_y - 5.0, y + _jitter(rng, 10))), 0.0))
        return positions

    if layout == "edge_authorities" and role == "authority":
        anchors = [(12.0, 12.0), (max_x - 12.0, 12.0), (12.0, max_y - 12.0), (max_x - 12.0, max_y - 12.0)]
        for index in range(count):
            ax, ay = anchors[index % len(anchors)]
            positions.append((max(5.0, min(max_x - 5.0, ax + _jitter(rng, 6))), max(5.0, min(max_y - 5.0, ay + _jitter(rng, 6))), 0.0))
        return positions

    if layout == "edge_authorities" and role == "client":
        layout = "uniform"

    cols = max(1, int(count ** 0.5))
    rows = (count + cols - 1) // cols
    for index in range(count):
        col = index % cols
        row = index // cols
        x = (col + 1) * max_x / float(cols + 1)
        y = (row + 1) * max_y / float(rows + 1)
        positions.append((max(5.0, min(max_x - 5.0, x + _jitter(rng, 8))), max(5.0, min(max_y - 5.0, y + _jitter(rng, 8))), 0.0))
    return positions


def mininet_position(position: Position) -> str:
    """Format a position tuple for Mininet-WiFi."""

    return f"{position[0]:.2f},{position[1]:.2f},{position[2]:.2f}"
