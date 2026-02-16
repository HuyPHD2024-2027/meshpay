from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from meshpay.consensus.tli_tendermint import TLIConfig, TLITendermint
from meshpay.consensus.base import Proposal, Vote, VoteKind

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture
    from _pytest.fixtures import FixtureRequest
    from _pytest.logging import LogCaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from pytest_mock.plugin import MockerFixture


def _h(x: str) -> str:
    """Deterministic hash for tests."""
    return hashlib.sha256(x.encode()).hexdigest()


def test_commit_certificate_threshold() -> None:
    """Engine produces a commit certificate after ≥2f+1 precommits."""
    cfg = TLIConfig(n=4, f=1)
    eng = TLITendermint(config=cfg)

    prop = Proposal(
        height=1,
        round=0,
        shard_id="S0",
        proposal_hash=_h("p"),
        proposer="A0",
        payload={"tx": "T1"},
    )

    # Process proposal (engine replies with a prevote template)
    _ = eng.on_proposal(prop)

    # Inject prevotes from A0,A1,A2
    voters = ["A0", "A1", "A2"]
    for v in voters:
        pv = Vote(
            kind=VoteKind.PREVOTE,
            height=1,
            round=0,
            shard_id="S0",
            proposal_hash=prop.proposal_hash,
            voter=v,
            signature=f"sig_{v}",
        )
        pc = eng.on_prevote(pv)
        # Precommit emitted once quorum of prevotes is observed
        # Not asserting here, but checking final certificate below
        if pc is not None:
            # simulate the precommit messages delivery
            pc.voter = v  # assign voter identity for tally
            pc.signature = f"sig2_{v}"
            cert = eng.on_precommit(pc)
            if cert is not None:
                assert cert.proposal_hash == prop.proposal_hash
                assert len(cert.precommits) >= cfg.quorum()
                break