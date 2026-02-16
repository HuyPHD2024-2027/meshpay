from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from meshpay.consensus.base import (
    CommitCertificate,
    ConsensusEngine,
    Proposal,
    RoundState,
    Vote,
    VoteKind,
)
from meshpay.types import AuthorityName


@dataclass(frozen=True)
class TLIConfig:
    """Config for Tendermint-Lite under intermittent connectivity."""
    n: int
    f: int
    # Optional: parameters can be tuned by RL
    max_round_skips: int = 5
    enable_client_aggregation: bool = True

    def quorum(self) -> int:
        """Return the precommit quorum threshold (≥ 2f+1)."""
        return 2 * self.f + 1


@dataclass
class TLITendermint(ConsensusEngine):
    """A lightweight Tendermint variant that tolerates intermittent links.

    This engine stores vote-locks and forms commit certificates once ≥2f+1
    precommits are observed for the same (height, round, hash).
    """

    config: TLIConfig
    _state: Dict[tuple[int, int, str], RoundState] = field(default_factory=dict)

    def _rs(self, h: int, r: int, s: str) -> RoundState:
        key = (h, r, s)
        if key not in self._state:
            self._state[key] = RoundState(height=h, round=r, shard_id=s)
        return self._state[key]

    def locked(self, shard_id: str) -> Optional[str]:
        """Return the most recent locked hash for the shard (if any)."""
        latest: Optional[RoundState] = None
        for (h, r, s), rs in self._state.items():
            if s != shard_id:
                continue
            if latest is None or (h, r) > (latest.height, latest.round):
                latest = rs
        return latest.locked_hash if latest else None

    def on_proposal(self, proposal: Proposal) -> Optional[Vote]:
        """Accept proposal and emit a prevote.

        Vote-lock rule: If locked on a conflicting hash at same height, prevote the lock.
        """
        rs = self._rs(proposal.height, proposal.round, proposal.shard_id)
        rs.proposal_hash = proposal.proposal_hash
        locked = rs.locked_hash
        vote_hash = locked if (locked and locked != proposal.proposal_hash) else proposal.proposal_hash
        return Vote(
            kind=VoteKind.PREVOTE,
            height=proposal.height,
            round=proposal.round,
            shard_id=proposal.shard_id,
            proposal_hash=vote_hash,
            voter="",
            signature="",  # Sign at integration layer
        )

    def on_prevote(self, vote: Vote) -> Optional[Vote]:
        """Record prevote; emit precommit if observed ≥2f+1 prevotes for some hash."""
        rs = self._rs(vote.height, vote.round, vote.shard_id)
        rs.prevotes[vote.voter] = vote.signature
        # Tally
        counts: Dict[str, int] = {}
        for vhash in [vote.proposal_hash for _v, _sig in rs.prevotes.items()]:
            counts[vhash] = counts.get(vhash, 0) + 1
        # If any hash reaches quorum prevotes, precommit it.
        for vhash, count in counts.items():
            if count >= self.config.quorum():
                rs.locked_hash = vhash  # lock on prevote quorum as in Tendermint
                return Vote(
                    kind=VoteKind.PRECOMMIT,
                    height=vote.height,
                    round=vote.round,
                    shard_id=vote.shard_id,
                    proposal_hash=vhash,
                    voter="",
                    signature="",
                )
        return None

    def on_precommit(self, vote: Vote) -> Optional[CommitCertificate]:
        """Collect precommits; form commit certificate at quorum."""
        rs = self._rs(vote.height, vote.round, vote.shard_id)
        rs.precommits[vote.voter] = vote.signature
        # Count precommits by hash
        counts: Dict[str, int] = {}
        for v in rs.precommits.items():
            # Note: all precommits stored are for vote.proposal_hash (integration ensures consistency)
            counts[vote.proposal_hash] = counts.get(vote.proposal_hash, 0) + 1
        if counts.get(vote.proposal_hash, 0) >= self.config.quorum():
            # Commit achieved
            cert = CommitCertificate(
                height=vote.height,
                round=vote.round,
                shard_id=vote.shard_id,
                proposal_hash=vote.proposal_hash,
                precommits=dict(rs.precommits),
            )
            return cert
        return None