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
from queue import Queue
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from meshpay.types import Address, PeerInfo
from meshpay.messages import (
    Message,
    MessageType,
    MeshRelayMessage,
    PeerDiscoveryMessage,
)
from mn_wifi.services.core.config import (
    DEFAULT_RELAY_TTL,
    DISCOVERY_PORT,
    DISCOVERY_INTERVAL,
    NEIGHBOR_TIMEOUT,
)


class MeshMixin:
    """Shared mesh networking behavior for Client and Authority nodes.

    Expects the host class to have:
      - ``self.name: str``
      - ``self.address: Address``
      - ``self.state`` (with ``.neighbors``, ``.seen_order_ids``)
      - ``self.transport``
      - ``self.logger``
      - ``self._running: bool``
      - ``self.popen(...)`` (Mininet Station method)
    """

    # Subclasses override to advertise their capabilities.
    _service_capabilities: List[str] = ["relay"]

    # ------------------------------------------------------------------
    # Neighbor management
    # ------------------------------------------------------------------

    def _init_mesh(self) -> None:
        """Initialise mesh-specific data structures (call from __init__)."""
        self.p2p_connections: Dict[str, PeerInfo] = {}
        # message_queue is used by TCPTransport as its receive buffer.
        self.message_queue: Queue[Message] = Queue()

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
            proc = self.popen(
                ['ping', '-c', '1', '-W', '1', ip],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            proc.communicate(timeout=3.0)
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

                discovery = PeerDiscoveryMessage(
                    node_info=self.address,
                    service_capabilities=self._service_capabilities,
                    network_metrics=None,
                )

                msg = Message(
                    message_id=uuid4(),
                    message_type=MessageType.PEER_DISCOVERY,
                    sender=self.address,
                    recipient=None,
                    timestamp=time.time(),
                    payload=discovery.to_payload(),
                )

                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    data = msg.to_json().encode('utf-8')
                    sock.sendto(data, ('<broadcast>', DISCOVERY_PORT))

            except Exception as e:
                self.logger.debug(f"Discovery broadcast error: {e}")

            time.sleep(DISCOVERY_INTERVAL)

    def _discovery_listen_loop(self) -> None:
        """Listen for neighbour advertisements."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('0.0.0.0', DISCOVERY_PORT))
            except Exception as e:
                self.logger.error(f"Failed to bind discovery port: {e}")
                return

            while self._running:
                try:
                    data, addr = sock.recvfrom(4096)
                    msg = Message.from_json(data.decode('utf-8'))

                    if msg.message_type == MessageType.PEER_DISCOVERY:
                        peer_info = PeerDiscoveryMessage.from_payload(msg.payload)
                        peer_addr = peer_info.node_info

                        if peer_addr.node_id == self.name:
                            continue

                        if peer_addr.node_id not in self.state.neighbors:
                            if self._is_reachable(peer_addr.ip_address):
                                self.add_neighbor(peer_addr.node_id, peer_addr)
                        else:
                            if self._is_reachable(peer_addr.ip_address):
                                peer = self.p2p_connections.get(peer_addr.node_id)
                                if peer:
                                    peer.last_seen = time.time()
                            else:
                                self.remove_neighbor(peer_addr.node_id)

                except Exception as e:
                    self.logger.error(f"Discovery receive error: {e}")
                    continue

    # ------------------------------------------------------------------
    # Mesh relay helpers
    # ------------------------------------------------------------------

    def _build_relay_message(
        self,
        inner_type: str,
        inner_payload: Dict[str, Any],
        order_id: str,
        sender_id: Optional[str] = None,
        origin_address: Optional[Dict[str, Any]] = None,
        ttl: Optional[int] = None,
        hop_path: Optional[List[str]] = None,
    ) -> MeshRelayMessage:
        """Build a MeshRelayMessage (DRY helper)."""
        return MeshRelayMessage(
            original_sender_id=sender_id or self.name,
            origin_address=origin_address or {
                "node_id": self.address.node_id,
                "ip_address": self.address.ip_address,
                "port": self.address.port,
                "node_type": self.address.node_type.value,
            },
            inner_message_type=inner_type,
            inner_payload=inner_payload,
            order_id=order_id,
            ttl=ttl if ttl is not None else DEFAULT_RELAY_TTL,
            hop_path=hop_path if hop_path is not None else [self.name],
        )

    def _relay_to_neighbors(self, relay: MeshRelayMessage) -> int:
        """Send a relay message to all neighbours not already in *hop_path*.

        Returns the number of neighbours that successfully received it.
        """
        if relay.ttl <= 0:
            self.logger.debug(f"Relay TTL expired for order {relay.order_id}")
            return 0

        successes = 0
        neighbors = self.get_neighbors()
        for nid, addr in neighbors.items():
            if nid in relay.hop_path:
                continue

            msg = Message(
                message_id=uuid4(),
                message_type=MessageType.MESH_RELAY,
                sender=self.address,
                recipient=addr,
                timestamp=time.time(),
                payload=relay.to_payload(),
            )

            if self.transport.send_message(msg, addr):
                successes += 1
            else:
                self.logger.warning(f"Relay to {nid} failed")

        if successes == 0:
            self.logger.warning(f"Could not relay order {relay.order_id} to any neighbour")
        else:
            self.logger.info(
                f"Relayed order {relay.order_id} to {successes}/{len(self.state.neighbors)} neighbours"
            )
        return successes

    def _handle_mesh_relay(
        self,
        message: Message,
        on_transfer_response: Optional[Callable] = None,
        on_transfer_request: Optional[Callable] = None,
        on_confirmation: Optional[Callable] = None,
    ) -> None:
        """Unwrap and process a mesh relay message.

        Callbacks:
          - on_transfer_response(resp): called when a TRANSFER_RESPONSE is for us
          - on_transfer_request(relay): called when a TRANSFER_REQUEST arrives (authority)
          - on_confirmation(order): called when a CONFIRMATION_REQUEST is for us
        """
        from meshpay.messages import (
            ConfirmationRequestMessage,
            TransferResponseMessage,
        )

        relay = MeshRelayMessage.from_payload(message.payload)
        order_key = relay.order_id

        # ── Deduplication ──
        if order_key in self.state.seen_order_ids:
            if not (
                relay.inner_message_type == MessageType.TRANSFER_RESPONSE.value
                and relay.original_sender_id == self.name
            ):
                self.logger.debug(f"Duplicate relay for order {order_key} – skipping")
                return
        else:
            self.state.seen_order_ids.add(order_key)

        consumed = False

        # ── TRANSFER_RESPONSE destined for us ──
        if relay.inner_message_type == MessageType.TRANSFER_RESPONSE.value:
            if relay.original_sender_id == self.name and on_transfer_response:
                resp = TransferResponseMessage.from_payload(relay.inner_payload)
                on_transfer_response(resp)
                consumed = True

        # ── TRANSFER_REQUEST (authority consumes these) ──
        elif relay.inner_message_type == MessageType.TRANSFER_REQUEST.value:
            if on_transfer_request:
                on_transfer_request(relay)
                consumed = True

        # ── CONFIRMATION_REQUEST ──
        elif relay.inner_message_type == MessageType.CONFIRMATION_REQUEST.value:
            if on_confirmation:
                req = ConfirmationRequestMessage.from_payload(relay.inner_payload)
                if req.confirmation_order.transfer_order.recipient == self.name:
                    on_confirmation(req.confirmation_order)
                    consumed = True

        # ── Re-relay if TTL allows ──
        if relay.ttl > 1:
            next_relay = MeshRelayMessage(
                original_sender_id=relay.original_sender_id,
                origin_address=relay.origin_address,
                inner_message_type=relay.inner_message_type,
                inner_payload=relay.inner_payload,
                order_id=relay.order_id,
                ttl=relay.ttl - 1,
                hop_path=relay.hop_path + [self.name],
            )
            self._relay_to_neighbors(next_relay)
        else:
            self.logger.debug(f"Relay TTL expired for order {order_key}")
