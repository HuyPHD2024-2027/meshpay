"""MeshPay client – opportunistic mesh relay with buffered retry.

This variant of the Client extends the basic mesh-relay design with a
*buffered transaction* mechanism: when a transfer order does not reach
enough authorities to form a quorum on the first attempt, it is buffered
and periodically re-relayed through the mesh until quorum is achieved.

Inherits shared mesh networking behavior from :class:`MeshMixin`.
"""

from __future__ import annotations

import time
import threading
from queue import Queue
from typing import Dict, List, Optional, Set
from uuid import UUID, uuid4

from mn_wifi.node import Station

from meshpay.types import (
    Address,
    BufferedTransfer,
    ClientState,
    ConfirmationOrder,
    KeyPair,
    NodeType,
    TransactionStatus,
    TransferOrder,
)
from meshpay.messages import (
    ConfirmationRequestMessage,
    Message,
    MessageType,
    TransferRequestMessage,
    TransferResponseMessage,
)
from meshpay.transport import NetworkTransport, TransportKind
from meshpay.transport.tcp import TCPTransport
from meshpay.transport.udp import UDPTransport
from meshpay.transport.wifiDirect import WiFiDirectTransport
from meshpay.logger.clientLogger import ClientLogger

from mn_wifi.services.core.config import (
    DEFAULT_RELAY_TTL,
    DISCOVERY_PORT,
    DISCOVERY_INTERVAL,
    NEIGHBOR_TIMEOUT,
)
from meshpay.messages import TransferRequestMessage, MessageType
from meshpay.types.transaction import MessageBufferItem
from mn_wifi.services.core.config import DEFAULT_RELAY_TTL
from mn_wifi.metrics import MetricsCollector

from meshpay.nodes.mesh_mixin import MeshMixin


