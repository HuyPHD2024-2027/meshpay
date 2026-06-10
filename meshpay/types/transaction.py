"""Transaction-related types for MeshPay."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from .common import AuthorityName, TransactionStatus


PAYMENT_APP = "meshpay.offline"


def _status_from_value(value: Any) -> TransactionStatus:
    if isinstance(value, TransactionStatus):
        return value

    if isinstance(value, str):
        return TransactionStatus(value)

    return TransactionStatus.PENDING


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

    def signing_dict(self) -> Dict[str, Any]:
        data = self.to_dict()
        data["signature"] = None
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TransferOrder":
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

    def to_dtn_payload(self) -> Dict[str, Any]:
        return {
            "app": PAYMENT_APP,
            "type": "transfer_order",
            "data": self.to_dict(),
        }

    @classmethod
    def from_dtn_payload(cls, payload: Dict[str, Any]) -> "TransferOrder":
        if payload.get("app") != PAYMENT_APP:
            raise ValueError("invalid app for TransferOrder payload")

        if payload.get("type") != "transfer_order":
            raise ValueError("invalid payload type for TransferOrder")

        return cls.from_dict(payload["data"])


@dataclass
class SignedTransferOrder:
    """Authority-signed transfer order."""

    order_id: UUID
    transfer_order: TransferOrder
    authority_signature: Dict[AuthorityName, str]
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

        self.authority_signature = dict(self.authority_signature)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": str(self.order_id),
            "transfer_order": self.transfer_order.to_dict(),
            "authority_signature": dict(self.authority_signature),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SignedTransferOrder":
        return cls(
            order_id=UUID(str(data["order_id"])),
            transfer_order=TransferOrder.from_dict(data["transfer_order"]),
            authority_signature=dict(data["authority_signature"]),
            timestamp=float(data["timestamp"]),
        )

    def to_dtn_payload(self) -> Dict[str, Any]:
        return {
            "app": PAYMENT_APP,
            "type": "signed_transfer_order",
            "data": self.to_dict(),
        }

    @classmethod
    def from_dtn_payload(cls, payload: Dict[str, Any]) -> "SignedTransferOrder":
        if payload.get("app") != PAYMENT_APP:
            raise ValueError("invalid app for SignedTransferOrder payload")

        if payload.get("type") != "signed_transfer_order":
            raise ValueError("invalid payload type for SignedTransferOrder")

        return cls.from_dict(payload["data"])


@dataclass
class ConfirmationOrder:
    """Payment confirmation created after quorum authority signatures."""

    order_id: UUID
    transfer_order: TransferOrder
    authority_signatures: List[str]
    timestamp: float
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
        self.authority_signatures = list(self.authority_signatures)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": str(self.order_id),
            "transfer_order": self.transfer_order.to_dict(),
            "authority_signatures": list(self.authority_signatures),
            "timestamp": self.timestamp,
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConfirmationOrder":
        return cls(
            order_id=UUID(str(data["order_id"])),
            transfer_order=TransferOrder.from_dict(data["transfer_order"]),
            authority_signatures=list(data.get("authority_signatures", [])),
            timestamp=float(data["timestamp"]),
            status=_status_from_value(data.get("status", TransactionStatus.PENDING)),
        )

    def to_dtn_payload(self) -> Dict[str, Any]:
        return {
            "app": PAYMENT_APP,
            "type": "confirmation_order",
            "data": self.to_dict(),
        }

    @classmethod
    def from_dtn_payload(cls, payload: Dict[str, Any]) -> "ConfirmationOrder":
        if payload.get("app") != PAYMENT_APP:
            raise ValueError("invalid app for ConfirmationOrder payload")

        if payload.get("type") != "confirmation_order":
            raise ValueError("invalid payload type for ConfirmationOrder")

        return cls.from_dict(payload["data"])


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