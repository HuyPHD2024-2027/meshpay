"""Safe fallback profile for Flash-Mesh D-SDN controller.

When the controller heartbeat is lost (e.g. 3 missed cycles), this
profile pushes conservative equal-bandwidth QoS rules to all managed
nodes so that network liveness is preserved even without active SDN
control.
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

# Number of consecutive missed heartbeats before activating fallback.
DEFAULT_MISS_THRESHOLD = 3


class FallbackProfile:
    """Static equal-bandwidth QoS rules activated on heartbeat loss.

    Parameters
    ----------
    miss_threshold : int
        Number of missed heartbeats before activation (default 3).
    heartbeat_interval : float
        Expected heartbeat interval in seconds (default 1.0).
    """

    def __init__(
        self,
        miss_threshold: int = DEFAULT_MISS_THRESHOLD,
        heartbeat_interval: float = 1.0,
    ) -> None:
        self._miss_threshold = miss_threshold
        self._heartbeat_interval = heartbeat_interval
        self._last_heartbeat: float = time.time()
        self._active = False
        self._managed_nodes: list = []

    # ── Public API ────────────────────────────────────────────────────

    def record_heartbeat(self) -> None:
        """Record a successful controller heartbeat."""
        self._last_heartbeat = time.time()
        if self._active:
            logger.info("Heartbeat restored – deactivating fallback")
            self.deactivate()

    def check(self) -> bool:
        """Check if fallback should be activated.

        Returns True if fallback was activated as a result of this check.
        """
        elapsed = time.time() - self._last_heartbeat
        missed = int(elapsed / self._heartbeat_interval)

        if missed >= self._miss_threshold and not self._active:
            logger.warning(
                "Heartbeat lost (%d missed) – activating fallback", missed
            )
            self.activate(self._managed_nodes)
            return True
        return False

    def set_managed_nodes(self, nodes: list) -> None:
        """Set the list of nodes to manage on fallback activation."""
        self._managed_nodes = nodes

    @property
    def is_active(self) -> bool:
        return self._active

    # ── Fallback rules ────────────────────────────────────────────────

    def activate(self, nodes: list) -> None:
        """Push conservative equal-bandwidth ``tc`` rules to all *nodes*."""
        for node in nodes:
            intf = f"{node.name}-wlan0"
            try:
                # Remove any existing qdisc
                node.cmd(f"tc qdisc del dev {intf} root 2>/dev/null")
                # Install simple SFQ (stochastic fair queueing)
                node.cmd(f"tc qdisc add dev {intf} root sfq perturb 10")
                logger.debug("Fallback SFQ installed on %s", node.name)
            except Exception as exc:
                logger.error("Fallback activation failed on %s: %s", node.name, exc)
        self._active = True
        logger.info("Fallback profile ACTIVE on %d nodes", len(nodes))

    def deactivate(self) -> None:
        """Remove fallback rules (the controller will reinstall priority QoS)."""
        for node in self._managed_nodes:
            intf = f"{node.name}-wlan0"
            try:
                node.cmd(f"tc qdisc del dev {intf} root 2>/dev/null")
                logger.debug("Fallback SFQ removed on %s", node.name)
            except Exception as exc:
                logger.error("Fallback deactivation failed on %s: %s", node.name, exc)
        self._active = False
        logger.info("Fallback profile DEACTIVATED")
