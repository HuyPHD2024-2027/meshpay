"""Base interface for dynamic emulation attack injectors in MeshPay."""

from __future__ import annotations
from meshpay.examples.emulation.topology import EmulationContext


class AttackHandler:
    """Interface for dynamic emulation attack injection."""

    def setup(self, context: EmulationContext, intensity: float, target: str) -> None:
        """Inject the attack logic (runs at workload start)."""
        pass

    def teardown(self, context: EmulationContext) -> None:
        """Restore interface and node states (runs at node shutdown)."""
        pass
