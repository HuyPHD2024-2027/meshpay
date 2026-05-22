"""Quorum calculations for authority-approved policy activation."""

from __future__ import annotations

from typing import Any, Sequence


def required_quorum(authorities: Sequence[str], rule: Any = "two_thirds_plus_one") -> int:
    """Return the number of unique valid signatures required by ``rule``."""

    n = len(set(str(authority) for authority in authorities))
    if n <= 0:
        return 0

    if isinstance(rule, int):
        return max(1, min(rule, n))

    if isinstance(rule, str):
        normalized = rule.lower()
        if normalized == "two_thirds_plus_one":
            return int((2 * n) // 3) + 1
        if normalized == "majority":
            return int(n // 2) + 1
        if normalized == "all":
            return n
        if normalized.isdigit():
            return max(1, min(int(normalized), n))

    raise ValueError(f"Unsupported quorum rule: {rule!r}")
