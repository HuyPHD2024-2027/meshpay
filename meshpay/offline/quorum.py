#!/usr/bin/env python3

from __future__ import annotations

from typing import Iterable

from meshpay.offline.crypto import verify_signature
from meshpay.offline.weighted_quorum import WeightSnapshot


def authority_vote_signing_dict(order, authority: str, epoch: int, weight_units: int,
                                total_weight_units: int, committee_digest: str) -> dict:
    return {
        "transfer_order": order.signing_dict(),
        "authority": authority,
        "epoch": int(epoch),
        "weight_units": int(weight_units),
        "total_weight_units": int(total_weight_units),
        "committee_digest": committee_digest,
    }


def verify_authority_vote(order, vote, snapshot: WeightSnapshot) -> bool:
    if vote.authority not in snapshot.committee:
        return False
    if vote.epoch != snapshot.epoch or vote.committee_digest != snapshot.committee_digest:
        return False
    if vote.total_weight_units != snapshot.total_weight_units:
        return False
    if vote.weight_units != snapshot.weight_for(vote.authority):
        return False
    return verify_signature(
        vote.authority,
        authority_vote_signing_dict(order, vote.authority, vote.epoch, vote.weight_units,
                                    vote.total_weight_units, vote.committee_digest),
        vote.signature,
    )


def has_weighted_quorum(order, votes: Iterable, snapshot: WeightSnapshot) -> bool:
    seen = set()
    total = 0
    for vote in votes:
        if vote.authority in seen or not verify_authority_vote(order, vote, snapshot):
            continue
        seen.add(vote.authority)
        total += vote.weight_units
    return total * 3 > snapshot.total_weight_units * 2


def quorum_threshold(committee_size: int) -> int:
    """Return FastPay-style quorum threshold.

    For N authorities:
        quorum = floor(2N / 3) + 1
    """

    if committee_size <= 0:
        return 1

    return int(committee_size * 2 / 3) + 1
