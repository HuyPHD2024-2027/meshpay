"""Routing protocols and registry for MeshPay DTN forwarding."""

from meshpay.routing.dtn import DTNRoutingProtocol
from meshpay.routing.epidemic import EpidemicRouting
from meshpay.routing.prophet import ProphetRouting
from meshpay.routing.registry import (
    create_routing_protocol,
    normalize_routing_name,
    supported_routing_algorithms,
)
from meshpay.routing.sdn import SDNDTNRouting
from meshpay.routing.spray_and_wait import SprayAndWaitRouting

__all__ = [
    "DTNRoutingProtocol",
    "EpidemicRouting",
    "ProphetRouting",
    "SDNDTNRouting",
    "SprayAndWaitRouting",
    "create_routing_protocol",
    "normalize_routing_name",
    "supported_routing_algorithms",
]
