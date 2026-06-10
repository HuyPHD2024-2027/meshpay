#!/usr/bin/env python3

from __future__ import annotations

import time
from typing import Dict, List, Optional

from mn_wifi.node import Station

from meshpay.offline.crypto import sign_payload, verify_signature
from meshpay.types.common import Address, NodeType, TransactionStatus
from meshpay.types.state import AccountOffchainState, AuthorityState
from meshpay.types.transaction import (
    ConfirmationOrder,
    SignedTransferOrder,
    TransferOrder,
)


class Authority(Station):
    """Offline MeshPay authority.

    The authority tracks account state using AccountOffchainState.

    No token support in the first clean version.
    Each account has one integer MeshPay balance.
    """

    def __init__(
        self,
        name: str,
        committee: List[str] | None = None,
        initial_balances: Dict[str, int] | None = None,
        ip: str = "10.0.0.1/24",
        port: int = 8000,
        **params,
    ) -> None:
        super().__init__(name, ip=ip, **params)

        self.name = name
        self.committee = committee or []

        self.address = Address(
            node_id=name,
            ip_address=ip.split("/")[0],
            port=port,
            node_type=NodeType.AUTHORITY,
        )

        self.state = AuthorityState(
            name=name,
            address=self.address,
            shard_assignments=set(),
            accounts={},
            committee_members=set(self.committee),
            authority_signature=f"authority:{name}",
            last_sync_time=time.time(),
            stake=0,
        )

        for account_address, balance in (initial_balances or {}).items():
            self.register_account(account_address, balance)

    def register_account(self, account_address: str, balance: int = 0) -> None:
        """Register or reset an account in the authority's off-chain state."""

        self.state.accounts[account_address] = AccountOffchainState(
            address=account_address,
            balance=balance,
            last_update=time.time(),
            pending_confirmation=None,
            confirmed_transfers={},
            sequence_number=0,
        )

    def handle_transfer(
        self,
        order: TransferOrder,
    ) -> Optional[SignedTransferOrder]:
        """Validate and sign a TransferOrder."""

        if not self._validate_transfer(order):
            return None

        sender_account = self.state.accounts[order.sender]
        existing_pending = sender_account.pending_confirmation

        if existing_pending is not None:
            existing_order = existing_pending.transfer_order

            if str(existing_order.order_id) == str(order.order_id):
                return existing_pending

            return None

        signature = sign_payload(self.name, order.signing_dict())

        signed = SignedTransferOrder(
            order_id=order.order_id,
            transfer_order=order,
            authority_signature={self.name: signature},
            timestamp=time.time(),
        )

        sender_account.pending_confirmation = signed
        sender_account.last_update = time.time()

        return signed

    def handle_confirmation(self, confirmation: ConfirmationOrder) -> bool:
        """Apply a confirmed transfer to authority account state."""

        order = confirmation.transfer_order
        order_id = str(order.order_id)

        if not self._validate_confirmation(confirmation):
            return False

        sender_account = self.state.accounts[order.sender]
        recipient_account = self._get_or_create_account(order.recipient)

        if order_id in sender_account.confirmed_transfers:
            return False

        sender_account.debit(order.amount)
        sender_account.set_sequence(order.sequence_number)
        sender_account.pending_confirmation = None
        sender_account.confirmed_transfers[order_id] = confirmation
        sender_account.last_update = time.time()

        recipient_account.credit(order.amount)

        confirmation.status = TransactionStatus.CONFIRMED

        return True

    def on_payment_object(self, obj) -> List[object]:
        """Handle a decoded payment object.

        Returns payment objects that should be injected into DTN.
        """

        if isinstance(obj, TransferOrder):
            signed = self.handle_transfer(obj)
            return [signed] if signed else []

        if isinstance(obj, ConfirmationOrder):
            self.handle_confirmation(obj)
            return []

        return []

    def balance_of(self, account_address: str) -> int:
        account = self.state.accounts.get(account_address)

        if account is None:
            return 0

        return account.balance

    def _get_or_create_account(self, account_address: str) -> AccountOffchainState:
        account = self.state.accounts.get(account_address)

        if account is not None:
            return account

        self.register_account(account_address, balance=0)
        return self.state.accounts[account_address]

    def _validate_transfer(self, order: TransferOrder) -> bool:
        if order.amount <= 0:
            return False

        if order.sender == order.recipient:
            return False

        if not verify_signature(order.sender, order.signing_dict(), order.signature):
            return False

        sender_account = self.state.accounts.get(order.sender)

        if sender_account is None:
            return False

        if not sender_account.can_debit(order.amount):
            return False

        if order.sequence_number <= sender_account.sequence_number:
            return False

        existing_pending = sender_account.pending_confirmation

        if existing_pending is not None:
            existing_order = existing_pending.transfer_order

            if str(existing_order.order_id) != str(order.order_id):
                return False

        return True

    def _validate_confirmation(self, confirmation: ConfirmationOrder) -> bool:
        order = confirmation.transfer_order
        sender_account = self.state.accounts.get(order.sender)

        if sender_account is None:
            return False

        if not sender_account.can_debit(order.amount):
            return False

        if order.sequence_number <= sender_account.sequence_number:
            return False

        if not confirmation.authority_signatures:
            return False

        return True