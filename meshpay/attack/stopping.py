"""Stopping attack causing permanent node process shutdown."""

from __future__ import annotations
from typing import List, Union

from mininet.log import info

from meshpay.attack.base import AttackHandler
from meshpay.examples.emulation.topology import EmulationContext
from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client import Client


class StoppingAttack(AttackHandler):
    """Simulates permanent node crashes at startup."""

    def __init__(self) -> None:
        self.stopped_nodes: List[Union[Client, WiFiAuthority]] = []

    def setup(self, context: EmulationContext, intensity: float, target: str) -> None:
        if intensity <= 0:
            return

        nodes: List[Union[Client, WiFiAuthority]] = []
        if target == "authority":
            nodes = list(context.authorities)
        elif target == "client" or not target:
            nodes = list(context.clients)
        else:
            matched = [n for n in list(context.clients) + list(context.authorities) if n.name == target]
            nodes = matched if matched else list(context.clients)

        # Count of nodes to shut down based on intensity
        num_to_stop = max(1, int(intensity * len(nodes)))
        nodes_to_stop = nodes[:num_to_stop]

        info(f"💥 [Attack: stopping] Shutting down fastpay services permanently on nodes: {[n.name for n in nodes_to_stop]}\n")
        for node in nodes_to_stop:
            try:
                node.stop_fastpay_services()
                self.stopped_nodes.append(node)
            except Exception:
                pass

    def teardown(self, context: EmulationContext) -> None:
        info("🧹 [Attack: stopping] Restarting permanently stopped node services...\n")
        for node in self.stopped_nodes:
            try:
                node.start_fastpay_services()
            except Exception:
                pass
        self.stopped_nodes.clear()
