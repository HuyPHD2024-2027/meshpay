"""Flexible Routing Protocol architecture for DTN Mesh Networks.

This module defines the abstract interface `DTNRoutingProtocol` to manage
how messages are stored, carried, and forwarded when neighbors are encountered.
It also provides concrete implementations like `EpidemicRouting`.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from uuid import uuid4

from meshpay.types.transaction import MessageBufferItem

logger = logging.getLogger(__name__)


class DTNRoutingProtocol(ABC):
    """Abstract Base Class for DTN Routing strategies.
    
    Implementations dictate how a node communicates buffered messages with
    encountered neighbors (e.g., Epidemic, PROPHET, Spray and Wait).
    """
    
    def __init__(self, node_id: str):
        """Initialize the routing protocol for a specific node."""
        self.node_id = node_id
        # Queue of instructions/messages to pass to the transport layer
        self._outbox: List[Dict[str, Any]] = []

    @abstractmethod
    def on_neighbor_discovered(self, neighbor_id: str, current_buffer: Dict[str, MessageBufferItem]) -> None:
        """Called when a new neighbor is discovered or an old one is re-established.
        
        This should typically trigger the sending of a routing state message
        (like a Summary Vector for anti-entropy) to the neighbor.
        """
        pass

    @abstractmethod
    def on_routing_message_received(
        self, sender_id: str, payload: Dict[str, Any], current_buffer: Dict[str, MessageBufferItem]
    ) -> None:
        """Called when a MessageType.ROUTING_MESSAGE is received.
        
        Args:
            sender_id: The node_id of the neighbor who sent this message.
            payload: The inner payload of the routing message.
            current_buffer: Reference to the node's current message buffer.
        """
        pass
        
    @abstractmethod
    def on_message_added_to_buffer(self, msg_id: str, current_buffer: Dict[str, MessageBufferItem]) -> None:
        """Called when a local process adds a new message to the buffer.
        
        Some routing protocols might proactively forward newly buffered items.
        """
        pass

    def get_messages_to_send(self) -> List[Dict[str, Any]]:
        """Retrieve and clear the outbox of messages scheduled by the protocol.
        
        Returns:
            List of dictionaries dictating what to send. Format:
            {
                'recipient_id': str,
                'type': 'routing' | 'relay', 
                'payload': Dict (if routing),
                'msg_id': str (if relay)
            }
        """
        messages = self._outbox.copy()
        self._outbox.clear()
        return messages
        
    def _queue_routing_message(self, recipient_id: str, protocol_type: str, data: Any) -> None:
        """Helper to queue a routing control message."""
        self._outbox.append({
            'recipient_id': recipient_id,
            'type': 'routing',
            'payload': {
                'protocol_type': protocol_type,
                'data': data
            }
        })
        
    def _queue_relay_transmission(self, recipient_id: str, msg_id: str) -> None:
        """Helper to queue the transmission of an actual buffered relay message."""
        self._outbox.append({
            'recipient_id': recipient_id,
            'type': 'relay',
            'msg_id': msg_id
        })

