from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from meshpay.messages import (
    PreendorsementMessage,
    CertificateMessage,
    AnchorCommitmentMessage,
    ReconcileRequestMessage,
    ReconcileResponseMessage,
)

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture
    from _pytest.fixtures import FixtureRequest
    from _pytest.logging import LogCaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from pytest_mock.plugin import MockerFixture


def test_preendorsement_roundtrip() -> None:
    """Ensure PreendorsementMessage serializes and parses."""
    m = PreendorsementMessage(
        order_id=uuid4(),
        authority="A1",
        proposal_hash="h",
        signature="sig",
    )
    p = m.to_payload()
    m2 = PreendorsementMessage.from_payload(p)
    assert m2.order_id == m.order_id
    assert m2.authority == "A1"
    assert m2.proposal_hash == "h"
    assert m2.signature == "sig"