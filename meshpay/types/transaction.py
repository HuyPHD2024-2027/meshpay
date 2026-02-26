"""Transaction-related types for MeshPay."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from dataclasses import dataclass, field

from .common import AuthorityName, KeyPair, TransactionStatus


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
class BufferedTransaction:
    """Transaction buffered on client awaiting quorum.

    When a client broadcasts a transfer and doesn't receive enough
    signatures to form a quorum, the transaction is buffered and
    retried periodically until quorum is reached.

    For DTN store-carry-forward, relayed messages from other nodes
    can also be stored here with ``is_relay=True``.
    """

    order: TransferOrder
    signatures_received: Dict[str, str] = field(default_factory=dict)  # auth_name -> signature
    signatures_required: int = 0
    created_at: float = 0.0
    last_retry: float = 0.0
    retry_count: int = 0
    status: TransactionStatus = TransactionStatus.BUFFERED
    # DTN relay fields
    is_relay: bool = False
    relay_metadata: Optional[Dict[str, Any]] = None

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
