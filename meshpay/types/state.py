"""Node state types for MeshPay."""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

from .common import Address, KeyPair
from .transaction import (
    ConfirmationOrder,
    SignedTransferOrder,
    TransferOrder,
)


@dataclass
class AccountOffchainState:
    """Basic off-chain account state used by MeshPay authorities.

    First clean version:

        balance: int

    No token address, token symbol, decimals, wallet balance, or total balance.
    """

    address: str
    balance: int
    sequence_number: int
    last_update: float
    pending_confirmation: Optional[SignedTransferOrder]
    confirmed_transfers: Dict[str, ConfirmationOrder]

    def __post_init__(self) -> None:
        if self.last_update == 0:
            self.last_update = time.time()

        if self.confirmed_transfers is None:
            self.confirmed_transfers = {}

        self.balance = int(self.balance)
        self.sequence_number = int(self.sequence_number)

    def can_debit(self, amount: int) -> bool:
        return amount > 0 and self.balance >= amount

    def debit(self, amount: int) -> None:
        if amount <= 0:
            raise ValueError("amount must be positive")

        if not self.can_debit(amount):
            raise ValueError(f"insufficient balance for {self.address}")

        self.balance -= amount
        self.last_update = time.time()

    def credit(self, amount: int) -> None:
        if amount <= 0:
            raise ValueError("amount must be positive")

        self.balance += amount
        self.last_update = time.time()

    def set_sequence(self, sequence_number: int) -> None:
        self.sequence_number = max(self.sequence_number, int(sequence_number))
        self.last_update = time.time()


@dataclass
class AuthorityState:
    """State maintained by an authority node."""

    name: str
    address: Address
    shard_assignments: Set[str]
    accounts: Dict[str, AccountOffchainState]
    committee_members: Set[str]
    authority_signature: Optional[str] = None
    last_sync_time: float = 0.0
    stake: int = 0
    tx_count: int = 0
    current_weight: int = 0
    balance: int = 0
    neighbors: Dict[str, "Address"] = field(default_factory=dict)
    seen_order_ids: Set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.last_sync_time == 0:
            self.last_sync_time = time.time()

        if self.accounts is None:
            self.accounts = {}


@dataclass
class ClientState:
    """Lightweight in-memory state for a MeshPay client."""

    name: str
    address: Address
    secret: KeyPair = KeyPair("")
    sequence_number: int = 0
    committee: List["AuthorityState"] = field(default_factory=list)
    pending_transfer: Optional[TransferOrder] = None
    sent_certificates: List[SignedTransferOrder] = field(default_factory=list)
    received_certificates: Dict[Tuple[str, int], SignedTransferOrder] = field(default_factory=dict)
    balance: int = 0
    stake: int = 0
    neighbors: Dict[str, "Address"] = field(default_factory=dict)
    seen_order_ids: Set[str] = field(default_factory=set)

    def next_sequence(self) -> int:
        seq = self.sequence_number
        self.sequence_number += 1
        return seq


@dataclass
class GatewayState:
    """State maintained by a gateway node."""

    name: str
    address: Address
