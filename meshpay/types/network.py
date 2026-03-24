"""Network and metrics types for MeshPay."""

from __future__ import annotations

import time
from enum import Enum
from typing import Optional, Tuple
from dataclasses import dataclass

from .common import Address


class BCBPriorityClass(Enum):
    """Traffic priority classes for BCB settlement.

    Used by the SDN controller / QoS manager to classify packets into
    strict-priority queues.
    """

    FASTPAY_BCB = 0     # votes, certificates — highest priority
    PAYMENT_DATA = 1    # transfer payloads, balance queries
    BEST_EFFORT = 2     # logs, telemetry, model updates


@dataclass
class TokenBalance:
    """Token balance information."""
    token_symbol: str
    token_address: str
    wallet_balance: float
    meshpay_balance: float
    total_balance: float
    decimals: int


@dataclass
class NetworkMetrics:
    """Network performance metrics."""

    latency: float
    bandwidth: float
    packet_loss: float
    connectivity_ratio: float
    last_update: float

    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.last_update == 0:
            self.last_update = time.time()


@dataclass
class PeerInfo:
    """Rich neighbor/peer information for DTN mesh networking."""

    address: Address
    last_seen: float = 0.0
    position: Optional[Tuple[float, float, float]] = None
    rssi: Optional[float] = None
    hop_count: int = 0

    def __post_init__(self) -> None:
        if self.last_seen == 0.0:
            self.last_seen = time.time()
