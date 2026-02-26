"""MeshPay client – opportunistic wireless mesh relay.

Instead of broadcasting transfer orders directly to the committee of
authorities, the client sends them to **any reachable neighbour node**.
Each neighbour re-broadcasts the order to *its* neighbours (with TTL
decrement and deduplication) until an authority processes the order and
relays a signed response back through the mesh.

Inherits shared mesh networking behavior from :class:`MeshMixin`.
"""

from __future__ import annotations

import time
import threading
from typing import Dict, List, Optional
from uuid import uuid4

from mn_wifi.node import Station

from meshpay.types import (
    Address,
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
from meshpay.transport.transport import NetworkTransport, TransportKind
from meshpay.transport.tcp import TCPTransport
from meshpay.transport.udp import UDPTransport
from meshpay.transport.wifiDirect import WiFiDirectTransport
from meshpay.logger.clientLogger import ClientLogger

from meshpay.nodes.mesh_utils import MeshMixin


class Client(MeshMixin, Station):
    """Client node for opportunistic wireless mesh payment relay.

    Inherits mesh networking (neighbor management, discovery, relay) from
    :class:`MeshMixin`.  This is the simple (non-buffered) client variant.
    """

    _service_capabilities = ["relay", "client"]

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
        self._running = False
        self._message_handler_thread: Optional[threading.Thread] = None

        # Init mesh mixin data structures
        self._init_mesh()

    # ------------------------------------------------------------------
    # Service lifecycle
    # ------------------------------------------------------------------

    def start_fastpay_services(self) -> bool:
        """Boot-strap background processing threads and ready the transport."""
        if hasattr(self.transport, "connect"):
            try:
                if not self.transport.connect():  # type: ignore[attr-defined]
                    self.logger.error("Failed to connect transport")
                    return False
            except Exception as exc:
                self.logger.error(f"Transport connect error: {exc}")
                return False

        self._running = True
        self._message_handler_thread = threading.Thread(
            target=self._message_handler_loop,
            daemon=True,
        )
        self._message_handler_thread.start()
        self._start_discovery_service()

        self.logger.info(f"Client {self.name} started successfully")
        return True

    def stop_fastpay_services(self) -> None:
        """Stop the FastPay client services."""
        self._running = False
        if hasattr(self.transport, "disconnect"):
            try:
                self.transport.disconnect()  # type: ignore[attr-defined]
            except Exception:
                pass
        if self._message_handler_thread:
            self._message_handler_thread.join(timeout=5.0)
        self.logger.info(f"Client {self.name} stopped")

    # ------------------------------------------------------------------
    # Transfer – opportunistic mesh relay
    # ------------------------------------------------------------------

    def transfer(
        self,
        recipient: str,
        token_address: str,
        amount: int,
    ) -> bool:
        """Initiate a transfer by relaying the order through the mesh."""
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
        request = TransferRequestMessage(transfer_order=order)
        self.state.pending_transfer = order
        self.state.seen_order_ids.add(str(order.order_id))

        relay_msg = self._build_relay_message(
            inner_type=MessageType.TRANSFER_REQUEST.value,
            inner_payload=request.to_payload(),
            order_id=str(order.order_id),
        )
        return self._relay_to_neighbors(relay_msg) > 0

    # ------------------------------------------------------------------
    # Transfer response handling
    # ------------------------------------------------------------------

    def _validate_transfer_response(self, resp: TransferResponseMessage) -> bool:
        """Validate a transfer response received from an authority."""
        if resp.transfer_order.sender != self.state.name:
            self.logger.error(f"Transfer {resp.transfer_order.order_id} failed: sender mismatch")
            return False
        if resp.transfer_order.sequence_number != self.state.sequence_number:
            self.logger.error(f"Transfer {resp.transfer_order.order_id} failed: sequence mismatch")
            return False
        return True

    def handle_transfer_response(self, resp: TransferResponseMessage) -> bool:
        """Handle transfer response from authority (received via mesh relay)."""
        try:
            if not self._validate_transfer_response(resp):
                return False

            self.state.sent_certificates.append(resp)
            self.logger.info(
                f"Collected signature ({len(self.state.sent_certificates)} total) "
                f"for order {resp.transfer_order.order_id}"
            )

            committee_size = len(self.state.committee)
            quorum = int(committee_size * 2 / 3) + 1 if committee_size > 0 else 1
            if len(self.state.sent_certificates) >= quorum and self.state.pending_transfer:
                self.logger.info("Quorum reached – broadcasting confirmation via mesh")
                self.broadcast_confirmation()

            return True
        except Exception as e:
            self.logger.error(f"Error handling transfer response: {e}")
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
            self.state.seen_order_ids.discard(str(transfer.order_id))

            self.logger.info(
                f"Confirmation {transfer.order_id} applied – "
                f"sender={transfer.sender}, amount={transfer.amount}"
            )
            return True
        except Exception as e:
            self.logger.error(f"Error handling confirmation order: {e}")
            return False

    def broadcast_confirmation(self) -> None:
        """Create and relay a ConfirmationOrder through the mesh."""
        if not self.state.pending_transfer:
            self.logger.error("No pending transfer to confirm")
            return

        committee_size = len(self.state.committee)
        quorum = int(committee_size * 2 / 3) + 1 if committee_size > 0 else 1

        if len(self.state.sent_certificates) < quorum:
            self.logger.error("Not enough transfer certificates to confirm")
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
        relay_msg = self._build_relay_message(
            inner_type=MessageType.CONFIRMATION_REQUEST.value,
            inner_payload=req.to_payload(),
            order_id=str(order.order_id),
        )
        self._relay_to_neighbors(relay_msg)

        self.state.pending_transfer = None
        self.state.sequence_number += 1
        self.state.sent_certificates = []
        self.state.balance -= order.amount

    # ------------------------------------------------------------------
    # Message processing – mesh relay aware
    # ------------------------------------------------------------------

    def _process_message(self, message: Message) -> None:
        """Process incoming message, handling mesh relay wrapping."""
        try:
            if message.message_type == MessageType.MESH_RELAY:
                self._handle_mesh_relay(
                    message,
                    on_transfer_response=self.handle_transfer_response,
                    on_confirmation=self.handle_confirmation_order,
                )
                return

            # Legacy direct messages (backwards compat).
            if message.message_type == MessageType.TRANSFER_RESPONSE:
                request = TransferResponseMessage.from_payload(message.payload)
                self.handle_transfer_response(request)
            elif message.message_type == MessageType.CONFIRMATION_REQUEST:
                request = ConfirmationRequestMessage.from_payload(message.payload)
                self.handle_confirmation_order(request.confirmation_order)

        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _message_handler_loop(self) -> None:
        """Background thread loop that polls the transport for incoming messages."""
        while self._running:
            try:
                message = self.transport.receive_message(timeout=1.0)
                if message:
                    self._process_message(message)
            except Exception as exc:
                if hasattr(self, "logger"):
                    self.logger.error(f"Error in message handler loop: {exc}")
                time.sleep(0.2)


__all__ = ["Client"]
