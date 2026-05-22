"""Spray-and-Wait DTN routing for MeshPay buffers."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from meshpay.routing.dtn import DTNRoutingProtocol
from meshpay.types.transaction import MessageBufferItem

logger = logging.getLogger(__name__)


class SprayAndWaitRouting(DTNRoutingProtocol):
    """Binary Spray-and-Wait with bounded per-message copy tickets."""

    def __init__(self, node_id: str, initial_copies: int = 6) -> None:
        super().__init__(node_id)
        self.initial_copies = max(1, int(initial_copies))
        self._copies: Dict[str, int] = {}
        self._last_summary_sent: Dict[str, float] = {}
        self.summary_cooldown = 1.5

    def on_neighbor_discovered(self, neighbor_id: str, current_buffer: Dict[str, MessageBufferItem]) -> None:
        now = time.time()
        if now - self._last_summary_sent.get(neighbor_id, 0.0) <= self.summary_cooldown:
            return

        keys = []
        for msg_id, item in current_buffer.items():
            if item.is_expired:
                continue
            self._copies.setdefault(msg_id, self.initial_copies)
            if self._copies.get(msg_id, 0) > 0:
                keys.append(msg_id)

        self._queue_routing_message(
            recipient_id=neighbor_id,
            protocol_type="spray_summary",
            data={"keys": keys},
        )
        self._last_summary_sent[neighbor_id] = now

    def on_routing_message_received(
        self,
        sender_id: str,
        payload: Dict[str, Any],
        current_buffer: Dict[str, MessageBufferItem],
    ) -> None:
        p_type = payload.get("protocol_type")
        data = payload.get("data", {})

        if p_type == "spray_summary":
            local_keys = {msg_id for msg_id, item in current_buffer.items() if not item.is_expired}
            missing = [key for key in data.get("keys", []) if key not in local_keys]
            if missing:
                self._queue_routing_message(
                    recipient_id=sender_id,
                    protocol_type="spray_request",
                    data={"requested_keys": missing},
                )
        elif p_type == "spray_request":
            for msg_id in data.get("requested_keys", []):
                item = current_buffer.get(msg_id)
                if not item or item.is_expired:
                    continue
                copies = self._copies.setdefault(msg_id, self.initial_copies)
                if copies <= 0:
                    continue
                self._queue_relay_transmission(sender_id, msg_id)
                self._copies[msg_id] = max(0, copies // 2)
        else:
            logger.warning("[%s] Unknown Spray-and-Wait protocol_type: %s", self.node_id, p_type)

    def on_message_added_to_buffer(self, msg_id: str, current_buffer: Dict[str, MessageBufferItem]) -> None:
        if msg_id in current_buffer and not current_buffer[msg_id].is_expired:
            self._copies.setdefault(msg_id, self.initial_copies)
            for neighbor_id in list(self._last_summary_sent.keys()):
                self._queue_routing_message(
                    recipient_id=neighbor_id,
                    protocol_type="spray_summary",
                    data={"keys": [msg_id]},
                )
