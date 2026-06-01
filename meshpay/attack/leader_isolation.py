"""Leader isolation attack bringing authority wireless interfaces down."""

from __future__ import annotations
from typing import List

from mininet.log import info

from meshpay.attack.base import AttackHandler
from meshpay.examples.emulation.topology import EmulationContext
from meshpay.nodes.authority import WiFiAuthority


class LeaderIsolationAttack(AttackHandler):
    """Disconnects WiFi interfaces on selected authorities to simulate partition."""

    def __init__(self) -> None:
        self.isolated_nodes: List[WiFiAuthority] = []

    def setup(self, context: EmulationContext, intensity: float, target: str) -> None:
        if intensity <= 0 or not context.authorities:
            return

        # Isolate a subset of authorities proportional to intensity
        num_to_isolate = max(1, int(intensity * len(context.authorities)))
        target_auths = context.authorities[:num_to_isolate]

        info(f"💥 [Attack: leader_isolation] Bringing wireless interface DOWN for authorities: {[a.name for a in target_auths]}\n")
        for auth in target_auths:
            intf_name = f"{auth.name}-wlan0"
            auth.cmd(f"ip link set dev {intf_name} down")
            self.isolated_nodes.append(auth)

    def teardown(self, context: EmulationContext) -> None:
        info("🧹 [Attack: leader_isolation] Bringing wireless interfaces back UP...\n")
        for auth in self.isolated_nodes:
            intf_name = f"{auth.name}-wlan0"
            auth.cmd(f"ip link set dev {intf_name} up")
        self.isolated_nodes.clear()
