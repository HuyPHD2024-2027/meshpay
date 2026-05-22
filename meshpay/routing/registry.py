"""Routing protocol registry for opportunistic MeshPay experiments."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable

from meshpay.routing.dtn import DTNRoutingProtocol
from meshpay.routing.epidemic import EpidemicRouting
from meshpay.routing.prophet import ProphetRouting
from meshpay.routing.sdn import SDNDTNRouting
from meshpay.routing.spray_and_wait import SprayAndWaitRouting


RoutingFactory = Callable[[str], DTNRoutingProtocol]


_ROUTING_ALIASES = {
    "sdn": "sdn_dtn",
}


_ROUTING_FACTORIES: Dict[str, RoutingFactory] = {
    "epidemic": EpidemicRouting,
    "spray_and_wait": SprayAndWaitRouting,
    "prophet": ProphetRouting,
    "sdn_dtn": SDNDTNRouting,
}


def normalize_routing_name(name: str) -> str:
    """Normalize CLI and legacy routing names."""

    value = str(name or "epidemic").strip().lower().replace("-", "_")
    return _ROUTING_ALIASES.get(value, value)


def supported_routing_algorithms() -> Iterable[str]:
    """Return supported routing algorithm keys."""

    return tuple(_ROUTING_FACTORIES.keys())


def create_routing_protocol(node_id: str, name: str, **params: Any) -> DTNRoutingProtocol:
    """Create a routing protocol by registry key."""

    normalized = normalize_routing_name(name)
    factory = _ROUTING_FACTORIES.get(normalized)
    if not factory:
        supported = ", ".join(sorted(_ROUTING_FACTORIES))
        raise ValueError(f"Unsupported routing protocol: {name!r}. Supported: {supported}")
    return factory(node_id, **params) if params else factory(node_id)
