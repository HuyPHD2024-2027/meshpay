"""Common types, enums, and type aliases for MeshPay."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, NewType
from dataclasses import dataclass

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
