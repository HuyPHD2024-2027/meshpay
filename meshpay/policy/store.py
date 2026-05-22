"""Local signed-policy store and activation logic."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from meshpay.policy.codec import canonical_policy_hash
from meshpay.policy.model import (
    SafetyLimits,
    committee_authorities,
    is_policy_expired,
    is_policy_not_yet_valid,
    merge_signatures,
    policy_epoch,
    policy_signatures,
    quorum_rule,
    validate_policy_safety,
)
from meshpay.policy.quorum import required_quorum
from meshpay.policy.signing import default_authority_keys, verify_policy_signature


@dataclass(frozen=True)
class PolicyActivationResult:
    """Result returned after a policy fragment is processed."""

    accepted: bool
    active: bool
    reason: str
    policy_hash: Optional[str] = None
    signature_count: int = 0
    required_signatures: int = 0
    policy: Optional[Dict[str, Any]] = None


class PolicyStore:
    """Collect signed policy fragments and activate only after quorum."""

    def __init__(
        self,
        authority_keys: Optional[Mapping[str, str]] = None,
        *,
        safety_limits: Optional[SafetyLimits] = None,
        now_func: Optional[Callable[[], float]] = None,
    ) -> None:
        self.authority_keys: Dict[str, str] = {
            str(authority): str(key) for authority, key in (authority_keys or {}).items()
        }
        self.safety_limits = safety_limits or SafetyLimits()
        self.now_func = now_func or time.time
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.active_policy: Optional[Dict[str, Any]] = None
        self.active_policy_hash: Optional[str] = None
        self.active_epoch: int = -1

    def _ensure_authority_keys(self, authorities: Any) -> None:
        defaults = default_authority_keys(authorities)
        for authority, key in defaults.items():
            self.authority_keys.setdefault(authority, key)

    def _reject(
        self,
        reason: str,
        *,
        policy_hash: Optional[str] = None,
        signature_count: int = 0,
        required_signatures: int = 0,
    ) -> PolicyActivationResult:
        return PolicyActivationResult(
            accepted=False,
            active=False,
            reason=reason,
            policy_hash=policy_hash,
            signature_count=signature_count,
            required_signatures=required_signatures,
        )

    def add_policy_fragment(self, policy: Mapping[str, Any]) -> PolicyActivationResult:
        """Merge a signed policy fragment and activate if quorum is reached."""

        policy_copy = copy.deepcopy(dict(policy))
        computed_hash = canonical_policy_hash(policy_copy)
        advertised_hash = policy_copy.get("policy_hash")
        if advertised_hash and str(advertised_hash) != computed_hash:
            return self._reject("policy_hash_mismatch", policy_hash=computed_hash)

        authorities = committee_authorities(policy_copy)
        if not authorities:
            return self._reject("missing_committee", policy_hash=computed_hash)
        self._ensure_authority_keys(authorities)

        required = required_quorum(authorities, quorum_rule(policy_copy))
        epoch = policy_epoch(policy_copy)
        if epoch <= self.active_epoch:
            return self._reject(
                "old_epoch",
                policy_hash=computed_hash,
                required_signatures=required,
            )

        now = self.now_func()
        if is_policy_not_yet_valid(policy_copy, now):
            return self._reject(
                "not_yet_valid",
                policy_hash=computed_hash,
                required_signatures=required,
            )
        if is_policy_expired(policy_copy, now):
            return self._reject(
                "expired",
                policy_hash=computed_hash,
                required_signatures=required,
            )

        safety_error = validate_policy_safety(policy_copy, self.safety_limits)
        if safety_error:
            return self._reject(
                safety_error,
                policy_hash=computed_hash,
                required_signatures=required,
            )

        valid_signatures: Dict[str, str] = {}
        for authority, signature in policy_signatures(policy_copy).items():
            if authority not in authorities:
                continue
            key = self.authority_keys.get(authority)
            if not key:
                continue
            if verify_policy_signature(policy_copy, authority, signature, key):
                valid_signatures[authority] = signature

        if not valid_signatures:
            return self._reject(
                "no_valid_signatures",
                policy_hash=computed_hash,
                required_signatures=required,
            )

        pending_policy = self.pending.get(computed_hash)
        if pending_policy:
            merged_signatures = policy_signatures(pending_policy)
            merged_signatures.update(valid_signatures)
            merged_policy = merge_signatures(policy_copy, merged_signatures)
        else:
            merged_policy = merge_signatures(policy_copy, valid_signatures)

        merged_policy["policy_hash"] = computed_hash
        signature_count = len(policy_signatures(merged_policy))
        self.pending[computed_hash] = merged_policy

        if signature_count >= required:
            self.active_policy = copy.deepcopy(merged_policy)
            self.active_policy_hash = computed_hash
            self.active_epoch = epoch
            self.pending.pop(computed_hash, None)
            return PolicyActivationResult(
                accepted=True,
                active=True,
                reason="activated",
                policy_hash=computed_hash,
                signature_count=signature_count,
                required_signatures=required,
                policy=copy.deepcopy(self.active_policy),
            )

        return PolicyActivationResult(
            accepted=True,
            active=False,
            reason="pending_quorum",
            policy_hash=computed_hash,
            signature_count=signature_count,
            required_signatures=required,
            policy=copy.deepcopy(merged_policy),
        )

    def get_pending_policy(self, policy_hash: str) -> Optional[Dict[str, Any]]:
        """Return a copy of a pending policy by hash."""

        policy = self.pending.get(policy_hash)
        return copy.deepcopy(policy) if policy else None
