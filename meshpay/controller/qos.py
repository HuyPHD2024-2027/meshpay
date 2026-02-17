"""QoS manager for Flash-Mesh BCB traffic classification.

Uses Linux ``tc`` prio qdiscs for non-P4 nodes and wraps BMv2 Thrift /
ONOS REST for P4-enabled nodes.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from meshpay.types import BCBPriorityClass

logger = logging.getLogger(__name__)

# Port ranges used to classify traffic into priority bands.
# Band 0 (strict-highest): BCB votes & certificates  →  ports 8001-8099
# Band 1:                  Payment data               →  ports 9001-9099
# Band 2 (best effort):    Everything else

BCB_PORT_LO = 8001
BCB_PORT_HI = 8099
PAY_PORT_LO = 9001
PAY_PORT_HI = 9099


@dataclass
class QueueStats:
    """Per-band queue statistics parsed from ``tc -s``."""

    band: int
    sent_bytes: int = 0
    sent_packets: int = 0
    dropped: int = 0
    overlimits: int = 0


class QoSManager:
    """Manage strict-priority queues on mesh nodes.

    For the MVP this uses ``tc`` (works in every Mininet-WiFi namespace).
    The P4 path can be wired in later via ``bmv2Thrift`` or ONOS REST.
    """

    def __init__(self) -> None:
        self._installed_nodes: set[str] = set()

    # ── Public API ────────────────────────────────────────────────────

    def install_priority(self, node) -> bool:
        """Install a 3-band prio qdisc on *node*'s wireless interface.

        Returns True on success, False on error.
        """
        intf = f"{node.name}-wlan0"
        if node.name in self._installed_nodes:
            logger.debug("QoS already installed on %s", node.name)
            return True

        try:
            # Remove any existing qdisc first (ignore errors)
            node.cmd(f"tc qdisc del dev {intf} root 2>/dev/null")

            # Create prio qdisc with 3 bands
            node.cmd(f"tc qdisc add dev {intf} root handle 1: prio bands 3")

            # Band 0 (highest): BCB ports
            node.cmd(
                f"tc filter add dev {intf} parent 1:0 protocol ip prio 1 "
                f"u32 match ip dport {BCB_PORT_LO} 0xff00 flowid 1:1"
            )

            # Band 1: Payment ports
            node.cmd(
                f"tc filter add dev {intf} parent 1:0 protocol ip prio 2 "
                f"u32 match ip dport {PAY_PORT_LO} 0xff00 flowid 1:2"
            )

            # Band 2: everything else (default)
            self._installed_nodes.add(node.name)
            logger.info("QoS installed on %s (%s)", node.name, intf)
            return True

        except Exception as exc:
            logger.error("Failed to install QoS on %s: %s", node.name, exc)
            return False

    def remove_priority(self, node) -> bool:
        """Tear down QoS qdiscs on *node*."""
        intf = f"{node.name}-wlan0"
        try:
            node.cmd(f"tc qdisc del dev {intf} root 2>/dev/null")
            self._installed_nodes.discard(node.name)
            logger.info("QoS removed on %s", node.name)
            return True
        except Exception as exc:
            logger.error("Failed to remove QoS on %s: %s", node.name, exc)
            return False

    def get_queue_stats(self, node) -> Dict[int, QueueStats]:
        """Parse ``tc -s qdisc`` output into per-band stats."""
        intf = f"{node.name}-wlan0"
        try:
            raw = node.cmd(f"tc -s qdisc show dev {intf}")
            return self._parse_tc_stats(raw)
        except Exception as exc:
            logger.error("Failed to get queue stats on %s: %s", node.name, exc)
            return {}

    # ── Internals ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_tc_stats(raw: str) -> Dict[int, QueueStats]:
        """Best-effort parser for ``tc -s qdisc`` output."""
        stats: Dict[int, QueueStats] = {}
        band = -1
        for line in raw.splitlines():
            # Each prio band appears as a separate qdisc block
            if "prio" in line or "pfifo" in line:
                band += 1

            match = re.search(
                r"Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt.*?"
                r"dropped\s+(\d+).*?overlimits\s+(\d+)",
                line,
            )
            if match and band >= 0:
                stats[band] = QueueStats(
                    band=band,
                    sent_bytes=int(match.group(1)),
                    sent_packets=int(match.group(2)),
                    dropped=int(match.group(3)),
                    overlimits=int(match.group(4)),
                )
        return stats

    # ── Future: P4 flow-rule push ─────────────────────────────────────

    def install_p4_priority(self, switch, priority_class: BCBPriorityClass) -> bool:
        """Push BCB priority table entries via BMv2 Thrift (stub).

        Will use ``switch.bmv2Thrift('table_add ...')`` once a .p4 program
        with a ``bcb_priority`` table is compiled and loaded.
        """
        logger.warning("P4 priority push is stubbed for MVP")
        return False
