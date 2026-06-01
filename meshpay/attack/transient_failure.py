"""Transient failure attack cycling fastpay node processes on and off."""

from __future__ import annotations
import random
import threading
import time
from typing import List, Union

from mininet.log import info

from meshpay.attack.base import AttackHandler
from meshpay.examples.emulation.topology import EmulationContext
from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client import Client


class TransientFailureAttack(AttackHandler):
    """Simulates dynamic node churn / sleep cycles by toggling services."""

    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.cycle_thread: threading.Thread | None = None

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

        if not nodes:
            return

        # Frequency of toggles based on intensity (lower delay for higher intensity)
        cycle_period = max(3.0, 30.0 - intensity * 27.0)
        info(f"💥 [Attack: transient_failure] Cycling fastpay services on {[n.name for n in nodes]} every {cycle_period:.1f}s\n")

        def cycle_loop() -> None:
            while not self.stop_event.is_set():
                node = random.choice(nodes)
                try:
                    node.stop_fastpay_services()
                    time.sleep(min(cycle_period * 0.4, 5.0))
                    if not self.stop_event.is_set():
                        node.start_fastpay_services()
                except Exception:
                    pass
                time.sleep(cycle_period)

        self.stop_event.clear()
        self.cycle_thread = threading.Thread(target=cycle_loop, daemon=True)
        self.cycle_thread.start()

    def teardown(self, context: EmulationContext) -> None:
        if self.cycle_thread:
            info("🧹 [Attack: transient_failure] Stopping transient service toggler...\n")
            self.stop_event.set()
            self.cycle_thread.join(timeout=1.0)
