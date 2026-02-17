from __future__ import annotations

"""MeshPay client implementation capable of using multiple network transports.

This class mirrors the previous implementation under ``mn_wifi.client`` but is
now housed under the MeshPay namespace. It communicates via a pluggable
``NetworkTransport`` and operates in the Mininet-WiFi simulation environment.
"""

import time
import threading
from queue import Queue
from typing import Dict, Optional, List
from uuid import UUID, uuid4

from meshpay.types import (
    Address,
    ClientState,
    NodeType,
    TransferOrder,
    KeyPair,
    AuthorityName,
    ConfirmationOrder,
    TransactionStatus,
    BufferedTransaction,
)
from meshpay.messages import (
    Message,
    MessageType,
    TransferRequestMessage,
    TransferResponseMessage,
    ConfirmationRequestMessage,
)
from mn_wifi.node import Station
from meshpay.transport.transport import NetworkTransport, TransportKind
from meshpay.transport.tcp import TCPTransport
from meshpay.transport.udp import UDPTransport
from meshpay.transport.wifiDirect import WiFiDirectTransport
from meshpay.logger.clientLogger import ClientLogger


class Client(Station):
    """Client node which can be added to a Mininet-WiFi topology using addStation.

    The class embeds the FastPay client logic while extending Station so that it
    participates in the wireless network simulation natively. Upon construction
    the caller may choose one of the supported transport kinds or inject an
    already configured NetworkTransport instance.
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

        self.p2p_connections: Dict[str, Address] = {}
        self.message_queue: Queue[Message] = Queue()

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
        
        # Buffered transactions awaiting quorum
        self._buffered_transactions: Dict[UUID, BufferedTransaction] = {}
        self._retry_thread: Optional[threading.Thread] = None
        self._retry_interval: float = 5.0  # Retry every 5 seconds
        self._quorum_threshold: int = 0  # Will be set based on committee size


    def start_fastpay_services(self) -> bool:
        """Boot-strap background processing threads and ready the transport."""
        if hasattr(self.transport, "connect"):
            try:
                if not self.transport.connect():  # type: ignore[attr-defined]
                    self.logger.error("Failed to connect transport")
                    return False
            except Exception as exc:  # pragma: no cover
                self.logger.error(f"Transport connect error: {exc}")
                return False

        self._running = True
        
        # Calculate quorum threshold (2/3 + 1 of committee)
        committee_size = len(self.state.committee)
        self._quorum_threshold = int(committee_size * 2 / 3) + 1
        
        self._message_handler_thread = threading.Thread(
            target=self._message_handler_loop,
            daemon=True,
        )
        self._message_handler_thread.start()
        
        # Start retry thread for buffered transactions
        self._retry_thread = threading.Thread(
            target=self._retry_loop,
            daemon=True,
        )
        self._retry_thread.start()

        self.logger.info(f"Client {self.name} started (quorum={self._quorum_threshold}/{committee_size})")
        return True
    
    def stop_fastpay_services(self) -> None:
        """Stop the FastPay client services."""
        self._running = False
        if hasattr(self.transport, "disconnect"):
            try:
                self.transport.disconnect()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover
                pass
        
        if self._message_handler_thread:
            self._message_handler_thread.join(timeout=5.0)
        if self._retry_thread:
            self._retry_thread.join(timeout=5.0)
        self.logger.info(f"Client {self.name} stopped")

    def transfer(
        self,
        recipient: str,
        token_address: str,
        amount: int,
    ) -> TransactionStatus:
        """Broadcast a transfer order to the committee.
        
        Returns:
            TransactionStatus: PENDING if broadcast started, BUFFERED if quorum
            not immediately reached, FINALIZED if instant quorum (rare).
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

        message = Message(
            message_id=uuid4(),
            message_type=MessageType.TRANSFER_REQUEST,
            sender=self.state.address,
            recipient=None,
            timestamp=time.time(),
            payload=request.to_payload(),
        )
        
        # Broadcast and track signatures
        signatures_received = self._broadcast_and_collect_signatures(order, message)
        
        if signatures_received >= self._quorum_threshold:
            self.logger.info(f"Transfer {order.order_id} reached quorum immediately!")
            return TransactionStatus.FINALIZED
        else:
            # Buffer for retry
            buffered = BufferedTransaction(
                order=order,
                signatures_received={},  # Will be populated by responses
                signatures_required=self._quorum_threshold,
                status=TransactionStatus.BUFFERED,
            )
            self._buffered_transactions[order.order_id] = buffered
            self.logger.info(
                f"Transfer {order.order_id} buffered ({signatures_received}/{self._quorum_threshold} signatures)"
            )
            return TransactionStatus.BUFFERED
    
    def _broadcast_and_collect_signatures(
        self, order: TransferOrder, message: Message
    ) -> int:
        """Broadcast transfer request and return number of successful sends."""
        self.logger.info(
            f"Broadcasting transfer request to {len(self.state.committee)} authorities"
        )

        successes = 0
        for auth in self.state.committee:
            msg = Message(
                message_id=uuid4(),
                message_type=message.message_type,
                sender=message.sender,
                recipient=auth.address,
                timestamp=time.time(),
                payload=message.payload,
            )

            if self.transport.send_message(msg, auth.address):
                successes += 1
            else:
                self.logger.warning(f"Failed to send to authority {auth.name}")

        if successes == 0:
            self.logger.error("Failed to send transfer request to any authority")

        self.logger.info(
            f"Transfer request delivered to {successes}/{len(self.state.committee)} authorities"
        )
        return successes
    
    def _validate_transfer_response(self, transfer_response: TransferResponseMessage) -> bool:
        """Validate a transfer response received from an authority."""
        if transfer_response.transfer_order.sender != self.state.name:
            self.logger.error(f"Transfer {transfer_response.transfer_order.order_id} failed: sender mismatch")
            return False
        
        if transfer_response.transfer_order.sequence_number != self.state.sequence_number:
            self.logger.error(f"Transfer {transfer_response.transfer_order.order_id} failed: sequence number mismatch")
            return False
        return True
    
    def handle_transfer_response(self, transfer_response: TransferResponseMessage) -> bool:
        """Handle transfer response from authority."""
        try:
            if not self._validate_transfer_response(transfer_response):
                return False
            
            self.state.sent_certificates.append(transfer_response)
            return True
            
        except Exception as e:
            self.logger.error(f"Error handling transfer response: {e}")
            return False
        
    def _validate_confirmation_order(self, confirmation_order: ConfirmationOrder) -> bool:
        """Validate a confirmation order (placeholder)."""
        return True     
        
    def handle_confirmation_order(self, confirmation_order: ConfirmationOrder) -> bool:
        """Handle confirmation order from committee."""
        try:
            transfer = confirmation_order.transfer_order
            
            if transfer.recipient != self.state.name:
                return False
            
            if not self._validate_confirmation_order(confirmation_order):
                return False
            
            self.state.balance += transfer.amount

            self.logger.info(
                f"Confirmation {transfer.order_id} applied – sender={transfer.sender}, amount={transfer.amount}"
            )
            self.logger.info(f"Confirmation order {confirmation_order.order_id} processed")
            return True
            
        except Exception as e:
            self.logger.error(f"Error handling confirmation order: {e}")
            # Note: client has no performance metrics collector yet
            return False

    def broadcast_confirmation(self) -> None:
        """Create and broadcast a ConfirmationOrder (internal helper)."""
        order = self.state.pending_transfer
        
        # Filter certificates to only include those for the current pending transfer
        relevant_certs = [c for c in self.state.sent_certificates 
                         if c.transfer_order.order_id == order.order_id]

        if len(relevant_certs) < self._quorum_threshold:
            self.logger.error(f"Insufficient certificates for {order.order_id} ({len(relevant_certs)}/{self._quorum_threshold})")
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

        for auth in self.state.committee:
            msg = Message(
                message_id=uuid4(),
                message_type=MessageType.CONFIRMATION_REQUEST,
                sender=self.address,
                recipient=auth.address,
                timestamp=time.time(),
                payload=req.to_payload(),
            )
            self.transport.send_message(msg, auth.address)

        self.state.pending_transfer = None
        self.state.sequence_number += 1
        # Remove only certificates related to this specific order
        self.state.sent_certificates = [c for c in self.state.sent_certificates 
                                       if c.transfer_order.order_id != order.order_id]
        self.state.balance -= order.amount

    def _process_message(self, message: Message) -> None:
        """Process incoming message."""
        try:
            if message.message_type == MessageType.TRANSFER_RESPONSE:
                request = TransferResponseMessage.from_payload(message.payload)
                self.handle_transfer_response(request)

            if message.message_type == MessageType.CONFIRMATION_REQUEST:
                request = ConfirmationRequestMessage.from_payload(message.payload)
                self.handle_confirmation_order(request.confirmation_order)
                
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    def _message_handler_loop(self) -> None:
        """Background thread loop that polls the transport for incoming messages."""
        while self._running:
            try:
                message = self.transport.receive_message(timeout=1.0)
                if message:
                    self._process_message(message)
            except Exception as exc:  # pragma: no cover
                if hasattr(self, "logger"):
                    self.logger.error(f"Error in message handler loop: {exc}")
                time.sleep(0.2)

    def _retry_loop(self) -> None:
        """Background thread that retries buffered transactions every 5 seconds."""
        while self._running:
            time.sleep(self._retry_interval)
            
            if not self._buffered_transactions:
                continue
                
            self.logger.info(
                f"Retry loop: {len(self._buffered_transactions)} buffered transactions"
            )
            
            # Process each buffered transaction
            completed_txs: List[UUID] = []
            
            for tx_id, buffered_tx in self._buffered_transactions.items():
                if buffered_tx.status != TransactionStatus.BUFFERED:
                    continue
                    
                # Retry broadcasting
                buffered_tx.retry_count += 1
                buffered_tx.last_retry = time.time()
                
                self.logger.info(
                    f"Retrying tx {tx_id} (attempt {buffered_tx.retry_count})"
                )
                
                # Create new request message
                request = TransferRequestMessage(transfer_order=buffered_tx.order)
                message = Message(
                    message_id=uuid4(),
                    message_type=MessageType.TRANSFER_REQUEST,
                    sender=self.state.address,
                    recipient=None,
                    timestamp=time.time(),
                    payload=request.to_payload(),
                )
                
                # Re-broadcast
                signatures = self._broadcast_and_collect_signatures(
                    buffered_tx.order, message
                )
                
                # Check quorum for this specific transaction
                relevant_votes = [c for c in self.state.sent_certificates 
                                if c.transfer_order.order_id == tx_id]
                if len(relevant_votes) >= self._quorum_threshold:
                    self.logger.info(
                        f"Transaction {tx_id} reached quorum after retry!"
                    )
                    buffered_tx.status = TransactionStatus.FINALIZED
                    completed_txs.append(tx_id)
                    
                    # Complete the transaction
                    self._finalize_buffered_transaction(buffered_tx)
            
            # Remove completed transactions
            for tx_id in completed_txs:
                del self._buffered_transactions[tx_id]

    def _finalize_buffered_transaction(self, buffered_tx: BufferedTransaction) -> None:
        """Complete a buffered transaction that has reached quorum."""
        self.logger.info(f"Finalizing transaction {buffered_tx.order.order_id}")
        
        # Broadcast confirmation
        self.state.pending_transfer = buffered_tx.order
        self.broadcast_confirmation()

    def get_buffered_transactions(self) -> Dict[UUID, BufferedTransaction]:
        """Return dict of buffered transactions for inspection."""
        return self._buffered_transactions.copy()

    def get_transaction_status(self, order_id: UUID) -> Optional[TransactionStatus]:
        """Get the status of a transaction by its order ID."""
        if order_id in self._buffered_transactions:
            return self._buffered_transactions[order_id].status
        return None


__all__ = ["Client"]
