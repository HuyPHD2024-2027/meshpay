"""Common MeshPay types used by transaction and state models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import NewType


AuthorityName = NewType("AuthorityName", str)


class TransactionStatus(Enum):
    """Basic transaction status values."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    BUFFERED = "buffered"
    EXPIRED = "expired"
    FAILED = "failed"


class NodeType(Enum):
    """MeshPay node roles."""

    CLIENT = "client"
    AUTHORITY = "authority"
    GATEWAY = "gateway"
    RELAY = "relay"


@dataclass(frozen=True)
class KeyPair:
    """Placeholder key pair for the first clean implementation.

    Later we can replace this with real public/private key handling.
    """

    private_key: str
    public_key: str = ""


@dataclass(frozen=True)
class Address:
    """Network address for a MeshPay node."""

    node_id: str
    ip_address: str
    port: int
    node_type: NodeType