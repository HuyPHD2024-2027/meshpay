"""Utility functions for logger message formatting.

This module provides helper functions to format log messages for better debugging,
including handling of complex objects, message truncation, and structured formatting.
"""

import json
from typing import Any, Dict, Optional
from uuid import UUID


def format_message(message: Any, max_length: int = 200, pretty: bool = False) -> str:
    """Format a message for logging with intelligent truncation and object handling.
    
    Args:
        message: The message to format (can be str, dict, object, etc.)
        max_length: Maximum length of the formatted message (default: 200)
        pretty: If True, format dicts/objects with indentation for readability
        
    Returns:
        Formatted string suitable for logging
    """
    if message is None:
        return "None"
    
    # Handle string messages
    if isinstance(message, str):
        if len(message) <= max_length:
            return message
        return message[:max_length - 3] + "..."
    
    # Handle dict messages
    if isinstance(message, dict):
        return format_dict(message, max_length=max_length, pretty=pretty)
    
    # Handle objects with __dict__ attribute
    if hasattr(message, '__dict__'):
        return format_object(message, max_length=max_length, pretty=pretty)
    
    # Handle other types
    msg_str = str(message)
    if len(msg_str) <= max_length:
        return msg_str
    return msg_str[:max_length - 3] + "..."


def format_dict(data: Dict[str, Any], max_length: int = 200, pretty: bool = False) -> str:
    """Format a dictionary for logging.
    
    Args:
        data: Dictionary to format
        max_length: Maximum length of output
        pretty: If True, use indented JSON format
        
    Returns:
        Formatted dictionary string
    """
    try:
        if pretty:
            formatted = json.dumps(data, indent=2, default=_json_serializer, ensure_ascii=False)
        else:
            formatted = json.dumps(data, default=_json_serializer, ensure_ascii=False)
        
        if len(formatted) <= max_length:
            return formatted
        
        # Truncate and add ellipsis
        return formatted[:max_length - 3] + "..."
    except (TypeError, ValueError):
        # Fallback to repr if JSON serialization fails
        result = repr(data)
        if len(result) <= max_length:
            return result
        return result[:max_length - 3] + "..."


def format_object(obj: Any, max_length: int = 200, pretty: bool = False) -> str:
    """Format an object for logging by extracting its attributes.
    
    Args:
        obj: Object to format
        max_length: Maximum length of output
        pretty: If True, use indented format
        
    Returns:
        Formatted object string
    """
    class_name = obj.__class__.__name__
    
    # Try to get key attributes
    try:
        attrs = {}
        for key, value in obj.__dict__.items():
            # Skip private attributes
            if key.startswith('_'):
                continue
            attrs[key] = value
        
        if pretty:
            formatted = f"{class_name}({json.dumps(attrs, indent=2, default=_json_serializer, ensure_ascii=False)})"
        else:
            formatted = f"{class_name}({json.dumps(attrs, default=_json_serializer, ensure_ascii=False)})"
        
        if len(formatted) <= max_length:
            return formatted
        return formatted[:max_length - 3] + "..."
    except Exception:
        # Fallback to repr
        result = repr(obj)
        if len(result) <= max_length:
            return result
        return result[:max_length - 3] + "..."


def format_transfer_message(transfer_order: Any) -> str:
    """Format a transfer order for logging.
    
    Args:
        transfer_order: TransferOrder object
        
    Returns:
        Formatted transfer summary
    """
    try:
        order_id = getattr(transfer_order, 'order_id', 'unknown')
        sender = getattr(transfer_order, 'sender', 'unknown')
        recipient = getattr(transfer_order, 'recipient', 'unknown')
        amount = getattr(transfer_order, 'amount', 0)
        token = getattr(transfer_order, 'token_address', 'unknown')
        seq = getattr(transfer_order, 'sequence_number', '?')
        
        # Truncate addresses for readability
        order_id_short = str(order_id)[:8] if order_id != 'unknown' else 'unknown'
        token_short = str(token)[:10] + '...' if len(str(token)) > 10 else str(token)
        
        return f"Transfer[{order_id_short}] {sender}→{recipient} {amount} {token_short} seq={seq}"
    except Exception:
        return format_object(transfer_order, max_length=150)


def format_network_message(message: Any) -> str:
    """Format a network message for logging.
    
    Args:
        message: Message object
        
    Returns:
        Formatted message summary
    """
    try:
        msg_id = getattr(message, 'message_id', 'unknown')
        msg_type = getattr(message, 'message_type', 'unknown')
        sender = getattr(message, 'sender', 'unknown')
        recipient = getattr(message, 'recipient', 'unknown')
        
        # Extract sender/recipient info
        sender_str = _format_address(sender)
        recipient_str = _format_address(recipient)
        
        msg_id_short = str(msg_id)[:8] if msg_id != 'unknown' else 'unknown'
        msg_type_str = str(msg_type).split('.')[-1] if hasattr(msg_type, '__class__') else str(msg_type)
        
        return f"Msg[{msg_id_short}] {msg_type_str} from {sender_str} to {recipient_str}"
    except Exception:
        return format_object(message, max_length=150)


def format_balance_info(balances: Dict[str, Any]) -> str:
    """Format balance information for logging.
    
    Args:
        balances: Dictionary of token_address -> TokenBalance
        
    Returns:
        Formatted balance summary
    """
    try:
        balance_parts = []
        for token_addr, balance in balances.items():
            symbol = getattr(balance, 'token_symbol', '???')
            meshpay_bal = getattr(balance, 'meshpay_balance', 0)
            total_bal = getattr(balance, 'total_balance', 0)
            balance_parts.append(f"{symbol}:{meshpay_bal:.2f}/{total_bal:.2f}")
        
        return ", ".join(balance_parts) if balance_parts else "no balances"
    except Exception:
        return format_dict(balances, max_length=100)


def _format_address(addr: Any) -> str:
    """Format an address object for compact display.
    
    Args:
        addr: Address object or string
        
    Returns:
        Formatted address string
    """
    if addr is None:
        return "None"
    
    if isinstance(addr, str):
        return addr
    
    try:
        node_id = getattr(addr, 'node_id', None)
        ip = getattr(addr, 'ip_address', None)
        port = getattr(addr, 'port', None)
        
        if node_id:
            return f"{node_id}@{ip}:{port}" if ip and port else str(node_id)
        return str(addr)
    except Exception:
        return str(addr)


def _json_serializer(obj: Any) -> Any:
    """Custom JSON serializer for objects that aren't natively serializable.
    
    Args:
        obj: Object to serialize
        
    Returns:
        Serializable representation
    """
    # Handle UUID
    if isinstance(obj, UUID):
        return str(obj)
    
    # Handle objects with __dict__
    if hasattr(obj, '__dict__'):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith('_')}
    
    # Handle enums
    if hasattr(obj, 'value'):
        return obj.value
    
    # Fallback to string representation
    return str(obj)


def truncate_for_display(text: str, max_length: int = 60) -> str:
    """Truncate text for display in narrow columns.
    
    Args:
        text: Text to truncate
        max_length: Maximum length
        
    Returns:
        Truncated text with ellipsis if needed
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."
