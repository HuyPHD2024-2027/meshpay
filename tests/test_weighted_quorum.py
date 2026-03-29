"""Unit tests for weighted quorum helpers and Committee logic.

These tests focus on pure logic and avoid any Mininet-WiFi dependency beyond
constructing the Committee class and calling its methods.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mn_wifi.committee import Committee
from meshpay.consensus.weighted_quorum import (
    has_equal_quorum,
    has_weighted_quorum,
    required_equal_quorum_count,
)

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from _pytest.capture import CaptureFixture
    from _pytest.fixtures import FixtureRequest
    from _pytest.logging import LogCaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from pytest_mock.plugin import MockerFixture


def test_required_equal_quorum_count() -> None:
    """It computes the classic ceil(n*2/3) + 1 threshold behavior."""
    assert required_equal_quorum_count(3) == 3  # 2 + 1
    assert required_equal_quorum_count(4) == 3  # 2 + 1
    assert required_equal_quorum_count(5) == 4  # 3 + 1
    assert required_equal_quorum_count(0) == 0


def test_has_equal_quorum() -> None:
    """Equal quorum should depend only on cardinality of unique signers."""
    signers = ["a1", "a2", "a2", "a3"]  # duplicates do not count twice
    assert has_equal_quorum(signers, num_authorities=4, ratio=2.0 / 3.0) is True
    assert has_equal_quorum(["a1", "a2"], num_authorities=5, ratio=2.0 / 3.0) is False


def test_weighted_quorum_with_dynamic_powers() -> None:
    """Weighted quorum follows the cumulative voting power threshold."""
    committee = Committee({"a1": 1, "a2": 1, "a3": 1, "a4": 1})

    # Default scoring produces all zeros -> equal distribution (0.25 each)
    assert has_weighted_quorum(["a1", "a2", "a3"], committee=committee, ratio=2.0 / 3.0) is True
    assert has_weighted_quorum(["a1", "a2"], committee=committee, ratio=2.0 / 3.0) is False

    # Boost a1 heavily so that it alone crosses 2/3 threshold
    committee.update_performance("a1", {"transaction_count": 100, "error_count": 0, "network_metrics": {"connectivity_ratio": 1.0}})
    committee.update_performance("a2", {"transaction_count": 1, "error_count": 0, "network_metrics": {"connectivity_ratio": 1.0}})
    committee.update_performance("a3", {"transaction_count": 1, "error_count": 0, "network_metrics": {"connectivity_ratio": 1.0}})
    committee.update_performance("a4", {"transaction_count": 1, "error_count": 0, "network_metrics": {"connectivity_ratio": 1.0}})

    # Now a1 should hold > 2/3 power alone
    assert has_weighted_quorum(["a1"], committee=committee, ratio=2.0 / 3.0) is True
    assert has_weighted_quorum(["a2", "a3", "a4"], committee=committee, ratio=2.0 / 3.0) is False
