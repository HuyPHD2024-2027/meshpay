"""Base types and data structures for MeshPay offline payment system."""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Dict, Optional, Set, NewType, List, Tuple
from uuid import UUID, uuid4
from dataclasses import dataclass, field


KeyPair = NewType("KeyPair", str)
AuthorityName = str
ClientAddress = str
MessagePayload = Dict[str, Any] 

class NodeType(Enum):
    """Type of node in the network."""
    
    AUTHORITY = "authority"
    CLIENT = "client"
    GATEWAY = "gateway"


class TransactionStatus(Enum):
    """Status of a transaction."""
    
    PENDING = "pending"
    BUFFERED = "buffered"  # Awaiting quorum, will retry
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    FINALIZED = "finalized"


class BCBPriorityClass(Enum):
    """Traffic priority classes for BCB settlement.

    Used by the SDN controller / QoS manager to classify packets into
    strict-priority queues.
    """

    FASTPAY_BCB = 0     # votes, certificates — highest priority
    PAYMENT_DATA = 1    # transfer payloads, balance queries
    BEST_EFFORT = 2     # logs, telemetry, model updates



@dataclass
class Address:
    """Network address for a node."""
    
    node_id: str
    ip_address: str
    port: int
    node_type: NodeType
    
    def __str__(self) -> str:
        """Return string representation of address."""
        return f"{self.node_type.value}:{self.node_id}@{self.ip_address}:{self.port}"


@dataclass
class TransferOrder:
    """Transfer order from client to authority.

    In the Flash-Mesh BCB model this doubles as the *Lock* — the client's
    signed spend intent.  Replay protection is provided by the monotonic
    ``sequence_number``; ``epoch`` tracks the committee epoch and ``ttl``
    limits how long the lock remains valid.
    """
    
    order_id: UUID
    sender: str
    recipient: str
    token_address: str
    amount: int
    sequence_number: int
    timestamp: float
    signature: Optional[str] = None
    epoch: int = 0           # committee epoch
    ttl: float = 30.0        # seconds until lock expires
    
    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.order_id is None:
            self.order_id = uuid4()
        if self.timestamp == 0:
            self.timestamp = time.time()

@dataclass
class SignedTransferOrder:
    """Signed transfer order from authority to client."""
    
    order_id: UUID
    transfer_order: TransferOrder
    authority_signature: Dict[AuthorityName, str]
    timestamp: float

    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.order_id is None:
            self.order_id = uuid4()
        if self.timestamp == 0:
            self.timestamp = time.time()

@dataclass
class ConfirmationOrder:
    """Confirmation order between authorities."""
    
    order_id: UUID
    transfer_order: TransferOrder
    authority_signatures: List[str]
    timestamp: float
    status: TransactionStatus = TransactionStatus.PENDING
    
    def __post_init__(self) -> None:
        """Initialise defaults and sanitise nested fields.

        When deserialised from JSON, ``transfer_order`` may arrive as a plain
        dictionary.  Here we convert it back to a :class:`TransferOrder` so
        that attribute access (*transfer_order.sender* etc.) works reliably
        across the code-base.
        """

        from uuid import UUID  # local import to avoid circularity

        # Convert *transfer_order* to dataclass if needed ------------------
        if isinstance(self.transfer_order, dict):
            raw = self.transfer_order  # type: ignore[assignment]

            # Ensure UUID typed field
            if isinstance(raw.get("order_id"), str):
                raw["order_id"] = UUID(raw["order_id"])

            self.transfer_order = TransferOrder(**raw)  # type: ignore[assignment]

        # Sanitise *order_id* ---------------------------------------------
        if isinstance(self.order_id, str):  # when reconstructed poorly
            self.order_id = UUID(self.order_id)

        # Timestamp default ------------------------------------------------
        if self.timestamp == 0:
            self.timestamp = time.time()

@dataclass
class TokenBalance:
    """Token balance information."""
    token_symbol: str
    token_address: str
    wallet_balance: float
    meshpay_balance: float
    total_balance: float
    decimals: int

@dataclass
class AccountOffchainState:
    """Account state in the FastPay system."""
    
    address: str
    balances: Dict[str, TokenBalance]  # Map of token_address -> balance
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
    
    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.last_sync_time == 0:
            self.last_sync_time = time.time()

@dataclass
class NetworkMetrics:
    """Network performance metrics."""
    
    latency: float
    bandwidth: float
    packet_loss: float
    connectivity_ratio: float
    last_update: float
    
    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.last_update == 0:
            self.last_update = time.time()

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
    # Normally, `sent_certificates` should contain one certificate for each index in `0..next_sequence_number`.
    sent_certificates: List[SignedTransferOrder] = field(default_factory=list)
    # Known received certificates, indexed by sender and sequence number.
    # TODO: API to search and download yet unknown `received_certificates`.
    received_certificates: Dict[Tuple[str, int], SignedTransferOrder] = field(default_factory=dict)
    # The known spendable balance (including a possible initial funding, excluding unknown sent
    # or received certificates).
    balance: int = 0
    stake: int = 0

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


@dataclass
class BufferedTransaction:
    """Transaction buffered on client awaiting quorum.
    
    When a client broadcasts a transfer and doesn't receive enough
    signatures to form a quorum, the transaction is buffered and
    retried periodically until quorum is reached.
    """
    
    order: TransferOrder
    signatures_received: Dict[str, str] = field(default_factory=dict)  # auth_name -> signature
    signatures_required: int = 0
    created_at: float = 0.0
    last_retry: float = 0.0
    retry_count: int = 0
    status: TransactionStatus = TransactionStatus.BUFFERED
    
    def __post_init__(self) -> None:
        """Initialize timestamps."""
        if self.created_at == 0.0:
            self.created_at = time.time()
        if self.last_retry == 0.0:
            self.last_retry = self.created_at
    
    @property
    def has_quorum(self) -> bool:
        """Check if enough signatures have been collected."""
        return len(self.signatures_received) >= self.signatures_required
    
    def add_signature(self, authority_name: str, signature: str) -> bool:
        """Add a signature from an authority. Returns True if quorum now reached."""
        if authority_name not in self.signatures_received:
            self.signatures_received[authority_name] = signature
        return self.has_quorum
