#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional


def canonical_json(payload: Dict[str, Any]) -> str:
    """Return deterministic JSON for signing and verification."""

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def sign_payload(node_id: str, payload: Dict[str, Any]) -> str:
    """Create a deterministic fake signature.

    This is not real cryptography. It is only for protocol development.
    """

    raw = f"{node_id}:{canonical_json(payload)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_signature(
    node_id: str,
    payload: Dict[str, Any],
    signature: Optional[str],
) -> bool:
    """Verify a deterministic fake signature."""

    if not signature:
        return False

    return signature == sign_payload(node_id, payload)