"""Policy signing primitives.

The current MeshPay emulation does not ship authority key material, so this
module uses deterministic HMAC-SHA256 signatures behind a small API.  Replacing
the implementation with public-key signatures only needs changes here; policy
documents and the activation store continue to carry ``signatures`` in the same
authority-id keyed shape.
"""

from __future__ import annotations

import hmac
import copy
from hashlib import sha256
from typing import Any, Dict, Mapping

from meshpay.policy.codec import canonical_policy_hash


SIGNATURE_SCHEME = "hmac-sha256"


def default_authority_key(authority_id: str) -> str:
    """Return a deterministic emulation key for an authority id."""

    return f"meshpay-demo-policy-key:{authority_id}"


def default_authority_keys(authorities: Any) -> Dict[str, str]:
    """Return deterministic emulation keys for a collection of authorities."""

    return {str(authority): default_authority_key(str(authority)) for authority in authorities}


def _signature_digest(policy_hash: str, authority_id: str, key: str) -> str:
    payload = f"{authority_id}:{policy_hash}".encode("utf-8")
    return hmac.new(str(key).encode("utf-8"), payload, sha256).hexdigest()


def sign_policy(
    policy: Mapping[str, Any],
    authority_id: str,
    signing_key: str,
) -> Dict[str, Any]:
    """Return a copy of ``policy`` signed by ``authority_id``."""

    signed = copy.deepcopy(dict(policy))
    policy_hash = canonical_policy_hash(signed)
    signature = f"{SIGNATURE_SCHEME}:{_signature_digest(policy_hash, authority_id, signing_key)}"
    signatures = dict(signed.get("signatures") or {})
    signatures[str(authority_id)] = signature
    signed["signatures"] = signatures
    signed["policy_hash"] = policy_hash
    return signed


def verify_policy_signature(
    policy: Mapping[str, Any],
    authority_id: str,
    signature: str,
    verification_key: str,
) -> bool:
    """Verify one authority signature over the canonical policy hash."""

    policy_hash = canonical_policy_hash(policy)
    expected = f"{SIGNATURE_SCHEME}:{_signature_digest(policy_hash, authority_id, verification_key)}"
    return hmac.compare_digest(str(signature), expected)
