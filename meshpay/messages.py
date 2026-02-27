"""Message types and protocols for MeshPay WiFi communication."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from meshpay.types import Address, ConfirmationOrder, TransferOrder

class MessageType(Enum):
    """Types of messages in the MeshPay WiFi protocol."""
    
    TRANSFER_REQUEST = "transfer_request"
    TRANSFER_RESPONSE = "transfer_response"
    CONFIRMATION_REQUEST = "confirmation_request"
    CONFIRMATION_RESPONSE = "confirmation_response"
    SYNC_REQUEST = "sync_request"
    SYNC_RESPONSE = "sync_response"
    PEER_DISCOVERY = "peer_discovery"
    HEARTBEAT = "heartbeat"
    ERROR = "error"
    PREENDORSEMENT = "preendorsement"
    CERTIFICATE = "certificate"
    ANCHOR_COMMITMENT = "anchor_commitment"
    RECONCILE_REQUEST = "reconcile_request"
    RECONCILE_RESPONSE = "reconcile_response"


@dataclass
class Message:
    """Base message class for all WiFi communications."""
    
    message_id: UUID
    message_type: MessageType
    sender: Address
    recipient: Optional[Address]
    timestamp: float
    payload: Dict[str, Any]
    signature: Optional[str] = None
    
    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.message_id is None:
            self.message_id = uuid4()
        if self.timestamp == 0:
            self.timestamp = time.time()
    
    def to_json(self) -> str:
        """Serialize message to JSON."""
        data = asdict(self)
        # Convert UUID to string for JSON serialization
        data['message_id'] = str(data['message_id'])
        data['message_type'] = data['message_type'].value
        return json.dumps(data)
    
    @classmethod
    def from_json(cls, json_str: str) -> "Message":
        """Deserialize message from JSON."""
        data = json.loads(json_str)
        data['message_id'] = UUID(data['message_id'])
        data['message_type'] = MessageType(data['message_type'])
        return cls(**data)


@dataclass
class TransferRequestMessage:
    """Message for requesting a transfer."""
    
    transfer_order: TransferOrder
    
    def to_payload(self) -> Dict[str, Any]:
        """Convert to message payload."""
        return {
            'transfer_order': {
                'order_id': str(self.transfer_order.order_id),
                'sender': str(self.transfer_order.sender),
                'recipient': str(self.transfer_order.recipient),
                'token_address': str(self.transfer_order.token_address),
                'amount': self.transfer_order.amount,
                'sequence_number': self.transfer_order.sequence_number,
                'timestamp': self.transfer_order.timestamp,
                'signature': self.transfer_order.signature
            }
        }
    
    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "TransferRequestMessage":
        """Create from message payload."""
        transfer_data = payload['transfer_order']

        if isinstance(transfer_data.get('order_id'), str):
            transfer_data['order_id'] = UUID(transfer_data['order_id'])
        transfer_order = TransferOrder(**transfer_data)
        return cls(
            transfer_order=transfer_order,
        )


@dataclass
class TransferResponseMessage:
    """Message for responding to a transfer request."""
    
    transfer_order: TransferOrder
    success: bool
    error_message: Optional[str] = None
    authority_signature: Optional[str] = None
    
    def to_payload(self) -> Dict[str, Any]:
        """Convert to message payload."""
        return {
            'transfer_order': asdict(self.transfer_order),
            'success': self.success,
            'error_message': self.error_message,
            'authority_signature': self.authority_signature
        }
    
    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "TransferResponseMessage":
        """Create from message payload."""
        return cls(
            transfer_order=TransferOrder(**payload['transfer_order']),
            success=payload['success'],
            error_message=payload.get('error_message'),
            authority_signature=payload.get('authority_signature')
        )


@dataclass
class ConfirmationRequestMessage:
    """Message for requesting confirmation from committee."""
    
    confirmation_order: ConfirmationOrder
    
    def to_payload(self) -> Dict[str, Any]:
        """Convert to message payload."""
        return {
            'confirmation_order': asdict(self.confirmation_order)
        }
    
    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "ConfirmationRequestMessage":
        """Create from message payload."""
        conf_data = payload['confirmation_order']

        if isinstance(conf_data.get('order_id'), str):
            conf_data['order_id'] = UUID(conf_data['order_id'])
        confirmation_order = ConfirmationOrder(**conf_data)
        return cls(
            confirmation_order=confirmation_order,
        )

@dataclass
class SyncRequestMessage:
    """Message for requesting synchronization."""
    
    last_sync_time: float
    account_addresses: List[str]
    
    def to_payload(self) -> Dict[str, Any]:
        """Convert to message payload."""
        return {
            'last_sync_time': self.last_sync_time,
            'account_addresses': self.account_addresses
        }
    
    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "SyncRequestMessage":
        """Create from message payload."""
        return cls(
            last_sync_time=payload['last_sync_time'],
            account_addresses=payload['account_addresses']
        )


@dataclass
class PeerDiscoveryMessage:
    """Message for peer discovery in WiFi network."""
    
    node_info: Address
    service_capabilities: List[str]
    network_metrics: Optional[Dict[str, float]] = None
    
    def to_payload(self) -> Dict[str, Any]:
        """Convert to message payload."""
        return {
            'node_info': asdict(self.node_info),
            'service_capabilities': self.service_capabilities,
            'network_metrics': self.network_metrics
        }
    
    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "PeerDiscoveryMessage":
        """Create from message payload."""
        node_data = payload['node_info']
        node_info = Address(**node_data)
        return cls(
            node_info=node_info,
            service_capabilities=payload['service_capabilities'],
            network_metrics=payload.get('network_metrics')
        )

@dataclass
class PreendorsementMessage:
    """Authority preendorsement (prevote) for a proposal/transfer hash."""

    order_id: UUID
    authority: AuthorityName
    proposal_hash: str
    signature: str

    def to_payload(self) -> Dict[str, Any]:
        """Convert to message payload."""
        return {
            "order_id": str(self.order_id),
            "authority": self.authority,
            "proposal_hash": self.proposal_hash,
            "signature": self.signature,
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "PreendorsementMessage":
        """Create from message payload."""
        return cls(
            order_id=UUID(payload["order_id"]),
            authority=payload["authority"],
            proposal_hash=payload["proposal_hash"],
            signature=payload["signature"],
        )


@dataclass
class CertificateMessage:
    """Commit certificate formed from ≥2f+1 precommits."""

    order_id: UUID
    proposal_hash: str
    precommits: Dict[AuthorityName, str]
    threshold: int

    def to_payload(self) -> Dict[str, Any]:
        """Convert to message payload."""
        return {
            "order_id": str(self.order_id),
            "proposal_hash": self.proposal_hash,
            "precommits": self.precommits,
            "threshold": self.threshold,
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "CertificateMessage":
        """Create from message payload."""
        return cls(
            order_id=UUID(payload["order_id"]),
            proposal_hash=payload["proposal_hash"],
            precommits=payload["precommits"],
            threshold=int(payload["threshold"]),
        )


@dataclass
class AnchorCommitmentMessage:
    """Batch anchor commitment for L1 posting."""

    shard_id: str
    height: int
    state_root: str
    signatures: Dict[AuthorityName, str]

    def to_payload(self) -> Dict[str, Any]:
        """Convert to message payload."""
        return {
            "shard_id": self.shard_id,
            "height": self.height,
            "state_root": self.state_root,
            "signatures": self.signatures,
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "AnchorCommitmentMessage":
        """Create from message payload."""
        return cls(
            shard_id=payload["shard_id"],
            height=int(payload["height"]),
            state_root=payload["state_root"],
            signatures=payload["signatures"],
        )


@dataclass
class ReconcileRequestMessage:
    """Request missing certificates/blocks for a shard."""

    shard_id: str
    from_height: int
    to_height: Optional[int] = None

    def to_payload(self) -> Dict[str, Any]:
        """Convert to message payload."""
        return {
            "shard_id": self.shard_id,
            "from_height": self.from_height,
            "to_height": self.to_height,
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "ReconcileRequestMessage":
        """Create from message payload."""
        return cls(
            shard_id=payload["shard_id"],
            from_height=int(payload["from_height"]),
            to_height=payload.get("to_height"),
        )


@dataclass
class ReconcileResponseMessage:
    """Return a sequence of committed certificates for reconciliation."""

    shard_id: str
    certificates: List[Dict[str, Any]]

    def to_payload(self) -> Dict[str, Any]:
        """Convert to message payload."""
        return {
            "shard_id": self.shard_id,
            "certificates": self.certificates,
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "ReconcileResponseMessage":
        """Create from message payload."""
        return cls(
            shard_id=payload["shard_id"],
            certificates=payload["certificates"],
        )