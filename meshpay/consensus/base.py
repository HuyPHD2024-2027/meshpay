from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, MutableMapping, Optional, Protocol, Set
from uuid import UUID

from meshpay.types import AuthorityName


@dataclass(frozen=True)
class Proposal:
    """A proposal for a block or single-transfer commit at (height, round)."""

    height: int
    round: int
    shard_id: str
    proposal_hash: str
    proposer: AuthorityName
    payload: Dict[str, Any]


class VoteKind:
    """Enumeration of vote kinds used by Tendermint-like protocols."""
    PREVOTE: str = "prevote"
    PRECOMMIT: str = "precommit"


@dataclass(frozen=True)
class Vote:
    """A (prevote|precommit) for a proposal hash at (height, round)."""

    kind: str
    height: int
    round: int
    shard_id: str
    proposal_hash: str
    voter: AuthorityName
    signature: str


@dataclass
class CommitCertificate:
    """Commit certificate assembled from ≥2f+1 precommits."""

    height: int
    round: int
    shard_id: str
    proposal_hash: str
    precommits: Mapping[AuthorityName, str]  # voter -> signature


@dataclass
class RoundState:
    """Local per-(height, round) state tracking votes and locks."""

    height: int
    round: int
    shard_id: str
    proposal_hash: Optional[str] = None
    locked_hash: Optional[str] = None
    prevotes: MutableMapping[AuthorityName, str] = field(default_factory=dict)
    precommits: MutableMapping[AuthorityName, str] = field(default_factory=dict)


class ConsensusEngine(Protocol):
    """Protocol for a minimal Tendermint-like consensus engine."""

    def on_proposal(self, proposal: Proposal) -> Optional[Vote]:
        """Handle a proposal; optionally emit a prevote."""

    def on_prevote(self, vote: Vote) -> Optional[Vote]:
        """Handle a prevote; optionally emit a precommit."""

    def on_precommit(self, vote: Vote) -> Optional[CommitCertificate]:
        """Handle a precommit; possibly return a commit certificate."""

    def locked(self, shard_id: str) -> Optional[str]:
        """Return the currently locked proposal hash (if any) for the shard."""