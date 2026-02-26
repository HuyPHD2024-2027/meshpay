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
    BufferedTransaction,
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
    MeshRelayMessage,
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

from meshpay.nodes.mesh_utils import MeshMixin


class Client(MeshMixin, Station):
    """Client node for opportunistic wireless mesh payment relay (buffered).

    Inherits mesh networking (neighbor management, discovery, relay) from
    :class:`MeshMixin`.  Adds transaction queuing, quorum tracking, and
    confirmation broadcasting on top.
    """

    _service_capabilities = ["relay", "client-buffered"]

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

        # Transaction queue (DTN store-carry-forward)
        self.transaction_queue: Dict[UUID, BufferedTransaction] = {}
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

    def _start_background_threads(self) -> None:
        """Start message handler and retry threads."""
        for target in [self._message_handler_loop, self._retry_loop]:
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)

    def start_fastpay_services(self) -> bool:
        """Boot-strap background processing threads and ready the transport."""
        if not self._connect_transport():
            return False

        self._running = True
        self._quorum_threshold = self._calculate_quorum()
        self._start_background_threads()
        self._start_discovery_service()

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

        for t in self._threads:
            t.join(timeout=5.0)

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
        request = TransferRequestMessage(transfer_order=order)
        self.state.pending_transfer = order
        self.state.seen_order_ids.add(str(order.order_id))

        relay_msg = self._build_relay_message(
            inner_type=MessageType.TRANSFER_REQUEST.value,
            inner_payload=request.to_payload(),
            order_id=str(order.order_id),
        )
        self._relay_to_neighbors(relay_msg)

        # Buffer for retry
        self.transaction_queue[order.order_id] = BufferedTransaction(
            order=order,
            signatures_received={},
            signatures_required=self._quorum_threshold,
            status=TransactionStatus.BUFFERED,
        )
        self.logger.info(
            f"Transfer {order.order_id} relayed to mesh and queued for retry"
        )
        return TransactionStatus.BUFFERED

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

    def _track_signature(self, resp: TransferResponseMessage) -> None:
        """Record a signature from an authority."""
        self.state.sent_certificates.append(resp)
        order_id = resp.transfer_order.order_id
        if order_id in self.transaction_queue:
            bt = self.transaction_queue[order_id]
            auth_name = resp.authority_signature or "unknown"
            bt.add_signature(auth_name, resp.authority_signature or "")
        self.logger.info(
            f"Collected signature ({len(self.state.sent_certificates)} total) "
            f"for order {order_id}"
        )

    def _check_quorum(self, order_id: UUID) -> bool:
        """Check if enough signatures have been collected for an order."""
        relevant = [
            c for c in self.state.sent_certificates
            if c.transfer_order.order_id == order_id
        ]
        return len(relevant) >= self._quorum_threshold

    def _on_quorum_reached(self, order_id: UUID) -> None:
        """Handle quorum reached: broadcast confirmation, purge from queue."""
        self.logger.info("Quorum reached – broadcasting confirmation via mesh")
        self.broadcast_confirmation()
        if order_id in self.transaction_queue:
            self.transaction_queue[order_id].status = TransactionStatus.FINALIZED
            del self.transaction_queue[order_id]

    def handle_transfer_response(self, resp: TransferResponseMessage) -> bool:
        """Handle transfer response from authority (received via mesh relay)."""
        try:
            if not self._validate_transfer_response(resp):
                return False
            self._track_signature(resp)
            if self._check_quorum(resp.transfer_order.order_id) and self.state.pending_transfer:
                self._on_quorum_reached(resp.transfer_order.order_id)
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

            if transfer.order_id in self.transaction_queue:
                del self.transaction_queue[transfer.order_id]

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
        order = self.state.pending_transfer
        if not order:
            self.logger.error("No pending transfer to confirm")
            return

        relevant_certs = [
            c for c in self.state.sent_certificates
            if c.transfer_order.order_id == order.order_id
        ]
        if len(relevant_certs) < self._quorum_threshold:
            self.logger.error(
                f"Insufficient certificates for {order.order_id} "
                f"({len(relevant_certs)}/{self._quorum_threshold})"
            )
            return

        transfer_signatures = [c.authority_signature for c in relevant_certs]
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

        # Clear local state
        self.state.pending_transfer = None
        self.state.sequence_number += 1
        self.state.sent_certificates = [
            c for c in self.state.sent_certificates
            if c.transfer_order.order_id != order.order_id
        ]
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

    def _retry_loop(self) -> None:
        """Background thread that re-relays queued transactions periodically."""
        while self._running:
            time.sleep(self._retry_interval)

            if not self.transaction_queue:
                continue

            self.logger.info(
                f"Retry loop: {len(self.transaction_queue)} queued transactions"
            )

            completed: List[UUID] = []

            for tx_id, btx in list(self.transaction_queue.items()):
                if btx.status != TransactionStatus.BUFFERED:
                    continue

                # Purge relayed messages older than 2 minutes
                if btx.is_relay and (time.time() - btx.created_at > 120):
                    completed.append(tx_id)
                    continue

                btx.retry_count += 1
                btx.last_retry = time.time()
                self.logger.info(
                    f"Retrying tx {tx_id} via mesh relay (attempt {btx.retry_count})"
                )

                self.state.seen_order_ids.discard(str(tx_id))
                self.state.seen_order_ids.add(str(tx_id))

                request = TransferRequestMessage(transfer_order=btx.order)

                if btx.is_relay and btx.relay_metadata:
                    relay_msg = self._build_relay_message(
                        inner_type=MessageType.TRANSFER_REQUEST.value,
                        inner_payload=request.to_payload(),
                        order_id=str(tx_id),
                        sender_id=btx.relay_metadata["original_sender_id"],
                        origin_address=btx.relay_metadata["origin_address"],
                        ttl=btx.relay_metadata.get("ttl", DEFAULT_RELAY_TTL),
                        hop_path=btx.relay_metadata.get("hop_path", [self.name]),
                    )
                else:
                    relay_msg = self._build_relay_message(
                        inner_type=MessageType.TRANSFER_REQUEST.value,
                        inner_payload=request.to_payload(),
                        order_id=str(tx_id),
                    )
                self._relay_to_neighbors(relay_msg)

                # Check for quorum (own TXs only)
                if not btx.is_relay and self._check_quorum(tx_id):
                    self.logger.info(f"Transaction {tx_id} reached quorum after retry!")
                    btx.status = TransactionStatus.FINALIZED
                    completed.append(tx_id)
                    self._finalize_buffered_transaction(btx)

            for tx_id in completed:
                self.transaction_queue.pop(tx_id, None)

    def _finalize_buffered_transaction(self, btx: BufferedTransaction) -> None:
        """Complete a buffered transaction that has reached quorum."""
        self.logger.info(f"Finalizing transaction {btx.order.order_id}")
        self.state.pending_transfer = btx.order
        self.broadcast_confirmation()

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    def get_buffered_transactions(self) -> Dict[UUID, BufferedTransaction]:
        """Return dict of queued transactions for inspection."""
        return self.transaction_queue.copy()

    def get_transaction_status(self, order_id: UUID) -> Optional[TransactionStatus]:
        """Get the status of a transaction by its order ID."""
        if order_id in self.transaction_queue:
            return self.transaction_queue[order_id].status
        return None


__all__ = ["Client"]
