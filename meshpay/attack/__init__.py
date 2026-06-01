"""MeshPay Emulation Attack Injection Package.

Provides a registry framework for executing diverse opportunist network attacks.
"""

from __future__ import annotations
from typing import Dict, Type

from meshpay.attack.base import AttackHandler
from meshpay.attack.jamming import GrayholeAttack, JammingAttack
from meshpay.attack.targeted_load import TargetedLoadAttack
from meshpay.attack.leader_isolation import LeaderIsolationAttack
from meshpay.attack.transient_failure import TransientFailureAttack
from meshpay.attack.stopping import StoppingAttack

# Centralized Registry mapping of attack type strings to handler classes.
ATTACK_REGISTRY: Dict[str, Type[AttackHandler]] = {
    # --- Packet-loss family (replaces legacy no-op packet_loss) ---
    "jamming": JammingAttack,      # Option A: RF channel flooding via iperf3
    "grayhole": GrayholeAttack,    # Option B: selective FastPay certificate drop via tc
    # --- Other adversarial scenarios ---
    "targeted_load": TargetedLoadAttack,
    "leader_isolation": LeaderIsolationAttack,
    "transient_failure": TransientFailureAttack,
    "stopping": StoppingAttack,
}

__all__ = [
    "AttackHandler",
    "ATTACK_REGISTRY",
    "JammingAttack",
    "GrayholeAttack",
    "TargetedLoadAttack",
    "LeaderIsolationAttack",
    "TransientFailureAttack",
    "StoppingAttack",
]
