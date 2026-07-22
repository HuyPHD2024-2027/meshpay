"""Transaction-related types for MeshPay."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID, uuid4

from .common import AuthorityName, TransactionStatus


PAYMENT_APP = "meshpay.offline"
COMPACT_PAYLOAD_VERSION = 3
OrderLookup = Callable[[str], Optional["TransferOrder"]]


def _status_from_value(value: Any) -> TransactionStatus:
    if isinstance(value, TransactionStatus):
        return value

    if isinstance(value, str):
        return TransactionStatus(value)

    return TransactionStatus.PENDING


def _payload_data(payload: Dict[str, Any], expected_type: str) -> Dict[str, Any]:
    if payload.get("app") != PAYMENT_APP:
        raise ValueError(f"invalid app for {expected_type} payload")

    if payload.get("type") != expected_type:
        raise ValueError(f"invalid payload type for {expected_type}")

    data = payload.get("data")

    if not isinstance(data, dict):
        raise ValueError(f"invalid data for {expected_type}")

    return data


@dataclass
class TransferOrder:
    """Transfer order from client to authority.

    First clean version:
        - no token address
        - no token symbol
        - no decimals
        - amount is a plain integer MeshPay balance amount
    """

    order_id: UUID
    sender: str
    recipient: str
    amount: int
    sequence_number: int
    timestamp: float
    signature: Optional[str] = None
    epoch: int = 0
    ttl: float = 30.0

    def __post_init__(self) -> None:
        if isinstance(self.order_id, str):
            self.order_id = UUID(self.order_id)
        elif self.order_id is None:
            self.order_id = uuid4()

        if self.timestamp == 0:
            self.timestamp = time.time()

        self.amount = int(self.amount)
        self.sequence_number = int(self.sequence_number)
        self.epoch = int(self.epoch)
        self.ttl = float(self.ttl)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": str(self.order_id),
            "sender": self.sender,
            "recipient": self.recipient,
            "amount": self.amount,
            "sequence_number": self.sequence_number,
            "timestamp": self.timestamp,
            "signature": self.signature,
            "epoch": self.epoch,
            "ttl": self.ttl,
        }

    def to_compact_dict(self) -> Dict[str, Any]:
        data = {
            "i": self.order_id.hex,
            "s": self.sender,
            "r": self.recipient,
            "a": self.amount,
            "q": self.sequence_number,
            "t": self.timestamp,
            "g": self.signature,
        }

        if self.epoch != 0:
            data["e"] = self.epoch

        if self.ttl != 30.0:
            data["l"] = self.ttl

        return data

    def signing_dict(self) -> Dict[str, Any]:
        data = self.to_dict()
        data["signature"] = None
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TransferOrder":
        if "order_id" not in data and "i" in data:
            return cls.from_compact_dict(data)

        return cls(
            order_id=UUID(str(data["order_id"])),
            sender=data["sender"],
            recipient=data["recipient"],
            amount=int(data["amount"]),
            sequence_number=int(data["sequence_number"]),
            timestamp=float(data["timestamp"]),
            signature=data.get("signature"),
            epoch=int(data.get("epoch", 0)),
            ttl=float(data.get("ttl", 30.0)),
        )

    @classmethod
    def from_compact_dict(cls, data: Dict[str, Any]) -> "TransferOrder":
        return cls(
            order_id=UUID(str(data["i"])),
            sender=data["s"],
            recipient=data["r"],
            amount=int(data["a"]),
            sequence_number=int(data["q"]),
            timestamp=float(data.get("t", 0.0)),
            signature=data.get("g"),
            epoch=int(data.get("e", 0)),
            ttl=float(data.get("l", 30.0)),
        )

    def to_dtn_payload(self) -> Dict[str, Any]:
        return self.to_compact_dtn_payload()

    def to_full_dtn_payload(self) -> Dict[str, Any]:
        return {
            "app": PAYMENT_APP,
            "type": "transfer_order",
            "data": self.to_dict(),
        }

    def to_compact_dtn_payload(self) -> Dict[str, Any]:
        return {
            "app": PAYMENT_APP,
            "type": "transfer_order",
            "v": COMPACT_PAYLOAD_VERSION,
            "data": self.to_compact_dict(),
        }

    @classmethod
    def from_dtn_payload(cls, payload: Dict[str, Any]) -> "TransferOrder":
        data = _payload_data(payload, "transfer_order")

        if payload.get("v") == COMPACT_PAYLOAD_VERSION or "i" in data:
            return cls.from_compact_dict(data)

        return cls.from_dict(data)


@dataclass(frozen=True)
class AuthorityVote:
    """One authority's signed vote against an immutable weight snapshot."""

    authority: AuthorityName
    signature: str
    epoch: int
    weight_units: int
    total_weight_units: int
    committee_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "authority", AuthorityName(str(self.authority)))
        object.__setattr__(self, "epoch", int(self.epoch))
        object.__setattr__(self, "weight_units", int(self.weight_units))
        object.__setattr__(self, "total_weight_units", int(self.total_weight_units))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "authority": str(self.authority),
            "signature": self.signature,
            "epoch": self.epoch,
            "weight_units": self.weight_units,
            "total_weight_units": self.total_weight_units,
            "committee_digest": self.committee_digest,
        }

    def to_compact_dict(self) -> Dict[str, Any]:
        return {
            "a": str(self.authority),
            "g": self.signature,
            "e": self.epoch,
            "w": self.weight_units,
            "n": self.total_weight_units,
            "c": self.committee_digest,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuthorityVote":
        if "authority" not in data:
            return cls.from_compact_dict(data)
        return cls(
            authority=AuthorityName(str(data["authority"])),
            signature=str(data["signature"]),
            epoch=int(data["epoch"]),
            weight_units=int(data["weight_units"]),
            total_weight_units=int(data["total_weight_units"]),
            committee_digest=str(data["committee_digest"]),
        )

    @classmethod
    def from_compact_dict(cls, data: Dict[str, Any]) -> "AuthorityVote":
        return cls(
            authority=AuthorityName(str(data["a"])),
            signature=str(data["g"]),
            epoch=int(data["e"]),
            weight_units=int(data["w"]),
            total_weight_units=int(data["n"]),
            committee_digest=str(data["c"]),
        )


@dataclass
class SignedTransferOrder:
    """Authority-signed transfer order with its immutable weighted vote."""

    order_id: UUID
    transfer_order: TransferOrder
    authority_vote: AuthorityVote
    timestamp: float

    def __post_init__(self) -> None:
        if isinstance(self.order_id, str):
            self.order_id = UUID(self.order_id)
        elif self.order_id is None:
            self.order_id = uuid4()

        if self.timestamp == 0:
            self.timestamp = time.time()

        if isinstance(self.transfer_order, dict):
            self.transfer_order = TransferOrder.from_dict(self.transfer_order)

        if isinstance(self.authority_vote, dict):
            self.authority_vote = AuthorityVote.from_dict(self.authority_vote)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": str(self.order_id),
            "transfer_order": self.transfer_order.to_dict(),
            "authority_vote": self.authority_vote.to_dict(),
            "timestamp": self.timestamp,
        }

    def to_compact_dict(self) -> Dict[str, Any]:
        return {
            "i": self.order_id.hex,
            "v": self.authority_vote.to_compact_dict(),
            "t": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SignedTransferOrder":
        return cls(
            order_id=UUID(str(data["order_id"])),
            transfer_order=TransferOrder.from_dict(data["transfer_order"]),
            authority_vote=AuthorityVote.from_dict(data["authority_vote"]),
            timestamp=float(data["timestamp"]),
        )

    @classmethod
    def from_compact_dict(
        cls,
        data: Dict[str, Any],
        order_lookup: OrderLookup | None = None,
    ) -> "SignedTransferOrder":
        order_id = str(UUID(str(data["i"])))
        transfer_order = order_lookup(order_id) if order_lookup else None

        if transfer_order is None:
            raise ValueError(f"missing transfer order for compact signed payload: {order_id}")

        return cls(
            order_id=UUID(order_id),
            transfer_order=transfer_order,
            authority_vote=AuthorityVote.from_compact_dict(data["v"]),
            timestamp=float(data.get("t", 0.0)),
        )

    def to_dtn_payload(self) -> Dict[str, Any]:
        return self.to_compact_dtn_payload()

    def to_full_dtn_payload(self) -> Dict[str, Any]:
        return {
            "app": PAYMENT_APP,
            "type": "signed_transfer_order",
            "data": self.to_dict(),
        }

    def to_compact_dtn_payload(self) -> Dict[str, Any]:
        return {
            "app": PAYMENT_APP,
            "type": "signed_transfer_order",
            "v": COMPACT_PAYLOAD_VERSION,
            "data": self.to_compact_dict(),
        }

    @classmethod
    def from_dtn_payload(
        cls,
        payload: Dict[str, Any],
        order_lookup: OrderLookup | None = None,
    ) -> "SignedTransferOrder":
        data = _payload_data(payload, "signed_transfer_order")

        if "i" in data and payload.get("v") != COMPACT_PAYLOAD_VERSION:
            raise ValueError("unsupported signed-transfer payload version")
        if payload.get("v") == COMPACT_PAYLOAD_VERSION or "i" in data:
            return cls.from_compact_dict(data, order_lookup=order_lookup)

        return cls.from_dict(data)


@dataclass
class ConfirmationOrder:
    """Payment confirmation created after quorum authority signatures."""

    order_id: UUID
    transfer_order: TransferOrder
    authority_votes: List[AuthorityVote]
    timestamp: float
    quorum_epoch: int
    total_weight_units: int
    committee_digest: str
    status: TransactionStatus = TransactionStatus.PENDING

    def __post_init__(self) -> None:
        if isinstance(self.order_id, str):
            self.order_id = UUID(self.order_id)
        elif self.order_id is None:
            self.order_id = uuid4()

        if isinstance(self.transfer_order, dict):
            self.transfer_order = TransferOrder.from_dict(self.transfer_order)

        if self.timestamp == 0:
            self.timestamp = time.time()

        self.status = _status_from_value(self.status)
        self.authority_votes = [
            AuthorityVote.from_dict(vote) if isinstance(vote, dict) else vote
            for vote in self.authority_votes
        ]
        self.quorum_epoch = int(self.quorum_epoch)
        self.total_weight_units = int(self.total_weight_units)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": str(self.order_id),
            "transfer_order": self.transfer_order.to_dict(),
            "authority_votes": [vote.to_dict() for vote in self.authority_votes],
            "timestamp": self.timestamp,
            "quorum_epoch": self.quorum_epoch,
            "total_weight_units": self.total_weight_units,
            "committee_digest": self.committee_digest,
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
        }

    def to_compact_dict(self) -> Dict[str, Any]:
        order = self.transfer_order
        data = {
            "i": self.order_id.hex,
            "s": order.sender,
            "r": order.recipient,
            "a": order.amount,
            "q": order.sequence_number,
            "g": order.signature,
            "ot": order.timestamp,
            "x": [vote.to_compact_dict() for vote in self.authority_votes],
            "h": self.quorum_epoch,
            "n": self.total_weight_units,
            "c": self.committee_digest,
            "t": self.timestamp,
        }

        if order.epoch != 0:
            data["e"] = order.epoch

        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConfirmationOrder":
        if "order_id" not in data and "i" in data:
            return cls.from_compact_dict(data)

        return cls(
            order_id=UUID(str(data["order_id"])),
            transfer_order=TransferOrder.from_dict(data["transfer_order"]),
            authority_votes=[AuthorityVote.from_dict(vote) for vote in data.get("authority_votes", [])],
            timestamp=float(data["timestamp"]),
            quorum_epoch=int(data["quorum_epoch"]),
            total_weight_units=int(data["total_weight_units"]),
            committee_digest=str(data["committee_digest"]),
            status=_status_from_value(data.get("status", TransactionStatus.PENDING)),
        )

    @classmethod
    def from_compact_dict(
        cls,
        data: Dict[str, Any],
        order_lookup: OrderLookup | None = None,
    ) -> "ConfirmationOrder":
        order_id = str(UUID(str(data["i"])))
        transfer_order = order_lookup(order_id) if order_lookup else None

        if transfer_order is None:
            transfer_order = TransferOrder(
                order_id=UUID(order_id),
                sender=data["s"],
                recipient=data["r"],
                amount=int(data["a"]),
                sequence_number=int(data["q"]),
                timestamp=float(data.get("ot", 0.0)),
                signature=data.get("g"),
                epoch=int(data.get("e", 0)),
            )

        return cls(
            order_id=UUID(order_id),
            transfer_order=transfer_order,
            authority_votes=[AuthorityVote.from_compact_dict(vote) for vote in data.get("x", [])],
            timestamp=float(data.get("t", 0.0)),
            quorum_epoch=int(data["h"]),
            total_weight_units=int(data["n"]),
            committee_digest=str(data["c"]),
            status=_status_from_value(data.get("z", TransactionStatus.CONFIRMED)),
        )

    def to_dtn_payload(self) -> Dict[str, Any]:
        return self.to_compact_dtn_payload()

    def to_full_dtn_payload(self) -> Dict[str, Any]:
        return {
            "app": PAYMENT_APP,
            "type": "confirmation_order",
            "data": self.to_dict(),
        }

    def to_compact_dtn_payload(self) -> Dict[str, Any]:
        return {
            "app": PAYMENT_APP,
            "type": "confirmation_order",
            "v": COMPACT_PAYLOAD_VERSION,
            "data": self.to_compact_dict(),
        }

    @classmethod
    def from_dtn_payload(
        cls,
        payload: Dict[str, Any],
        order_lookup: OrderLookup | None = None,
    ) -> "ConfirmationOrder":
        data = _payload_data(payload, "confirmation_order")

        if "i" in data and payload.get("v") != COMPACT_PAYLOAD_VERSION:
            raise ValueError("unsupported confirmation payload version")
        if payload.get("v") == COMPACT_PAYLOAD_VERSION or "i" in data:
            return cls.from_compact_dict(data, order_lookup=order_lookup)

        return cls.from_dict(data)


