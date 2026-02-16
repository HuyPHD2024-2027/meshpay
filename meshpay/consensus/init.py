"""Consensus package for MeshPay.

Provides interfaces and a Tendermint-Lite (TLI) implementation tailored for
intermittent mesh networks.
"""

from __future__ import annotations

from .base import (
    Proposal,
    VoteKind,
    Vote,
    CommitCertificate,
    RoundState,
    ConsensusEngine,
)
from .tli_tendermint import TLIConfig, TLITendermint

__all__ = [
    "Proposal",
    "VoteKind",
    "Vote",
    "CommitCertificate",
    "RoundState",
    "ConsensusEngine",
    "TLIConfig",
    "TLITendermint",
]