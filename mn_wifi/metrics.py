"""Performance metrics collection module for FastPay simulation."""

import time
from typing import Any, Dict, Optional
from dataclasses import asdict

from meshpay.types import NetworkMetrics


# --------------------------------------------------------------------------------------
# Data classes and helpers
# --------------------------------------------------------------------------------------


class RollingAverage:
    """Utility to keep a rolling average over the last *N* samples.

    The helper is intentionally lightweight – it only stores the running
    *sum* and a circular buffer of values.  When *N* is small (e.g. ≤ 100)
    this is more than efficient enough for the simulation.
    """

    def __init__(self, capacity: int = 20) -> None:  # noqa: D401 – imperative
        self._values: list[float] = []
        self._capacity = capacity
        self._sum = 0.0

    def add(self, value: float) -> None:
        """Insert a new sample and update the running average."""
        self._values.append(value)
        self._sum += value

        if len(self._values) > self._capacity:
            self._sum -= self._values.pop(0)

    @property
    def average(self) -> float:
        """Return the current average (0.0 when no samples)."""
        return self._sum / len(self._values) if self._values else 0.0


# --------------------------------------------------------------------------------------
# Collector class
# --------------------------------------------------------------------------------------


class MetricsCollector:
    """Performance metrics collector for authority node."""
    
    def __init__(self) -> None:
        """Initialize the metrics collector.

        The collector maintains two levels of metrics:

        1. *Global* – aggregated across all links (latency, bandwidth …).
        2. *Per-peer* – individual link quality to each authority or client.
        """

        # Global / aggregated network metrics ------------------------------------
        self.network_metrics = NetworkMetrics(
            latency=0.0,
            bandwidth=0.0,
            packet_loss=0.0,
            connectivity_ratio=0.0,
            last_update=time.time(),
        )

        # Per-peer rolling averages ---------------------------------------------
        self._peer_latency: Dict[str, RollingAverage] = {}
        self._peer_bandwidth: Dict[str, RollingAverage] = {}
        self._peer_connectivity: Dict[str, RollingAverage] = {}

        # Transaction-level counters --------------------------------------------
        self.transaction_count = 0
        self.error_count = 0
        self.sync_count = 0
        self._validation_latency = RollingAverage()
        
        
    def record_transaction(self) -> None:
        """Record a transaction."""
        self.transaction_count += 1
        
    def record_error(self) -> None:
        """Record an error."""
        self.error_count += 1
        
    def record_sync(self) -> None:
        """Record a synchronization."""
        self.sync_count += 1
        
    def record_validation_time(self, latency_ms: float) -> None:
        """Record the time spent validating a transaction/shard."""
        self._validation_latency.add(latency_ms)

    def get_reputation_score(self) -> float:
        """Calculate the node's reputation score (success completion ratio)."""
        tot = self.transaction_count + self.error_count
        return self.transaction_count / tot if tot > 0 else 1.0
        
    def update_network_metrics(self, metrics: NetworkMetrics) -> None:
        """Update network metrics.
        
        Args:
            metrics: New network metrics
        """
        self.network_metrics = metrics
        
    def record_link_metrics(
        self,
        peer: str,
        *,
        latency_ms: Optional[float] = None,
        bandwidth_mbps: Optional[float] = None,
        connectivity_ratio: Optional[float] = None,
    ) -> None:
        """Record raw link metrics for the connection to *peer*.

        Any *None* parameter is ignored so callers may update metrics
        independently (e.g. latency from ICMP echo, bandwidth from iperf …).
        A simple rolling average (last 20 samples by default) is maintained
        internally.  This keeps memory usage bounded while smoothing out
        short-term fluctuations.
        """

        if latency_ms is not None:
            self._peer_latency.setdefault(peer, RollingAverage()).add(latency_ms)

        if bandwidth_mbps is not None:
            self._peer_bandwidth.setdefault(peer, RollingAverage()).add(bandwidth_mbps)

        if connectivity_ratio is not None:
            self._peer_connectivity.setdefault(peer, RollingAverage()).add(connectivity_ratio)

    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics.
        
        Returns:
            Dictionary containing performance stats
        """
        # Build peer-level dictionary -------------------------------------------
        peer_stats: Dict[str, Dict[str, float]] = {}
        for peer in set(
            list(self._peer_latency.keys())
            + list(self._peer_bandwidth.keys())
            + list(self._peer_connectivity.keys())
        ):
            peer_stats[peer] = {
                "latency_ms": self._peer_latency.get(peer, RollingAverage()).average,
                "bandwidth_mbps": self._peer_bandwidth.get(peer, RollingAverage()).average,
                "connectivity_ratio": self._peer_connectivity.get(peer, RollingAverage()).average,
            }

        return {
            "transaction_count": self.transaction_count,
            "error_count": self.error_count,
            "sync_count": self.sync_count,
            "validation_latency_ms": self._validation_latency.average,
            "reputation_score": self.get_reputation_score(),
            "network_metrics": asdict(self.network_metrics),
            "peer_metrics": peer_stats,
        } 