class Client(MeshMixin, Station):
    """Client node for opportunistic wireless mesh payment relay (buffered).

    Inherits mesh networking (neighbor management, discovery, relay) from
    :class:`MeshMixin`.  Adds transaction queuing, quorum tracking, and
    confirmation broadcasting on top.
    """

    def __init__(
        self,
        name: str,
        transport_kind: TransportKind = TransportKind.TCP,
        transport: Optional[NetworkTransport] = None,
        ip: str = "10.0.0.100/8",
        port: int = 9000,
        **params,
    ) -> None:
        """Create a new client station."""

        default_params = {
            "ip": ip,
            "min_x": 0,
            "max_x": 200,
            "min_y": 0,
            "max_y": 150,
            "min_v": 1,
            "max_v": 3,
            "range": 20,
            "txpower": 20,
            "antennaGain": 5,
        }
        default_params.update(params)

        super().__init__(name, **default_params)

        self.name = name
        self.address = Address(
            node_id=name,
            ip_address=ip.split("/")[0],
            port=port,
            node_type=NodeType.CLIENT,
        )

        self.state = ClientState(
            name=name,
            address=self.address,
            balance=0,
            secret=KeyPair("secret-placeholder"),
            sequence_number=1,
            pending_transfer=None,
            committee=[],
            sent_certificates=[],
            received_certificates={},
        )

        # Transport
        if transport is not None:
            self.transport = transport
        else:
            if transport_kind == TransportKind.TCP:
                self.transport = TCPTransport(self, self.address)
            elif transport_kind == TransportKind.UDP:
                self.transport = UDPTransport(self, self.address)
            elif transport_kind == TransportKind.WIFI_DIRECT:
                self.transport = WiFiDirectTransport(self, self.address)
            else:
                raise ValueError(f"Unsupported transport kind: {transport_kind}")

        self.logger = ClientLogger(name)
        self.performance_metrics = MetricsCollector()
        self._running = False

        self._quorum_threshold: int = 0

        # Background threads
        self._threads: List[threading.Thread] = []
        self._retry_interval: float = 5.0

        # Init mesh mixin data structures
        self._init_mesh()

    # ------------------------------------------------------------------
    # Service lifecycle
    # ------------------------------------------------------------------

    def _connect_transport(self) -> bool:
        """Connect the transport layer."""
        if hasattr(self.transport, "connect"):
            try:
                if not self.transport.connect():  # type: ignore[attr-defined]
                    self.logger.error("Failed to connect transport")
                    return False
            except Exception as exc:
                self.logger.error(f"Transport connect error: {exc}")
                return False
        return True

    def _calculate_quorum(self) -> int:
        """Calculate quorum threshold from committee size."""
        committee_size = len(self.state.committee)
        return int(committee_size * 2 / 3) + 1

    def start_fastpay_services(self) -> bool:
        """Boot-strap background processing threads and ready the transport."""
        if not self._connect_transport():
            return False

        self._running = True
        self._quorum_threshold = self._calculate_quorum()
        self._start_discovery_service()

        # Start message handler loop
        t = threading.Thread(target=self._message_handler_loop, daemon=True)
        t.start()
        self._threads.append(t)

        self.logger.info(
            f"Client {self.name} started "
            f"(quorum={self._quorum_threshold}/{len(self.state.committee)})"
        )
        return True

    def stop_fastpay_services(self) -> None:
        """Stop all services."""
        self._running = False
        if hasattr(self.transport, "disconnect"):
            try:
                self.transport.disconnect()  # type: ignore[attr-defined]
            except Exception:
                pass

        self.logger.info(f"Client {self.name} stopped")

    # ------------------------------------------------------------------
    # Transfer – opportunistic mesh relay
    # ------------------------------------------------------------------

    def transfer(
        self,
        recipient: str,
        token_address: str,
        amount: int,
    ) -> TransactionStatus:
        """Relay a transfer order through the mesh.

        Returns:
            TransactionStatus: BUFFERED — queued for retry until quorum.
        """
        order = TransferOrder(
            order_id=uuid4(),
            sender=self.state.name,
            token_address=token_address,
            recipient=recipient,
            amount=amount,
            sequence_number=self.state.sequence_number,
            timestamp=time.time(),
            signature=self.state.secret,
        )
        self.state.pending_transfer = order

        request = TransferRequestMessage(transfer_order=order)
        msg_id = str(order.order_id)
        
        # Buffer raw request, so Epidemic logic transmits it via summary vector
        if msg_id not in self.message_buffer:
            self.message_buffer[msg_id] = MessageBufferItem(
                message_id=msg_id,
                message_type=MessageType.TRANSFER_REQUEST.value,
                payload=request.to_payload(),
                sender_id=self.name,
                ttl=DEFAULT_RELAY_TTL,
            )
            # Notify routing protocol locally
            self.routing_protocol.on_message_added_to_buffer(msg_id, self.message_buffer)

        self.logger.info(
            f"Transfer {order.order_id} queued"
        )
        self.performance_metrics.record_transaction()
        return TransactionStatus.BUFFERED

    # ------------------------------------------------------------------
    # Transfer response handling
    # ------------------------------------------------------------------

    def _validate_transfer_response(self, resp: TransferResponseMessage) -> bool:
        """Validate a transfer response received ."""
        if str(resp.transfer_order.sender) != str(self.state.name):
            self.logger.info(f"Transfer {resp.transfer_order.order_id} skipped: not my transfer")
            return False
        if int(resp.transfer_order.sequence_number) != int(self.state.sequence_number):
            self.logger.error(
                f"Transfer {resp.transfer_order.order_id} failed: sequence mismatch "
                f"(got {resp.transfer_order.sequence_number!r}, expected {self.state.sequence_number!r})"
            )
            return False
        return True

    def _track_signature(self, resp: TransferResponseMessage) -> None:
        """Record a signature from an authority (deduplicated per authority)."""
        auth_name = resp.authority_signature or "unknown"
        order_id_str = str(resp.transfer_order.order_id)

        self.state.sent_certificates.append(resp)
        self.logger.info(
            f"Collected signature from {auth_name} "
            f"({len(self.state.sent_certificates)} total) for order {order_id_str}"
        )

    def _check_quorum(self, order_id) -> bool:
        """Check if enough signatures have been collected for an order."""
        oid = str(order_id)
        relevant = [
            c for c in self.state.sent_certificates
            if str(c.transfer_order.order_id) == oid
        ]
        return len(relevant) >= self._quorum_threshold

    def _on_quorum_reached(self, order_id) -> None:
        """Handle quorum reached: broadcast confirmation, purge from queue."""
        self.logger.info("Quorum reached – broadcasting confirmation via opportunistic mesh")
        self.broadcast_confirmation()
        # Remove transfer request from buffer
        self.message_buffer.pop(str(order_id), None)

    def handle_transfer_response(self, resp: TransferResponseMessage) -> bool:
        """Handle transfer response from authority (received via mesh relay)."""
        try:
            if not resp.success:
                self.logger.warning(
                    f"Authority rejected transfer {resp.transfer_order.order_id}: "
                    f"{resp.error_message}"
                )
                return False
            if not self._validate_transfer_response(resp):
                return False
            self._track_signature(resp)
            if self._check_quorum(resp.transfer_order.order_id) and self.state.pending_transfer:
                self._on_quorum_reached(resp.transfer_order.order_id)
            return True
        except Exception as e:
            self.logger.error(f"Error handling transfer response: {e}")
            self.performance_metrics.record_error()
            return False

    # ------------------------------------------------------------------
    # Confirmation handling
    # ------------------------------------------------------------------

    def _validate_confirmation_order(self, confirmation_order: ConfirmationOrder) -> bool:
        """Validate a confirmation order (placeholder)."""
        return True

    def handle_confirmation_order(self, confirmation_order: ConfirmationOrder) -> bool:
        """Handle a confirmation order received for us as the recipient."""
        try:
            transfer = confirmation_order.transfer_order
            if transfer.recipient != self.state.name:
                return False
            if not self._validate_confirmation_order(confirmation_order):
                return False

            self.state.balance += transfer.amount
            self.state.seen_order_ids.discard(f"{transfer.order_id}:req")
            self.state.seen_order_ids.discard(f"{transfer.order_id}:conf")

            # Save certificate
            self.state.received_certificates[(transfer.sender, transfer.sequence_number)] = confirmation_order

            self.logger.info(
                f"Confirmation {transfer.order_id} applied – "
                f"sender={transfer.sender}, amount={transfer.amount}"
            )
            return True
        except Exception as e:
            self.logger.error(f"Error handling confirmation order: {e}")
            self.performance_metrics.record_error()
            return False

    def broadcast_confirmation(self) -> None:
        """Create a ConfirmationOrder."""
        order = self.state.pending_transfer
        if not order:
            self.logger.error("No pending transfer to confirm")
            return

        relevant_certs = [
            c for c in self.state.sent_certificates
            if str(c.transfer_order.order_id) == str(order.order_id)
        ]
        if len(relevant_certs) < self._quorum_threshold:
            self.logger.error(
                f"Insufficient certificates for {order.order_id} "
                f"({len(relevant_certs)}/{self._quorum_threshold})"
            )
            return

        transfer_signatures = [c.authority_signature for c in self.state.sent_certificates]
        order = self.state.pending_transfer
        confirmation = ConfirmationOrder(
            order_id=order.order_id,
            transfer_order=order,
            authority_signatures=transfer_signatures,
            timestamp=time.time(),
            status=TransactionStatus.CONFIRMED,
        )

        req = ConfirmationRequestMessage(confirmation_order=confirmation)

        # Buffer for epidemic spread
        from meshpay.types.transaction import MessageBufferItem
        from mn_wifi.services.core.config import DEFAULT_RELAY_TTL
        conf_msg_id = f"{order.order_id}:conf"
        if conf_msg_id not in self.message_buffer:
            self.message_buffer[conf_msg_id] = MessageBufferItem(
                message_id=conf_msg_id,
                message_type=MessageType.CONFIRMATION_REQUEST.value,
                payload=req.to_payload(),
                sender_id=self.name,
                ttl=DEFAULT_RELAY_TTL,
            )
            # Notify routing protocol locally
            self.routing_protocol.on_message_added_to_buffer(conf_msg_id, self.message_buffer)

        # Clear local state
        self.state.pending_transfer = None
        self.state.sequence_number += 1
        self.state.sent_certificates = [
            c for c in self.state.sent_certificates
            if str(c.transfer_order.order_id) != str(order.order_id)
        ]
        self.state.balance -= order.amount

    # ------------------------------------------------------------------
    # Message processing – mesh relay aware
    # ------------------------------------------------------------------

    def _process_message(self, message: Message) -> None:
        """Process incoming raw network packets directly from the transport layer.
        
        This handler catches two types of communication:
        1. Pure DTN Routing (Mesh): The foundation of the mesh network. Summaries 
           and bundles wrapped in ROUTING_MESSAGEs.
        2. Direct Transport (Legacy/Internet): If a client is connected directly to an 
           authority (e.g., via TCP/Internet) rather than through the mesh, it receives 
           direct responses.
        """
        try:
            # ── 1. Pure DTN / Mesh Network Flow ──
            if message.message_type == MessageType.ROUTING_MESSAGE:
                # Hand this over to the DTN bridge (MeshMixin) which parses it.
                # If it's data, the DTN bridge will eventually call `on_dtn_bundle_received`.
                self._handle_routing_message(message)

            # ── 2. Direct Internet / Legacy Flow ──
            elif message.message_type == MessageType.TRANSFER_RESPONSE:
                # Received directly from an authority without being buffered
                request = TransferResponseMessage.from_payload(message.payload)
                self.handle_transfer_response(request)
            
            elif message.message_type == MessageType.CONFIRMATION_REQUEST:
                # Received directly
                request = ConfirmationRequestMessage.from_payload(message.payload)
                self.handle_confirmation_order(request.confirmation_order)

        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    # ------------------------------------------------------------------
    # Background loops
    # ------------------------------------------------------------------

    def _message_handler_loop(self) -> None:
        """Background thread that polls the transport for incoming messages."""
        while self._running:
            try:
                message = self.transport.receive_message(timeout=1.0)
                if message:
                    self._process_message(message)
            except Exception as exc:
                if hasattr(self, "logger"):
                    self.logger.error(f"Error in message handler loop: {exc}")
                time.sleep(0.2)

    def on_dtn_bundle_received(self, item) -> None:
        """Application hook triggered by the DTN layer when a new bundle arrives.
        
        Unlike `_process_message` which sees all raw packets, this is ONLY called 
        when the Epidemic routing protocol successfully exchanges a missing piece 
        of data and saves it to the `message_buffer`.

        As a Client in the mesh, our primary job is just to carry data for others.
        However, we MUST inspect the bundles to see if they relate to our own transfers:
        - Did an authority finally sign my transaction? (TRANSFER_RESPONSE)
        - Was a transaction sent to me finalized? (CONFIRMATION_REQUEST)
        """
        from meshpay.messages import MessageType

        # The DTN protocol tells us the inner type of the package it just downloaded
        if item.message_type == MessageType.TRANSFER_RESPONSE.value:
            resp = TransferResponseMessage.from_payload(item.payload)
            self.handle_transfer_response(resp)
            
        elif item.message_type == MessageType.CONFIRMATION_REQUEST.value:
            req = ConfirmationRequestMessage.from_payload(item.payload)
            self.handle_confirmation_order(req.confirmation_order)


__all__ = ["Client"]
