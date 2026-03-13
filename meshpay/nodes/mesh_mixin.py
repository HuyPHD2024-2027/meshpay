"""Shared mesh networking behavior for MeshPay nodes.

Provides the :class:`MeshMixin` that encapsulates neighbor management,
UDP broadcast discovery, ping-based reachability, and hop-limited mesh
relay logic.  Both :class:`Client` and :class:`WiFiAuthority` inherit
from this mixin so that the networking code is defined in one place.
"""

from __future__ import annotations

import socket
import subprocess
import threading
import time
import json
import math
import traceback
from queue import Queue
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from meshpay.types import Address, PeerInfo
from meshpay.messages import (
    Message,
    MessageType,
    PeerDiscoveryMessage,
    RoutingMessage,
)
from mn_wifi.services.core.config import (
    DEFAULT_RELAY_TTL,
    DISCOVERY_PORT,
    DISCOVERY_INTERVAL,
    NEIGHBOR_TIMEOUT,
)

from meshpay.nodes import mesh_utils

class MeshMixin:
    """Shared mesh networking behavior for Client and Authority nodes."""

    # Subclasses override to advertise their capabilities.
    _service_capabilities: List[str] = ["relay"]

    # ------------------------------------------------------------------
    # Core Infrastructure
    # ------------------------------------------------------------------

    def _init_mesh(self) -> None:
        """Initialise mesh-specific data structures (call from __init__)."""
        from meshpay.routing.epidemic import EpidemicRouting
        
        self.p2p_connections: Dict[str, PeerInfo] = {}
        # message_queue is used by TCPTransport as its receive buffer.
        self.message_queue: Queue[Message] = Queue()
        
        # DTN Store-Carry-Forward buffer and Routing Protocol
        self.message_buffer = {}  # Dict[str, MessageBufferItem]
        self.routing_protocol = EpidemicRouting(getattr(self, 'name', 'unknown'))
        self.telemetry_aggregator_ip: Optional[str] = None

        # Performance Evaluation Counters
        self.control_bytes_sent = 0
        self.data_bytes_sent = 0

    def _log(self, msg: str, level: str = "info") -> None:
        """Centralized logging helper that safely handles missing loggers."""
        logger = getattr(self, "logger", None)
        if not logger:
            return
        
        log_func = getattr(logger, level, logger.info)
        log_func(msg)

    # ------------------------------------------------------------------
    # Neighbor management
    # ------------------------------------------------------------------

    def add_neighbor(self, node_id: str, address: Address) -> None:
        """Register a neighbour node."""
        self.state.neighbors[node_id] = address
        self.p2p_connections[node_id] = PeerInfo(address=address, last_seen=time.time())

    def remove_neighbor(self, node_id: str) -> None:
        """Remove a neighbour node."""
        self.state.neighbors.pop(node_id, None)
        self.p2p_connections.pop(node_id, None)

    def get_neighbor_last_seen(self, node_id: str) -> Optional[float]:
        """Return the last seen timestamp for a specific neighbor."""
        peer = self.p2p_connections.get(node_id)
        return peer.last_seen if peer else None

    def get_neighbors(self) -> Dict[str, Address]:
        """Return a snapshot of the current neighbour table, filtering out stale ones."""
        now = time.time()
        stale_ids = [
            nid for nid, peer in self.p2p_connections.items()
            if now - peer.last_seen > NEIGHBOR_TIMEOUT
        ]
        for nid in stale_ids:
            self.remove_neighbor(nid)
        return dict(self.state.neighbors)

    def _is_reachable(self, ip: str) -> bool:
        """Check if an IP is reachable via ping (validates actual wireless link)."""
        try:
            # Increase timeout/count slightly for simulated wireless links
            proc = self.popen(
                ['ping', '-c', '1', '-W', '1', ip],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            proc.communicate(timeout=2.0)
            return proc.returncode == 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Discovery service (UDP broadcast)
    # ------------------------------------------------------------------

    def _start_discovery_service(self) -> None:
        """Start background threads for neighbour discovery."""
        threading.Thread(target=self._discovery_listen_loop, daemon=True).start()
        threading.Thread(target=self._discovery_broadcast_loop, daemon=True).start()

    def _discovery_broadcast_loop(self) -> None:
        """Periodically broadcast presence to neighbours."""
        while self._running:
            try:
                # Prune stale neighbors
                self.get_neighbors()

                pos = getattr(self, "position", (0.0, 0.0, 0.0))
                # Ensure it's a tuple of floats
                try:
                    pos_tuple = (float(pos[0]), float(pos[1]), float(pos[2]))
                except (IndexError, TypeError, ValueError):
                    pos_tuple = (0.0, 0.0, 0.0)

                # Piggyback latest telemetry if daemon is available
                telemetry_data = None
                daemon = getattr(self, "telemetry_daemon", None)
                if daemon:
                    state = daemon.get_latest_state()
                    if state:
                        telemetry_data = state.to_dict()

                discovery = PeerDiscoveryMessage(
                    node_info=self.address,
                    service_capabilities=self._service_capabilities,
                    telemetry=telemetry_data,
                    position=pos_tuple
                )

                msg = Message(
                    message_id=uuid4(),
                    message_type=MessageType.PEER_DISCOVERY,
                    sender=self.address,
                    recipient=None,
                    timestamp=time.time(),
                    payload=discovery.to_payload(),
                )

                # Report our OWN telemetry to the global aggregator too
                if telemetry_data:
                    self._update_telemetry_aggregator(telemetry_data)

                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    # Bind to 0.0.0.0 to ensure broadcast goes through simulation interfaces
                    # sock.bind(('0.0.0.0', 0)) # Usually not needed if routing is correct
                    data = msg.to_json().encode('utf-8')
                    self.control_bytes_sent += len(data)
                    # Use global broadcast for better reliability in virtual sims
                    sock.sendto(data, ('255.255.255.255', DISCOVERY_PORT))

            except Exception as e:
                self._log(f"Discovery broadcast error: {e}", "debug")

            time.sleep(DISCOVERY_INTERVAL)

    def _discovery_listen_loop(self) -> None:
        """Listen for neighbour advertisements."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('0.0.0.0', DISCOVERY_PORT))
            except Exception as e:
                self._log(f"Failed to bind discovery port {DISCOVERY_PORT}: {e}", "error")
                return

            while self._running:
                try:
                    data, addr = sock.recvfrom(4096)
                    msg = Message.from_json(data.decode('utf-8'))

                    if msg.message_type == MessageType.PEER_DISCOVERY:
                        self._handle_discovery_message(msg)

                except Exception as e:
                    self._log(f"Discovery receive error: {e}", "error")
                    continue

    def _handle_discovery_message(self, msg: Message) -> None:
        """Process an incoming discovery message and update neighbors."""
        peer_info = PeerDiscoveryMessage.from_payload(msg.payload)
        peer_addr = peer_info.node_info

        if peer_addr.node_id == self.name:
            return

        # NEW: Remove strict ping check for discovery. 
        # Hearing the UDP packet is enough to prove the peer is in range.
        if peer_addr.node_id not in self.state.neighbors:
            self.add_neighbor(peer_addr.node_id, peer_addr)
            self._update_peer_state(peer_addr.node_id, peer_info)
            self.routing_protocol.on_neighbor_discovered(peer_addr.node_id, self.message_buffer)
            self._flush_routing_outbox()
        else:
            self._update_peer_state(peer_addr.node_id, peer_info)
            self.routing_protocol.on_neighbor_discovered(peer_addr.node_id, self.message_buffer)
            self._flush_routing_outbox()

    def _update_peer_state(self, node_id: str, peer_info: PeerDiscoveryMessage) -> None:
        """Update last seen, position, and RSSI for a peer."""
        peer = self.p2p_connections.get(node_id)
        if not peer:
            return
            
        peer.last_seen = time.time()
        peer.position = peer_info.position
        
        if peer_info.telemetry:
            peer.rssi = peer_info.telemetry.get("wireless", {}).get("rssi_dbm")
            self._update_telemetry_aggregator(peer_info.telemetry)

    # ------------------------------------------------------------------
    # DTN Integration Bridge
    # ------------------------------------------------------------------

    def _flush_routing_outbox(self) -> None:
        """Drain the routing protocol's outbox and send over transport."""
        outbox = self.routing_protocol.get_messages_to_send()

        for instr in outbox:
            recipient_id = instr.get("recipient_id")
            recipient_addr = self.state.neighbors.get(recipient_id)
            if not recipient_addr:
                continue

            telemetry_data = None
            daemon = getattr(self, "telemetry_daemon", None)
            if daemon:
                state = daemon.get_latest_state()
                if state:
                    telemetry_data = state.to_dict()

            msg_type = instr.get("type")
            if msg_type == "routing":
                routing_msg = RoutingMessage(
                    protocol_type=instr["payload"]["protocol_type"],
                    data=instr["payload"]["data"],
                    telemetry=telemetry_data
                )
            elif msg_type == "relay":
                msg_id = instr.get("msg_id")
                item = self.message_buffer.get(msg_id)
                if not item: continue
                routing_msg = RoutingMessage(
                    protocol_type="dtn_bundle",
                    data=item.to_dict() if hasattr(item, 'to_dict') else {
                        "message_id": item.message_id,
                        "message_type": item.message_type,
                        "payload": item.payload,
                        "sender_id": item.sender_id,
                        "ttl": item.ttl,
                    },
                    telemetry=telemetry_data
                )
            else:
                continue

            msg = Message(
                message_id=uuid4(),
                message_type=MessageType.ROUTING_MESSAGE,
                sender=self.address,
                recipient=recipient_addr,
                timestamp=time.time(),
                payload=routing_msg.to_payload(),
            )
            # Track overhead: relay is data, routing is control
            msg_size = len(msg.to_json().encode('utf-8'))
            if msg_type == "relay":
                self.data_bytes_sent += msg_size
            else:
                self.control_bytes_sent += msg_size

            self.transport.send_message(msg, recipient_addr)

    def _handle_routing_message(self, message: Message) -> None:
        """Dispatch an incoming ROUTING_MESSAGE."""
        routing_msg = RoutingMessage.from_payload(message.payload)
        sender_id = message.sender.node_id

        if routing_msg.telemetry:
            self._update_telemetry_aggregator(routing_msg.telemetry)
        
        if sender_id not in self.state.neighbors:
            self.state.neighbors[sender_id] = message.sender

        if routing_msg.protocol_type == "dtn_bundle":
            self._handle_dtn_bundle(routing_msg.data)
        else:
            self.routing_protocol.on_routing_message_received(
                sender_id, routing_msg.to_payload(), self.message_buffer
            )

        self._flush_routing_outbox()

    def _handle_dtn_bundle(self, bundle: Dict[str, Any]) -> None:
        """Process an incoming DTN data bundle."""
        from meshpay.types.transaction import MessageBufferItem
        
        msg_id = bundle.get("message_id")
        if msg_id in self.message_buffer:
            return

        new_ttl = bundle.get("ttl", 1) - 1
        if new_ttl <= 0:
            return

        item = MessageBufferItem(
            message_id=msg_id,
            message_type=bundle.get("message_type"),
            payload=bundle.get("payload"),
            sender_id=bundle.get("sender_id"),
            ttl=new_ttl,
        )
        self.message_buffer[msg_id] = item
        self.routing_protocol.on_message_added_to_buffer(msg_id, self.message_buffer)
        self.on_dtn_bundle_received(item)

    def on_dtn_bundle_received(self, item) -> None:
        """Application-layer hook called when a new DTN bundle arrives."""
        pass

    # ------------------------------------------------------------------
    # Telemetry and state exposure
    # ------------------------------------------------------------------

    def _update_telemetry_aggregator(self, telemetry_dict: Optional[Dict[str, Any]]) -> None:
        mesh_utils.update_telemetry_aggregator(self, telemetry_dict)
