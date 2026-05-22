"""Lightweight PROPHET-inspired DTN routing for MeshPay buffers."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from meshpay.routing.dtn import DTNRoutingProtocol
from meshpay.types.transaction import MessageBufferItem

logger = logging.getLogger(__name__)


class ProphetRouting(DTNRoutingProtocol):
    """PROPHET-style delivery predictability routing.

    This implementation keeps the DTN protocol interface small: nodes exchange
    local predictability tables and relay messages when a neighbor appears to
    have at least comparable delivery utility for a destination.
    """

    def __init__(self, node_id: str) -> None:
        super().__init__(node_id)
        self.delivery_probabilities: Dict[str, float] = {node_id: 1.0}
        self._last_summary_sent: Dict[str, float] = {}
        self._last_relay_to_peer: Dict[str, Dict[str, int]] = {}
        self.summary_cooldown = 1.5
        self.encounter_boost = 0.75
        self.transitive_scale = 0.25

    def on_neighbor_discovered(self, neighbor_id: str, current_buffer: Dict[str, MessageBufferItem]) -> None:
        self._age_probabilities()
        previous = self.delivery_probabilities.get(neighbor_id, 0.0)
        self.delivery_probabilities[neighbor_id] = previous + (1.0 - previous) * self.encounter_boost

        now = time.time()
        if now - self._last_summary_sent.get(neighbor_id, 0.0) <= self.summary_cooldown:
            return

        keys = [msg_id for msg_id, item in current_buffer.items() if not item.is_expired]
        self._queue_routing_message(
            recipient_id=neighbor_id,
            protocol_type="prophet_summary",
            data={"keys": keys, "predictabilities": self.delivery_probabilities},
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
        if p_type == "prophet_summary":
            self._merge_predictabilities(sender_id, data.get("predictabilities", {}))
            self._handle_summary(sender_id, data.get("keys", []), data.get("predictabilities", {}), current_buffer)
        elif p_type == "prophet_request":
            self._handle_request(sender_id, data.get("requested_keys", []), current_buffer)
        else:
            logger.warning("[%s] Unknown PROPHET protocol_type: %s", self.node_id, p_type)

    def on_message_added_to_buffer(self, msg_id: str, current_buffer: Dict[str, MessageBufferItem]) -> None:
        if msg_id not in current_buffer or current_buffer[msg_id].is_expired:
            return
        for neighbor_id in list(self._last_summary_sent.keys()):
            self._queue_routing_message(
                recipient_id=neighbor_id,
                protocol_type="prophet_summary",
                data={"keys": [msg_id], "predictabilities": self.delivery_probabilities},
            )

    def _age_probabilities(self) -> None:
        for node_id in list(self.delivery_probabilities.keys()):
            if node_id == self.node_id:
                continue
            self.delivery_probabilities[node_id] *= 0.98

    def _merge_predictabilities(self, sender_id: str, peer_probs: Dict[str, Any]) -> None:
        sender_prob = self.delivery_probabilities.get(sender_id, 0.0)
        for node_id, value in peer_probs.items():
            try:
                peer_prob = float(value)
            except (TypeError, ValueError):
                continue
            local_prob = self.delivery_probabilities.get(node_id, 0.0)
            transitive = sender_prob * peer_prob * self.transitive_scale
            if transitive > local_prob:
                self.delivery_probabilities[str(node_id)] = min(1.0, transitive)

    def _message_destination(self, item: MessageBufferItem) -> str:
        payload = item.payload or {}
        transfer = payload.get("transfer_order")
        if isinstance(transfer, dict):
            if item.message_type == "transfer_response":
                return str(transfer.get("sender", ""))
            return str(transfer.get("recipient", ""))

        confirmation = payload.get("confirmation_order")
        if isinstance(confirmation, dict):
            nested_transfer = confirmation.get("transfer_order", {})
            if isinstance(nested_transfer, dict):
                return str(nested_transfer.get("recipient", ""))
        return ""

    def _handle_summary(
        self,
        sender_id: str,
        neighbor_keys: Any,
        neighbor_probs: Dict[str, Any],
        current_buffer: Dict[str, MessageBufferItem],
    ) -> None:
        local_keys = {msg_id for msg_id, item in current_buffer.items() if not item.is_expired}
        missing = [key for key in neighbor_keys if key not in local_keys]
        if missing:
            self._queue_routing_message(
                recipient_id=sender_id,
                protocol_type="prophet_request",
                data={"requested_keys": missing},
            )

        for msg_id, item in current_buffer.items():
            if item.is_expired or msg_id in neighbor_keys:
                continue
            destination = self._message_destination(item)
            if not destination:
                continue
            try:
                peer_score = float(neighbor_probs.get(destination, 0.0))
            except (TypeError, ValueError):
                peer_score = 0.0
            local_score = self.delivery_probabilities.get(destination, 0.0)
            if peer_score >= local_score and self._can_relay_to(sender_id, msg_id):
                self._queue_relay_transmission(sender_id, msg_id)

    def _handle_request(self, sender_id: str, requested_keys: Any, current_buffer: Dict[str, MessageBufferItem]) -> None:
        for msg_id in requested_keys:
            item = current_buffer.get(msg_id)
            if not item or item.is_expired or not self._can_relay_to(sender_id, msg_id):
                continue
            self._queue_relay_transmission(sender_id, msg_id)

    def _can_relay_to(self, neighbor_id: str, msg_id: str) -> bool:
        peer_counts = self._last_relay_to_peer.setdefault(neighbor_id, {})
        if peer_counts.get(msg_id, 0) > 0:
            return False
        peer_counts[msg_id] = 1
        return True