@dataclass
class BufferedTransfer:
    """Transaction buffered on client awaiting quorum."""

    order: TransferOrder
    signatures_received: Dict[str, str] = field(default_factory=dict)
    signatures_required: int = 0
    created_at: float = 0.0
    last_retry: float = 0.0
    retry_count: int = 0
    status: TransactionStatus = TransactionStatus.BUFFERED
    is_relay: bool = False
    relay_metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if isinstance(self.order, dict):
            self.order = TransferOrder.from_dict(self.order)

        if self.created_at == 0.0:
            self.created_at = time.time()

        if self.last_retry == 0.0:
            self.last_retry = self.created_at

        self.status = _status_from_value(self.status)

    @property
    def has_quorum(self) -> bool:
        return len(self.signatures_received) >= self.signatures_required

    def add_signature(self, authority_name: str, signature: str) -> bool:
        if authority_name not in self.signatures_received:
            self.signatures_received[authority_name] = signature

        return self.has_quorum


@dataclass
class MessageBufferItem:
    """An item stored in the DTN message buffer for store-carry-forward routing."""

    message_id: str
    message_type: str
    payload: Dict[str, Any]
    sender_id: str
    ttl: int
    created_at: float = 0.0
    expires_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.created_at == 0.0:
            self.created_at = time.time()

        if self.expires_at == 0.0:
            self.expires_at = self.created_at + (24 * 3600)

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at or self.ttl <= 0
