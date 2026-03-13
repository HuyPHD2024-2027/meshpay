"""WiFi Authority Node implementation for MeshPay simulation.

Inherits shared mesh networking behavior from :class:`MeshMixin`.
"""

from __future__ import annotations

import threading
import time

from typing import Any, Dict, List, Optional, Set
from uuid import uuid4
from datetime import datetime

from mn_wifi.node import Station
from mn_wifi.services.core.config import SUPPORTED_TOKENS, settings

from meshpay.types import (
    AccountOffchainState,
    Address,
    AuthorityState,
    ConfirmationOrder,
    SignedTransferOrder,
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

from mn_wifi.services.core.config import DEFAULT_RELAY_TTL

from meshpay.transport.transport import NetworkTransport, TransportKind
from meshpay.transport.tcp import TCPTransport
from meshpay.transport.udp import UDPTransport
from meshpay.transport.wifiDirect import WiFiDirectTransport

from mn_wifi.metrics import MetricsCollector
from meshpay.logger.authorityLogger import AuthorityLogger
from mn_wifi.services.blockchain_client import BlockchainClient, TokenBalance

from meshpay.nodes.mesh_mixin import MeshMixin


DEFAULT_BALANCES = {
    "0x0000000000000000000000000000000000000000": TokenBalance(
        token_address="0x0000000000000000000000000000000000000000",
        token_symbol="XTZ",
        meshpay_balance=0.0,
        wallet_balance=0.0,
        total_balance=0.0,
        decimals=18,
    ),
    settings.wtz_contract_address: TokenBalance(
        token_address=settings.wtz_contract_address,
        token_symbol="WTZ",
        meshpay_balance=0.0,
        wallet_balance=0.0,
        total_balance=0.0,
        decimals=18,
    ),
    settings.usdt_contract_address: TokenBalance(
        token_address=settings.usdt_contract_address,
        token_symbol="USDT",
        meshpay_balance=0.0,
        wallet_balance=0.0,
        total_balance=0.0,
        decimals=6,
    ),
    settings.usdc_contract_address: TokenBalance(
        token_address=settings.usdc_contract_address,
        token_symbol="USDC",
        meshpay_balance=0.0,
        wallet_balance=0.0,
        total_balance=0.0,
        decimals=6,
    ),
}


class WiFiAuthority(MeshMixin, Station):
    """Authority node that runs on Mininet-WiFi host.

    Inherits mesh networking (neighbor management, discovery, relay) from
    :class:`MeshMixin`. 
    """

    def __init__(
        self,
        name: str,
        committee_members: Set[str],
        shard_assignments: Optional[Set[str]] = None,
        ip: str = "10.0.0.1/8",
        port: int = 8080,
        position: Optional[List[float]] = None,
        **params,
    ) -> None:
        """Initialize WiFi Authority node."""

        transport_kind = params.pop("transport_kind", TransportKind.TCP)
        transport: Optional[NetworkTransport] = params.pop("transport", None)

        default_params = {
            "ip": ip,
            "min_x": 0,
            "max_x": 200,
            "min_y": 0,
            "max_y": 150,
            "min_v": 5,
            "max_v": 10,
            "range": 20,
            "txpower": 20,
            "antennaGain": 5,
        }
        if position is not None:
             default_params['position'] = position
        default_params.update(params)

        super().__init__(name, **default_params)

        self.logger = AuthorityLogger(name)
        self.blockchain_client = BlockchainClient(self.logger)

        self.address = Address(
            node_id=name,
            ip_address=ip.split("/")[0],
            port=port,
            node_type=NodeType.AUTHORITY,
        )

        self.state = AuthorityState(
            name=name,
            address=self.address,
            shard_assignments=shard_assignments or set(),
            accounts={},
            committee_members=committee_members,
            last_sync_time=time.time(),
            authority_signature=f"signed_by_authority_{name}",
            stake=0,
        )

        self.performance_metrics = MetricsCollector()

        self._running = False
        self._epoch: int = 1  # committee epoch for Flash-Mesh BCB
        self._message_handler_thread: Optional[threading.Thread] = None
        self._blockchain_sync_thread: Optional[threading.Thread] = None

        # Init mesh mixin data structures
        self._init_mesh()

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

    # ------------------------------------------------------------------
    # Service lifecycle
    # ------------------------------------------------------------------

    def start_fastpay_services(self, enable_internet: bool = False) -> bool:
        """Boot-strap background processing threads and ready the chosen transport."""

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
        if enable_internet:
            self._blockchain_sync_thread = threading.Thread(
                target=self._blockchain_sync_loop,
                daemon=True,
            )
            self._blockchain_sync_thread.start()

        self._start_discovery_service()

        self.logger.info(f"Authority {self.name} started successfully")
        return True

    def stop_fastpay_services(self) -> None:
        """Stop the FastPay authority services."""
        self._running = False
        if hasattr(self.transport, "disconnect"):
            try:
                self.transport.disconnect()  # type: ignore[attr-defined]
            except Exception:
                pass

        if self._message_handler_thread:
            self._message_handler_thread.join(timeout=5.0)
        if self._blockchain_sync_thread:
            self._blockchain_sync_thread.join(timeout=5.0)
        self.logger.info(f"Authority {self.name} stopped")

    # Discovery loops inherited from MeshMixin

    async def update_account_balance(self) -> None:
        """Update account balance.

        Uses confirmed transfers in local state to build confirmation orders
        suitable for on-chain submission.
        """
        try:
            for account in self.state.accounts.keys():
                confirmed_transfers = self.state.accounts[account].confirmed_transfers.values()
                for transfer in confirmed_transfers:
                    iso_timestamp = transfer.transfer_order.timestamp
                    dt = datetime.fromisoformat(str(iso_timestamp).replace("Z", "+00:00"))
                    unix_timestamp = int(dt.timestamp())

                    for token_symbol, token_config in SUPPORTED_TOKENS.items():
                        if token_config["address"] == transfer.transfer_order.token_address:
                            parsed_amount = int(
                                transfer.transfer_order.amount * (10 ** token_config["decimals"])  # noqa: W503
                            )
                            break

                    transfer_order = (
                        str(transfer.transfer_order.order_id),
                        str(transfer.transfer_order.sender),
                        str(transfer.transfer_order.recipient),
                        parsed_amount,
                        str(transfer.transfer_order.token_address),
                        int(transfer.transfer_order.sequence_number),
                        unix_timestamp,
                        str(transfer.transfer_order.signature or "0x"),
                    )

                    authority_signatures = [str(sig or "0x") for sig in transfer.authority_signatures]
                    confirmation_order = (transfer_order, authority_signatures)
                    await self.blockchain_client.update_account_balance(confirmation_order)

        except Exception as e:
            self.logger.error(f"Error updating account balance: {e}")

    async def sync_account_from_blockchain(self) -> None:
        """Sync all registered accounts from blockchain using blockchain client."""
        try:
            all_accounts_data = await self.blockchain_client.sync_all_accounts()
            for account_address, balances in all_accounts_data.items():
                await self._update_local_account_state(account_address, balances)
        except Exception as e:
            self.logger.error(f"Error syncing accounts from blockchain: {e}")

    async def _update_local_account_state(self, account_address: str, balances: Dict[str, TokenBalance]) -> None:
        """Update local account state with blockchain data."""
        try:
            account_info = await self.blockchain_client.get_account_info(account_address)
            if not account_info or not account_info.is_registered:
                self.logger.warning(f"Account {account_address} not registered on blockchain")
                return

            if account_address not in self.state.accounts:
                self.state.accounts[account_address] = AccountOffchainState(
                    address=account_address,
                    balances=balances,
                    last_update=time.time(),
                    pending_confirmation=None,
                    confirmed_transfers={},
                    sequence_number=0,
                )
                self.logger.info(f"Created new account state for {account_address}")
            else:
                account = self.state.accounts[account_address]
                account.balances = balances
                account.last_update = time.time()
                self.logger.debug(f"Updated account state for {account_address}")

        except Exception as e:
            self.logger.error(f"Error updating local account state for {account_address}: {e}")

    def handle_transfer_order(self, transfer_order: TransferOrder) -> TransferResponseMessage:
        """Handle transfer order from client."""
        start_time = time.time()
        try:
            if not self._validate_transfer_order(transfer_order):
                return TransferResponseMessage(
                    transfer_order=transfer_order,
                    success=False,
                    error_message="Invalid transfer order",
                    authority_signature=self.state.authority_signature,
                )

            self.state.accounts[transfer_order.sender].pending_confirmation = SignedTransferOrder(
                order_id=transfer_order.order_id,
                transfer_order=transfer_order,
                authority_signature=self.state.authority_signature,
                timestamp=time.time(),
            )

            if transfer_order.recipient not in self.state.accounts:
                self.state.accounts[transfer_order.recipient] = AccountOffchainState(
                    address=transfer_order.recipient,
                    balances=DEFAULT_BALANCES,
                    sequence_number=0,
                    last_update=time.time(),
                    pending_confirmation={},
                    confirmed_transfers={},
                )

            self.performance_metrics.record_transaction()
            validation_time = (time.time() - start_time) * 1000
            self.performance_metrics.record_validation_time(validation_time)

            return TransferResponseMessage(
                transfer_order=transfer_order,
                success=True,
                authority_signature=self.state.authority_signature,
                error_message=None,
            )

        except Exception as e:
            self.logger.error(f"Error handling transfer order: {e}")
            self.performance_metrics.record_error()
            return TransferResponseMessage(
                transfer_order=transfer_order,
                success=False,
                error_message=f"Internal error: {str(e)}",
            )

    def handle_confirmation_order(self, confirmation_order: ConfirmationOrder) -> bool:
        """Handle confirmation order from committee."""
        try:
            if not self._validate_confirmation_order(confirmation_order):
                return False

            account = self.state.accounts[confirmation_order.transfer_order.sender]
            account.confirmed_transfers[str(confirmation_order.order_id)] = confirmation_order
            account.pending_confirmation = None
            confirmation_order.status = TransactionStatus.CONFIRMED

            transfer = confirmation_order.transfer_order

            sender = self.state.accounts.setdefault(
                transfer.sender,
                AccountOffchainState(
                    address=transfer.sender,
                    balances=DEFAULT_BALANCES,
                    sequence_number=0,
                    last_update=time.time(),
                    pending_confirmation=None,
                    confirmed_transfers={},
                ),
            )
            recipient = self.state.accounts.setdefault(
                transfer.recipient,
                AccountOffchainState(
                    address=transfer.recipient,
                    balances=DEFAULT_BALANCES,
                    sequence_number=0,
                    last_update=time.time(),
                    pending_confirmation=None,
                    confirmed_transfers={},
                ),
            )

            sender.balances[transfer.token_address].meshpay_balance -= transfer.amount
            sender.sequence_number += 1
            sender.last_update = time.time()

            recipient.balances[transfer.token_address].meshpay_balance += transfer.amount
            recipient.last_update = time.time()

            self.performance_metrics.record_success()
            self.logger.info(f"Confirmation order {confirmation_order.order_id} processed")
            return True

        except Exception as e:
            self.logger.error(f"Error handling confirmation order: {e}")
            self.performance_metrics.record_error()
            return False

    def get_account_balance(self, account_address: str) -> Optional[int]:
        """Get account balance or None if not found."""
        account = self.state.accounts.get(account_address)
        return account.balances if account else None

    def get_performance_stats(self) -> Dict[str, Any]:
        """Get performance statistics as a dictionary."""
        return self.performance_metrics.get_stats()

    def trigger_blockchain_sync(self) -> None:
        """Manually trigger blockchain sync for all registered accounts."""
        if not self._running:
            self.logger.warning("Authority not running, cannot trigger blockchain sync")
            return

        self.logger.info("Manually triggering blockchain sync")
        try:
            import asyncio
            asyncio.run(self.sync_account_from_blockchain())
        except Exception as e:
            self.logger.error(f"Error during manual blockchain sync: {e}")
        self.logger.info(f"Manual blockchain sync completed for {len(self.state.accounts)} accounts")

    def _validate_transfer_order(self, transfer_order: TransferOrder) -> bool:
        """Validate a transfer order."""
        if transfer_order.amount <= 0:
            self.logger.debug(f"Validation failed: amount <= 0 ({transfer_order.amount})")
            return False
        if transfer_order.sender == transfer_order.recipient:
            self.logger.debug("Validation failed: sender == recipient")
            return False
        if not transfer_order.sender or not transfer_order.recipient:
            self.logger.debug("Validation failed: empty sender or recipient")
            return False

        # Sender must exist in local state
        sender_account = self.state.accounts.get(transfer_order.sender)
        if sender_account is None:
            self.logger.debug(
                f"Validation failed: sender '{transfer_order.sender}' not in accounts "
                f"(known: {list(self.state.accounts.keys())})"
            )
            return False

        # Sequence number must be monotonically increasing
        try:
            if int(transfer_order.sequence_number) < int(sender_account.sequence_number):
                self.logger.debug(
                    f"Validation failed: seq {transfer_order.sequence_number} < "
                    f"account seq {sender_account.sequence_number}"
                )
                return False
        except Exception:
            self.logger.debug("Validation failed: sequence_number conversion error")
            return False

        # Sender must have a tracked balance for the token
        token_balance = sender_account.balances.get(transfer_order.token_address)
        if token_balance is None:
            self.logger.debug(
                f"Validation failed: token '{transfer_order.token_address}' not in "
                f"sender balances (known: {list(sender_account.balances.keys())})"
            )
            return False

        try:
            meshpay_balance = float(token_balance.meshpay_balance)
        except Exception:
            self.logger.debug("Validation failed: meshpay_balance conversion error")
            return False

        if meshpay_balance < float(transfer_order.amount):
            self.logger.debug(
                f"Validation failed: insufficient balance "
                f"({meshpay_balance} < {transfer_order.amount})"
            )
            return False
        return True

    def _validate_confirmation_order(self, confirmation_order: ConfirmationOrder) -> bool:
        """Validate a confirmation order."""
        if not self._validate_transfer_order(confirmation_order.transfer_order):
            return False

        account = self.state.accounts.get(confirmation_order.transfer_order.sender)
        if (
            account
            and account.confirmed_transfers
            and confirmation_order.order_id in account.confirmed_transfers
        ):
            return False

        if account.pending_confirmation and str(account.pending_confirmation.order_id) != str(
            confirmation_order.transfer_order.order_id
        ):
            return False
        return True

    def _message_handler_loop(self) -> None:
        """Main message handling loop."""
        while self._running:
            try:
                message = self.transport.receive_message(timeout=1.0)
                if message:
                    self._process_message(message)
            except Exception as e:
                self.logger.error(f"Error in message handler loop: {e}")
                time.sleep(0.1)

    def _process_message(self, message: Message) -> None:
        """Process incoming raw network packets directly from the transport layer.
        
        This handler catches two types of communication:
        1. Pure DTN Routing (Mesh): The foundation of the mesh network. Summaries 
           and bundles wrapped in ROUTING_MESSAGEs.
        2. Direct Transport (Legacy/Internet): If a client is connected directly to 
           this authority (e.g., via TCP/Internet), bypass the buffer and process 
           immediately.
        """
        try:
            # ── 1. Pure DTN / Mesh Network Flow ──
            if message.message_type == MessageType.ROUTING_MESSAGE:
                # Hand this over to the DTN bridge (MeshMixin) which parses it.
                # If it's data, the DTN bridge will eventually call `on_dtn_bundle_received`.
                self._handle_routing_message(message)

            # ── 2. Direct Internet / Legacy Flow ──
            elif message.message_type == MessageType.TRANSFER_REQUEST:
                # Received directly from a client. Process and instantly reply directly.
                request = TransferRequestMessage.from_payload(message.payload)
                response = self.handle_transfer_order(request.transfer_order)
                response_message = Message(
                    message_id=uuid4(),
                    message_type=MessageType.TRANSFER_RESPONSE,
                    sender=self.address,
                    recipient=message.sender,
                    timestamp=time.time(),
                    payload=response.to_payload(),
                )
                self.transport.send_message(response_message, message.sender)

            elif message.message_type == MessageType.CONFIRMATION_REQUEST:
                # Received directly
                request = ConfirmationRequestMessage.from_payload(message.payload)
                self.handle_confirmation_order(request.confirmation_order)

        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    def _blockchain_sync_loop(self) -> None:
        """Periodic blockchain synchronization loop."""
        first_run = True
        while self._running:
            try:
                if not first_run:
                    time.sleep(settings.blockchain_sync_interval)

                if not self._running:
                    break

                try:
                    import asyncio
                    asyncio.run(self.sync_account_from_blockchain())
                except Exception as e:
                    self.logger.error(f"Error in blockchain sync cycle: {e}")

                first_run = False

            except Exception as e:
                self.logger.error(f"Error in blockchain sync loop: {e}")
                time.sleep(10)

    def on_dtn_bundle_received(self, item) -> None:
        """Application hook triggered by the DTN layer when a new bundle arrives.
        
        Unlike `_process_message` which sees all raw packets, this is ONLY called 
        when the Epidemic routing protocol successfully downloads a new transaction 
        from a neighbor and saves it to the `message_buffer`.

        As an Authority, our job is to ACT on these buffered transactions:
        - If it's a TRANSFER_REQUEST, we validate it, sign it, and inject the 
          resulting TRANSFER_RESPONSE *back* into the buffer so the epidemic 
          routing will carry it back to the client.
        - If it's a CONFIRMATION_REQUEST, we finalize the transaction locally.
        """
        from meshpay.messages import MessageType, TransferRequestMessage
        from meshpay.types.transaction import MessageBufferItem
        from mn_wifi.services.core.config import DEFAULT_RELAY_TTL

        if item.message_type == MessageType.TRANSFER_REQUEST.value:
            request = TransferRequestMessage.from_payload(item.payload)
            
            # Process the transaction and generate our signature/response
            response = self.handle_transfer_order(request.transfer_order)

            # Important: Inject response back into message_buffer for epidemic spread
            # We don't send it directly over the transport because we don't know where 
            # the client is. Epidemic routing will spread it until it reaches them.
            resp_msg_id = f"{request.transfer_order.order_id}:resp:{self.name}"
            
            if resp_msg_id not in self.message_buffer:
                self.message_buffer[resp_msg_id] = MessageBufferItem(
                    message_id=resp_msg_id,
                    message_type=MessageType.TRANSFER_RESPONSE.value,
                    payload=response.to_payload(),
                    sender_id=self.name,
                    ttl=DEFAULT_RELAY_TTL,
                )
                # Tell the DTN protocol we have a new item ready to spread
                self.routing_protocol.on_message_added_to_buffer(
                    resp_msg_id, self.message_buffer
                )

        elif item.message_type == MessageType.CONFIRMATION_REQUEST.value:
            request = ConfirmationRequestMessage.from_payload(item.payload)
            self.handle_confirmation_order(request.confirmation_order)
