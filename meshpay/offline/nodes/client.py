#!/usr/bin/env python3

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from mn_wifi.node import Station

from meshpay.offline.virtual_accounts import make_account_id
from meshpay.offline.crypto import sign_payload, verify_signature
from meshpay.offline.quorum import has_weighted_quorum, verify_authority_vote
from meshpay.offline.weighted_quorum import WeightRegistry
from meshpay.offline.wallet import Wallet
from meshpay.types.common import TransactionStatus
from meshpay.types.transaction import (
    ConfirmationOrder,
    SignedTransferOrder,
    TransferOrder,
)


class Client(Station):
    """Offline MeshPay client.

    The client processes one outgoing transfer at a time.

    State:
        pending_transfer:
            The current TransferOrder created by this client.

        signed_transfer_orders:
            Authority signatures collected for the current pending transfer.

        confirmation_orders:
            ConfirmationOrders accepted or created by this client.
    """

    def __init__(
        self,
        name: str,
        committee: List[str] | None = None,
        initial_balance: int = 100,
        accounts_per_station: int = 0,
        weight_state_path: str | Path = "weighted_quorum_state.json",
        weight_epoch_size: int = 100,
        max_voting_power_share: float = 0.30,
        **params,
    ) -> None:
        super().__init__(name, **params)

        self.name = name
        self.committee = committee or []
        self.weight_registry = WeightRegistry(
            weight_state_path,
            self.committee,
            epoch_size=weight_epoch_size,
            max_power_share=max_voting_power_share,
        )
        self.weight_registry.initialize()
        self._lock = threading.RLock()

        # Legacy physical-station account, useful for interactive testing:
        #   pay sta1 sta3 10
        self.accounts: Dict[str, Wallet] = {
            name: Wallet(owner=name, balance=initial_balance)
        }

        # Virtual logical accounts, useful for large benchmarks:
        #   sta1/u00001
        #   sta1/u00002
        #   ...
        for index in range(1, accounts_per_station + 1):
            account_id = make_account_id(name, index)
            self.accounts[account_id] = Wallet(
                owner=account_id,
                balance=initial_balance,
            )

        # One pending outgoing transfer per logical account.
        self.pending_by_account: Dict[str, str] = {}

        # Many pending transfers per physical station.
        self.pending_transfers: Dict[str, TransferOrder] = {}

        # order_id -> authority_name -> SignedTransferOrder
        self.signed_transfer_orders: Dict[str, Dict[str, SignedTransferOrder]] = {}

        self.confirmation_orders: Dict[str, ConfirmationOrder] = {}

    def pay(
        self,
        recipient: str,
        amount: int,
        sender_account: str | None = None,
    ) -> TransferOrder:
        """Create one outgoing TransferOrder.

        sender_account:
            None means use the physical station account, e.g. "sta1".
            Otherwise use a hosted virtual account, e.g. "sta1/u00042".
        """
        with self._lock:
            sender_account = sender_account or self.name

            if sender_account not in self.accounts:
                raise ValueError(f"{self.name} does not host account {sender_account}")

            if amount <= 0:
                raise ValueError("amount must be positive")

            if sender_account in self.pending_by_account:
                raise RuntimeError(f"account already has a pending transfer: {sender_account}")

            wallet = self.accounts[sender_account]

            if wallet.balance < amount:
                raise ValueError(f"insufficient balance for {sender_account}")

            sequence = wallet.next_sequence()

            order = TransferOrder(
                order_id=uuid4(),
                sender=sender_account,
                recipient=recipient,
                amount=amount,
                sequence_number=sequence,
                timestamp=time.time(),
                signature=None,
            )

            order.signature = sign_payload(sender_account, order.signing_dict())

            order_id = str(order.order_id)

            self.pending_by_account[sender_account] = order_id
            self.pending_transfers[order_id] = order
            self.signed_transfer_orders[order_id] = {}

            return order

    def handle_signed_transfer(
        self,
        signed: SignedTransferOrder,
    ) -> Optional[ConfirmationOrder]:
        """Collect authority signatures and form ConfirmationOrder on quorum."""
        with self._lock:
            order = signed.transfer_order
            order_id = str(order.order_id)

            pending = self.pending_transfers.get(order_id)

            if pending is None:
                return None

            if order.sender not in self.accounts:
                return None

            if order.sequence_number != pending.sequence_number:
                return None

            signatures_for_order = self.signed_transfer_orders.setdefault(order_id, {})

            vote = signed.authority_vote
            snapshot = self.weight_registry.snapshot_for_epoch(vote.epoch)
            if snapshot is None or not verify_authority_vote(order, vote, snapshot):
                return None
            if signatures_for_order:
                existing_epoch = next(iter(signatures_for_order.values())).authority_vote.epoch
                if existing_epoch != vote.epoch:
                    return None
            signatures_for_order[vote.authority] = signed

            votes = [item.authority_vote for item in signatures_for_order.values()]
            if not has_weighted_quorum(pending, votes, snapshot):
                return None

            confirmation = ConfirmationOrder(
                order_id=pending.order_id,
                transfer_order=pending,
                authority_votes=votes,
                timestamp=time.time(),
                quorum_epoch=snapshot.epoch,
                total_weight_units=snapshot.total_weight_units,
                committee_digest=snapshot.committee_digest,
                status=TransactionStatus.CONFIRMED,
            )

            wallet = self.accounts[pending.sender]
            wallet.debit(pending.amount)

            self.confirmation_orders[order_id] = confirmation
            self.weight_registry.record_finalization(
                order_id,
                [str(vote.authority) for vote in votes],
            )

            self.pending_transfers.pop(order_id, None)
            self.signed_transfer_orders.pop(order_id, None)
            self.pending_by_account.pop(pending.sender, None)

            return confirmation

    def handle_confirmation(self, confirmation: ConfirmationOrder) -> bool:
        """Apply a ConfirmationOrder if this station hosts the recipient account."""
        with self._lock:
            order = confirmation.transfer_order
            order_id = str(order.order_id)

            if order.recipient not in self.accounts:
                return False

            if order_id in self.confirmation_orders:
                return False

            snapshot = self.weight_registry.snapshot_for_epoch(confirmation.quorum_epoch)
            if snapshot is None:
                return False
            if (
                confirmation.total_weight_units != snapshot.total_weight_units
                or confirmation.committee_digest != snapshot.committee_digest
                or not confirmation.authority_votes
            ):
                return False
            if any(
                not verify_authority_vote(order, vote, snapshot)
                for vote in confirmation.authority_votes
            ):
                return False
            if not has_weighted_quorum(order, confirmation.authority_votes, snapshot):
                return False

            wallet = self.accounts[order.recipient]
            wallet.credit(order.amount)

            self.confirmation_orders[order_id] = confirmation

            return True

    def on_payment_object(self, obj) -> List[object]:
        """Handle a decoded payment object.

        Returns payment objects that should be injected into DTN.
        """

        if isinstance(obj, SignedTransferOrder):
            confirmation = self.handle_signed_transfer(obj)
            return [confirmation] if confirmation else []

        if isinstance(obj, ConfirmationOrder):
            self.handle_confirmation(obj)
            return []

        return []

    @property
    def balance(self) -> int:
        """Total balance across all accounts hosted by this physical station."""

        return sum(wallet.balance for wallet in self.accounts.values())


    def account_balance(self, account_id: str) -> int:
        wallet = self.accounts.get(account_id)

        if wallet is None:
            return 0

        return wallet.balance


    def hosted_accounts(self, virtual_only: bool = False) -> List[str]:
        accounts = list(self.accounts.keys())

        if virtual_only:
            return [account for account in accounts if "/" in account]

        return accounts


    def can_pay_from(self, account_id: str, amount: int) -> bool:
        wallet = self.accounts.get(account_id)

        if wallet is None:
            return False

        if account_id in self.pending_by_account:
            return False

        return wallet.balance >= amount
