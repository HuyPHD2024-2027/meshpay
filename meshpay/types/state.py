"""Node state types for MeshPay."""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

from .common import Address, KeyPair, NodeType
from .transaction import (
    ConfirmationOrder,
    SignedTransferOrder,
    TransferOrder,
)
from .network import TokenBalance


@dataclass
class AccountOffchainState:
    """Account state in the FastPay system."""

    address: str
    balances: Dict[str, "TokenBalance"]  # Map of token_address -> balance
    # Sequence number tracking spending actions.
    sequence_number: int
    last_update: float
    # Whether we have signed a transfer for this sequence number already.
    pending_confirmation: SignedTransferOrder
    # All confirmed certificates as a sender.
    confirmed_transfers: Dict[str, ConfirmationOrder]

    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.last_update == 0:
            self.last_update = time.time()

        # Ensure *confirmed_transfers* is always a dict
        if self.confirmed_transfers is None:
            self.confirmed_transfers = {}

        # Ensure *balances* is always a dict
        if self.balances is None:
            self.balances = {}


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
    balance: int = 0
    # ── Opportunistic mesh relay fields ──
    neighbors: Dict[str, "Address"] = field(default_factory=dict)
    seen_order_ids: Set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.last_sync_time == 0:
            self.last_sync_time = time.time()


@dataclass
class ClientState:
    """Lightweight in-memory state for a FastPay client.

    Only the fields required for initiating basic transfers are included at this stage.  The class
    can be extended later with balance tracking, sequence numbers, certificates, and so on.
    """

    name: str
    address: Address
    secret: KeyPair = KeyPair("")
    sequence_number: int = 0
    committee: List["AuthorityState"] = field(default_factory=list)
    # Pending transfer (None when idle).
    pending_transfer: Optional[TransferOrder] = None
    # Transfer certificates that we have created ("sent").
    sent_certificates: List[SignedTransferOrder] = field(default_factory=list)
    # Known received certificates, indexed by sender and sequence number.
    received_certificates: Dict[Tuple[str, int], SignedTransferOrder] = field(default_factory=dict)
    # The known spendable balance.
    balance: int = 0
    stake: int = 0
    # ── Opportunistic mesh relay fields ──
    neighbors: Dict[str, "Address"] = field(default_factory=dict)
    seen_order_ids: Set[str] = field(default_factory=set)

    def next_sequence(self) -> int:
        """Return the current sequence number and increment the internal counter."""
        seq = self.sequence_number
        self.sequence_number += 1
        return seq


@dataclass
class GatewayState:
    """State maintained by a gateway node."""

    name: str
    address: Address
