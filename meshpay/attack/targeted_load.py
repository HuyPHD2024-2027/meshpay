"""Targeted load attack spams transactions from an adversarial client."""

from __future__ import annotations
import random
import threading
import time

from mininet.log import info
from mn_wifi.services.core.config import SUPPORTED_TOKENS

from meshpay.attack.base import AttackHandler
from meshpay.examples.emulation.topology import EmulationContext


class TargetedLoadAttack(AttackHandler):
    """Spams offline payment transactions from an adversarial mule node."""

    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.flood_thread: threading.Thread | None = None

    def setup(self, context: EmulationContext, intensity: float, target: str) -> None:
        if intensity <= 0 or not context.clients:
            return

        # Last client acts as adversarial spammer
        attacker = context.clients[-1]
        recipients = [c.name for c in context.clients if c.name != attacker.name]
        if not recipients:
            return

        xtz_token = SUPPORTED_TOKENS.get("XTZ", {}).get("address", "")
        # Scale flood delay based on intensity: higher intensity -> lower interval
        flood_interval = max(0.02, 2.0 - intensity * 1.98)
        
        info(f"💥 [Attack: targeted_load] Spammer {attacker.name} flooding transactions (interval: {flood_interval:.3f}s)\n")

        def flood_loop() -> None:
            while not self.stop_event.is_set():
                dest = random.choice(recipients)
                amount = random.randint(1, 5)
                try:
                    attacker.transfer(dest, xtz_token, amount)
                except Exception:
                    pass
                time.sleep(flood_interval)

        self.stop_event.clear()
        self.flood_thread = threading.Thread(target=flood_loop, daemon=True)
        self.flood_thread.start()

    def teardown(self, context: EmulationContext) -> None:
        if self.flood_thread:
            info("🧹 [Attack: targeted_load] Stopping transaction flood thread...\n")
            self.stop_event.set()
            self.flood_thread.join(timeout=1.0)
