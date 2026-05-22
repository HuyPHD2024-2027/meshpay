"""Policy data helpers for the MeshPay SDN-DTN policy plane."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set


SUPPORTED_ROUTING_ALGORITHMS = (
    "epidemic",
    "spray_and_wait",
    "prophet",
    "sdn_dtn",
)

SUPPORTED_WIRELESS_INTERFACES = (
    "mesh_80211s",
    "adhoc_wifi",
    "wifi_direct",
    "physical_wifi_direct",
    "wwan_d2d",
)


DEFAULT_TRAFFIC_CLASSES: Dict[str, Dict[str, Any]] = {
    "high": {
        "message_types": ["transfer_response", "confirmation_request"],
        "priority": 1,
    },
    "medium": {
        "message_types": ["transfer_request"],
        "priority": 2,
    },
    "low": {
        "message_types": [
            "sdn_summary",
            "sdn_request",
            "sdn_policy",
            "telemetry",
            "heartbeat",
            "peer_discovery",
        ],
        "priority": 3,
    },
}

DEFAULT_FORWARDING_RULES: Dict[str, Dict[str, Any]] = {
    "transfer_request": {
        "action": "push",
        "target": "authority_neighbors",
        "interface_preference": ["mesh_80211s", "wifi_direct", "wwan_d2d"],
        "replication_limit": 5,
    },
    "transfer_response": {
        "action": "push",
        "target": "sender_client",
        "interface_preference": ["wifi_direct", "mesh_80211s", "wwan_d2d"],
        "replication_limit": 3,
    },
    "confirmation_request": {
        "action": "push",
        "target": "authorities_and_recipient",
        "interface_preference": ["mesh_80211s", "wifi_direct", "wwan_d2d"],
        "replication_limit": 5,
    },
}


@dataclass(frozen=True)
class SafetyLimits:
    """Local guardrails applied before a signed policy can become active."""

    max_buffer_items: int = 10_000
    max_replication_limit: int = 256
    supported_algorithms: Set[str] = field(
        default_factory=lambda: set(SUPPORTED_ROUTING_ALGORITHMS)
    )
    supported_interfaces: Set[str] = field(
        default_factory=lambda: set(SUPPORTED_WIRELESS_INTERFACES)
    )


def build_default_policy(
    authorities: Sequence[str],
    *,
    policy_id: str = "market-oppnet-policy-v1",
    epoch: int = 1,
    valid_from: float = 0.0,
    valid_until: float = 60.0,
    preferred_interfaces: Optional[Sequence[str]] = None,
    routing_algorithm: str = "sdn_dtn",
) -> Dict[str, Any]:
    """Build the default MeshPay opportunistic SDN-DTN policy document."""

    interfaces = list(preferred_interfaces or ("mesh_80211s", "wifi_direct", "wwan_d2d"))
    return {
        "policy_id": policy_id,
        "epoch": int(epoch),
        "valid_from": valid_from,
        "valid_until": valid_until,
        "committee": {
            "authorities": sorted(str(authority) for authority in authorities),
            "quorum": "two_thirds_plus_one",
        },
        "network": {
            "mode": "oppnet",
            "preferred_interfaces": interfaces,
            "fallback_interface": interfaces[0] if interfaces else "mesh_80211s",
        },
        "routing": {
            "algorithm": routing_algorithm,
            "fallback_algorithm": "epidemic",
            "supported_algorithms": list(SUPPORTED_ROUTING_ALGORITHMS),
        },
        "traffic_classes": copy.deepcopy(DEFAULT_TRAFFIC_CLASSES),
        "forwarding_rules": copy.deepcopy(DEFAULT_FORWARDING_RULES),
        "buffer_rules": {
            "prune_finalized_transactions": True,
            "max_buffer_items": 100,
            "drop_policy": "low_priority_first",
        },
        "security": {
            "reject_old_epoch": True,
            "reject_expired": True,
            "fallback_on_invalid_policy": "epidemic",
        },
        "signatures": {},
    }


def policy_epoch(policy: Mapping[str, Any]) -> int:
    """Return a policy epoch as an integer."""

    return int(policy.get("epoch", 0))


def committee_authorities(policy: Mapping[str, Any]) -> List[str]:
    """Return committee authorities in stable order."""

    committee = policy.get("committee") or {}
    authorities = committee.get("authorities") or []
    return sorted(str(authority) for authority in authorities)


def quorum_rule(policy: Mapping[str, Any]) -> Any:
    """Return the configured quorum rule."""

    committee = policy.get("committee") or {}
    return committee.get("quorum", "two_thirds_plus_one")


def policy_signatures(policy: Mapping[str, Any]) -> Dict[str, str]:
    """Return normalized signature mapping."""

    signatures = policy.get("signatures") or {}
    return {str(authority): str(signature) for authority, signature in signatures.items()}


def is_policy_expired(policy: Mapping[str, Any], now: float) -> bool:
    """Return whether the policy is expired at ``now``."""

    valid_until = policy.get("valid_until")
    return valid_until is not None and float(valid_until) < now


def is_policy_not_yet_valid(policy: Mapping[str, Any], now: float) -> bool:
    """Return whether the policy validity window has not opened."""

    valid_from = policy.get("valid_from")
    return valid_from is not None and float(valid_from) > now


def get_message_priority(policy: Optional[Mapping[str, Any]], message_type: str, default: int = 3) -> int:
    """Return the policy-defined priority for a MeshPay message type."""

    if not policy:
        return default

    traffic_classes = policy.get("traffic_classes") or {}
    for traffic_class in traffic_classes.values():
        message_types = traffic_class.get("message_types", [])
        if message_type in message_types:
            return int(traffic_class.get("priority", default))
    return default


def get_forwarding_rule(policy: Optional[Mapping[str, Any]], message_type: str) -> Dict[str, Any]:
    """Return a copy of the forwarding rule for ``message_type``."""

    if not policy:
        return {}
    rules = policy.get("forwarding_rules") or {}
    return copy.deepcopy(rules.get(message_type, {}))


def get_replication_limit(
    policy: Optional[Mapping[str, Any]],
    message_type: str,
    default: int,
) -> int:
    """Return the effective replication limit for a message type."""

    rule = get_forwarding_rule(policy, message_type)
    if "replication_limit" in rule:
        return int(rule["replication_limit"])

    legacy_limits = (policy or {}).get("replication_limits") or {}
    return int(legacy_limits.get(message_type, default))


def get_interface_preference(policy: Optional[Mapping[str, Any]], message_type: str) -> List[str]:
    """Return preferred interfaces for a message type, falling back to network defaults."""

    rule = get_forwarding_rule(policy, message_type)
    if rule.get("interface_preference"):
        return [str(name) for name in rule["interface_preference"]]

    network = (policy or {}).get("network") or {}
    return [str(name) for name in network.get("preferred_interfaces", [])]


def validate_policy_safety(policy: Mapping[str, Any], limits: SafetyLimits) -> Optional[str]:
    """Return an error string if the policy violates local safety limits."""

    buffer_rules = policy.get("buffer_rules") or {}
    max_buffer_items = int(buffer_rules.get("max_buffer_items", 0) or 0)
    if max_buffer_items and max_buffer_items > limits.max_buffer_items:
        return "max_buffer_items_exceeds_local_limit"

    routing = policy.get("routing") or {}
    algorithm = str(routing.get("algorithm", "epidemic"))
    fallback = str(routing.get("fallback_algorithm", "epidemic"))
    supported = set(str(name) for name in routing.get("supported_algorithms", []))
    for name in {algorithm, fallback} | supported:
        if name and name not in limits.supported_algorithms:
            return f"unsupported_routing_algorithm:{name}"

    network = policy.get("network") or {}
    interfaces: Set[str] = set(str(name) for name in network.get("preferred_interfaces", []))
    if network.get("fallback_interface"):
        interfaces.add(str(network["fallback_interface"]))

    forwarding_rules = policy.get("forwarding_rules") or {}
    for rule in forwarding_rules.values():
        interfaces.update(str(name) for name in rule.get("interface_preference", []))
        replication_limit = int(rule.get("replication_limit", 0) or 0)
        if replication_limit > limits.max_replication_limit:
            return "replication_limit_exceeds_local_limit"

    for name in interfaces:
        if name not in limits.supported_interfaces:
            return f"unsupported_wireless_interface:{name}"

    return None


def merge_signatures(policy: Mapping[str, Any], signatures: Mapping[str, str]) -> Dict[str, Any]:
    """Return a copy of ``policy`` with the provided signatures merged in."""

    merged = copy.deepcopy(dict(policy))
    existing = policy_signatures(merged)
    existing.update({str(authority): str(signature) for authority, signature in signatures.items()})
    merged["signatures"] = existing
    return merged
