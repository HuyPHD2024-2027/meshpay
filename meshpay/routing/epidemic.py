"""Epiedemic Routing Protocol architecture for DTN Mesh Networks.
"""

from __future__ import annotations
from meshpay.routing.dtn import DTNRoutingProtocol

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from uuid import uuid4

from meshpay.types.transaction import MessageBufferItem


logger = logging.getLogger(__name__)


class EpidemicRouting(DTNRoutingProtocol):
    """Epidemic Routing implementation with Anti-Entropy (Summary Vectors).
    
    When a neighbor is encountered, nodes exchange a list of their locally 
    buffered message IDs (Summary Vector). They then request any missing 
    messages from each other.
    """
    
    def __init__(self, node_id: str):
        super().__init__(node_id)
        # Track last summary exchange time to avoid spamming
        self._last_summary_sent: Dict[str, float] = {}
        self.summary_cooldown = 2.0  # seconds

    def on_neighbor_discovered(self, neighbor_id: str, current_buffer: Dict[str, MessageBufferItem]) -> None:
        """Send a summary vector if cooldown has passed."""
        now = time.time()
        last_sent = self._last_summary_sent.get(neighbor_id, 0.0)
        
        if now - last_sent > self.summary_cooldown:
            # Send keys of all unexpired items in buffer
            keys = [msg_id for msg_id, item in current_buffer.items() if not item.is_expired]
            logger.debug(f"[{self.node_id}] Sending epidemic_summary with {len(keys)} items to {neighbor_id}")
            
            self._queue_routing_message(
                recipient_id=neighbor_id,
                protocol_type="epidemic_summary",
                data={"keys": keys}
            )
            self._last_summary_sent[neighbor_id] = now

    def on_routing_message_received(
        self, sender_id: str, payload: Dict[str, Any], current_buffer: Dict[str, MessageBufferItem]
    ) -> None:
        """Handle incoming epidemic routing messages."""
        p_type = payload.get("protocol_type")
        data = payload.get("data", {})
        
        if p_type == "epidemic_summary":
            self._handle_summary(sender_id, data.get("keys", []), current_buffer)
        elif p_type == "epidemic_request":
            self._handle_request(sender_id, data.get("requested_keys", []), current_buffer)
        else:
            logger.warning(f"[{self.node_id}] Unknown Epidemic routing protocol_type: {p_type}")

    def on_message_added_to_buffer(self, msg_id: str, current_buffer: Dict[str, MessageBufferItem]) -> None:
        """For pure Epidemic, we rely on the periodic summary exchange, 
        but we could optionally proactively broadcast to known neighbors here.
        For now, do nothing and wait for discovery/cooldown.
        """
        pass

    def _handle_summary(self, sender_id: str, neighbor_keys: List[str], current_buffer: Dict[str, MessageBufferItem]) -> None:
        """Compare neighbor's summary vector with local buffer and request missing pieces."""
        local_keys = set(msg_id for msg_id, item in current_buffer.items() if not item.is_expired)
        
        # We want anything the neighbor has that we DON'T have
        missing_keys = [k for k in neighbor_keys if k not in local_keys]
        
        if missing_keys:
            logger.debug(f"[{self.node_id}] Requesting {len(missing_keys)} missing messages from {sender_id}")
            self._queue_routing_message(
                recipient_id=sender_id,
                protocol_type="epidemic_request",
                data={"requested_keys": missing_keys}
            )

    def _handle_request(self, sender_id: str, requested_keys: List[str], current_buffer: Dict[str, MessageBufferItem]) -> None:
        """Send the requested full messages to the neighbor."""
        logger.debug(f"[{self.node_id}] Satisfying epidemic_request for {len(requested_keys)} msgs from {sender_id}")
        for msg_id in requested_keys:
            # Check if we still have it and it's not expired
            if msg_id in current_buffer and not current_buffer[msg_id].is_expired:
                self._queue_relay_transmission(recipient_id=sender_id, msg_id=msg_id)
