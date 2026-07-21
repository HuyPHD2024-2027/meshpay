#!/usr/bin/env python3

from __future__ import annotations


def quorum_threshold(committee_size: int) -> int:
    """Return FastPay-style quorum threshold.

    For N authorities:
        quorum = floor(2N / 3) + 1
    """

    if committee_size <= 0:
        return 1

    return int(committee_size * 2 / 3) + 1