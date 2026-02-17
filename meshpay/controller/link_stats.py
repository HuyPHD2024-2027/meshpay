"""Link-stats collector for Flash-Mesh D-SDN controller.

Periodically samples wireless link quality from mesh nodes using
``iw dev ... station dump`` inside each node's network namespace.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LinkSample:
    """Snapshot of wireless link quality for a single node."""

    node_name: str
    rssi: int = -100        # dBm
    signal: int = -100      # dBm (alias used by some drivers)
    tx_bytes: int = 0
    rx_bytes: int = 0
    tx_packets: int = 0
    rx_packets: int = 0
    expected_throughput: float = 0.0  # Mbit/s
    rtt_ms: float = 0.0
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class LinkStatsCollector:
    """Background sampler that periodically gathers link metrics from nodes.

    Parameters
    ----------
    nodes : list
        Mininet-WiFi Station objects to sample.
    interval_ms : int
        Sampling interval in milliseconds (default 500).
    """

    def __init__(self, nodes: list, interval_ms: int = 500) -> None:
        self._nodes = nodes
        self._interval = interval_ms / 1000.0
        self._samples: Dict[str, LinkSample] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Begin periodic sampling in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        logger.info("LinkStatsCollector started (interval=%dms)", int(self._interval * 1000))

    def stop(self) -> None:
        """Stop sampling."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        logger.info("LinkStatsCollector stopped")

    # ── Query API ─────────────────────────────────────────────────────

    def get(self, node_name: str) -> Optional[LinkSample]:
        """Return the latest sample for *node_name*, or None."""
        with self._lock:
            return self._samples.get(node_name)

    def get_all(self) -> Dict[str, LinkSample]:
        """Return a snapshot of all latest samples."""
        with self._lock:
            return dict(self._samples)

    # ── Internals ─────────────────────────────────────────────────────

    def _sample_loop(self) -> None:
        while self._running:
            for node in self._nodes:
                try:
                    sample = self._collect_sample(node)
                    with self._lock:
                        self._samples[node.name] = sample
                except Exception as exc:
                    logger.debug("Sample failed for %s: %s", node.name, exc)
            time.sleep(self._interval)

    @staticmethod
    def _collect_sample(node) -> LinkSample:
        """Run ``iw`` inside the node namespace and parse results."""
        intf = f"{node.name}-wlan0"
        raw = node.cmd(f"iw dev {intf} station dump 2>/dev/null")

        sample = LinkSample(node_name=node.name)
        for line in raw.splitlines():
            line = line.strip()
            if "signal:" in line:
                try:
                    sample.rssi = int(line.split("signal:")[1].strip().split()[0])
                    sample.signal = sample.rssi
                except (IndexError, ValueError):
                    pass
            elif "tx bytes:" in line:
                try:
                    sample.tx_bytes = int(line.split("tx bytes:")[1].strip())
                except (IndexError, ValueError):
                    pass
            elif "rx bytes:" in line:
                try:
                    sample.rx_bytes = int(line.split("rx bytes:")[1].strip())
                except (IndexError, ValueError):
                    pass
            elif "expected throughput:" in line:
                try:
                    sample.expected_throughput = float(
                        line.split("expected throughput:")[1].strip().replace("Mbps", "")
                    )
                except (IndexError, ValueError):
                    pass
        return sample
