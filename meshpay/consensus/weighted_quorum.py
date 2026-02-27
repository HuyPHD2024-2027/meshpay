from __future__ import annotations

"""Weighted quorum helpers.

This module contains small, pure functions to determine whether a set of
signers satisfies a quorum under either equal-weight or weighted-power
membership. It is intentionally independent from Mininet-WiFi classes so
that it can be unit-tested in isolation.
"""

from math import ceil
from typing import Iterable, Mapping, Set

from mn_wifi.committee import Committee


def required_equal_quorum_count(num_authorities: int, ratio: float = 2.0 / 3.0) -> int:
    """Return the minimum number of signers for an equal-weight quorum.

    Args:
        num_authorities: Committee size.
        ratio: Fraction required to reach quorum (default: 2/3).

    Returns:
        The smallest integer k such that k >= num_authorities * ratio, plus one
        to replicate the traditional "2/3 + 1" rule.
    """
    if num_authorities <= 0:
        return 0
    threshold = int(num_authorities * ratio)
    return threshold + 1


def has_equal_quorum(signers: Iterable[str], *, num_authorities: int, ratio: float = 2.0 / 3.0) -> bool:
    """Check equal-weight quorum based on cardinality.

    Args:
        signers: Iterable of authority names that signed.
        num_authorities: Committee size.
        ratio: Fraction required to reach quorum (default: 2/3).

    Returns:
        True if the number of distinct signers reaches the equal-weight threshold.
    """
    unique: Set[str] = set(signers)
    return len(unique) >= required_equal_quorum_count(num_authorities, ratio)


def has_weighted_quorum(signers: Iterable[str], *, committee: Committee, ratio: float = 2.0 / 3.0) -> bool:
    """Check weighted quorum using the committee's dynamic voting power.

    Args:
        signers: Iterable of authority names that signed.
        committee: Committee instance providing current voting power per authority.
        ratio: Fraction of total power required to reach quorum (default: 2/3).

    Returns:
        True if the cumulative voting power of ``signers`` meets or exceeds the
        committee's quorum threshold.
    """
    signer_set: Set[str] = set(signers)
    # Committee.quorum_threshold returns the floating threshold directly
    power_sum = sum(committee.power(a) for a in signer_set)
    return power_sum >= committee.quorum_threshold(ratio)


__all__ = [
    "required_equal_quorum_count",
    "has_equal_quorum",
    "has_weighted_quorum",
]
