"""Message types and protocols for MeshPay WiFi communication."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from uuid import UUID, uuid4

from meshpay.types import Address, ConfirmationOrder, TransferOrder, NodeType


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
    MESH_RELAY = "mesh_relay"
    ERROR = "error"


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
        # We need a custom encoder or pre-processing because 'sender' and 'recipient'
        # are Address objects containing NodeType enums which json.dumps fails on.
        data = asdict(self)
        
        # Convert UUID to string for JSON serialization
        data['message_id'] = str(data['message_id'])
        data['message_type'] = data['message_type'].value
        
        # Fix top-level sender/recipient serialization
        if 'sender' in data and isinstance(data['sender'], dict):
             if 'node_type' in data['sender'] and isinstance(data['sender']['node_type'], Enum):
                 data['sender']['node_type'] = data['sender']['node_type'].value

        if 'recipient' in data and data['recipient'] and isinstance(data['recipient'], dict):
             if 'node_type' in data['recipient'] and isinstance(data['recipient']['node_type'], Enum):
                 data['recipient']['node_type'] = data['recipient']['node_type'].value
                 
        return json.dumps(data)
    
    @classmethod
    def from_json(cls, json_str: str) -> "Message":
        """Deserialize message from JSON."""
        data = json.loads(json_str)
        data['message_id'] = UUID(data['message_id'])
        data['message_type'] = MessageType(data['message_type'])
        
        # Reconstruct sender/recipient if needed (dictionaries to objects)
        # Note: Address(**dict) might fail if node_type is string and we expect Enum?
        # dataclass constructor doesn't validate types, so it might store string.
        # Ideally we should convert back to Enum.
        
        if isinstance(data['sender'], dict):
            if isinstance(data['sender'].get('node_type'), str):
                data['sender']['node_type'] = NodeType(data['sender']['node_type'])
            data['sender'] = Address(**data['sender'])
            
        if data.get('recipient') and isinstance(data['recipient'], dict):
            if isinstance(data['recipient'].get('node_type'), str):
                data['recipient']['node_type'] = NodeType(data['recipient']['node_type'])
            data['recipient'] = Address(**data['recipient'])
            
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
        data = {
            'node_info': asdict(self.node_info),
            'service_capabilities': self.service_capabilities,
            'network_metrics': self.network_metrics
        }
        # Explicitly convert Enum to value for JSON serialization
        if hasattr(self.node_info, 'node_type') and isinstance(self.node_info.node_type, Enum):
            data['node_info']['node_type'] = self.node_info.node_type.value
            
        return data
    
    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "PeerDiscoveryMessage":
        """Create from message payload."""
        node_data = payload['node_info']
        
        # Convert string back to Enum if needed
        if isinstance(node_data.get('node_type'), str):
            node_data['node_type'] = NodeType(node_data['node_type'])
            
        node_info = Address(**node_data)
        return cls(
            node_info=node_info,
            service_capabilities=payload['service_capabilities'],
            network_metrics=payload.get('network_metrics')
        )


@dataclass
class MeshRelayMessage:
    """Wrapper for messages relayed through the opportunistic wireless mesh.

    Any inner message (transfer request, transfer response, confirmation)
    is wrapped with relay metadata so that intermediate nodes can forward
    it toward its destination without needing end-to-end connectivity.
    """

    original_sender_id: str          # node_id of the originator
    origin_address: Dict[str, Any]   # serialised Address of the originator
    inner_message_type: str          # MessageType.value of the wrapped msg
    inner_payload: Dict[str, Any]    # payload of the wrapped message
    order_id: str                    # transfer order ID (for dedup)
    ttl: int                         # remaining hops
    hop_path: List[str]              # node_ids already traversed

    def to_payload(self) -> Dict[str, Any]:
        """Serialise to a dict suitable for ``Message.payload``."""
        return {
            "original_sender_id": self.original_sender_id,
            "origin_address": self.origin_address,
            "inner_message_type": self.inner_message_type,
            "inner_payload": self.inner_payload,
            "order_id": self.order_id,
            "ttl": self.ttl,
            "hop_path": list(self.hop_path),
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "MeshRelayMessage":
        """Reconstruct from a ``Message.payload`` dict."""
        return cls(
            original_sender_id=payload["original_sender_id"],
            origin_address=payload["origin_address"],
            inner_message_type=payload["inner_message_type"],
            inner_payload=payload["inner_payload"],
            order_id=payload["order_id"],
            ttl=payload["ttl"],
            hop_path=payload["hop_path"],
        )

