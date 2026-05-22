"""Canonical encoding and file loading for signed MeshPay policies."""

from __future__ import annotations

import copy
import json
import hashlib
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping
from uuid import UUID


_HASH_EXCLUDED_FIELDS = {"signatures", "policy_hash"}


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    return value


def canonical_policy_dict(
    policy: Mapping[str, Any],
    *,
    include_signatures: bool = False,
) -> Dict[str, Any]:
    """Return a normalized policy dictionary suitable for canonical hashing."""

    normalized = _jsonable(copy.deepcopy(dict(policy)))
    if not include_signatures:
        for field in _HASH_EXCLUDED_FIELDS:
            normalized.pop(field, None)
    return normalized


def canonical_policy_bytes(
    policy: Mapping[str, Any],
    *,
    include_signatures: bool = False,
) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a policy."""

    canonical = canonical_policy_dict(policy, include_signatures=include_signatures)
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return encoded.encode("utf-8")


def canonical_policy_hash(policy: Mapping[str, Any]) -> str:
    """Return the SHA-256 hash of a policy excluding signatures."""

    return hashlib.sha256(canonical_policy_bytes(policy)).hexdigest()


def policy_with_hash(policy: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``policy`` with its canonical hash stored."""

    result = copy.deepcopy(dict(policy))
    result["policy_hash"] = canonical_policy_hash(result)
    return result


def loads_policy(data: str) -> Dict[str, Any]:
    """Load a JSON policy string."""

    return json.loads(data)


def dumps_policy(policy: Mapping[str, Any], *, include_signatures: bool = True) -> str:
    """Serialize a policy as stable JSON."""

    return json.dumps(
        canonical_policy_dict(policy, include_signatures=include_signatures),
        sort_keys=True,
        indent=2,
        ensure_ascii=True,
    )


def load_policy_file(path: str) -> Dict[str, Any]:
    """Load a JSON or YAML policy file."""

    policy_path = Path(path)
    raw = policy_path.read_text(encoding="utf-8")
    if policy_path.suffix.lower() == ".json":
        return json.loads(raw)

    try:
        import yaml  # type: ignore
    except ImportError:
        loaded = json.loads(raw)
    else:
        loaded = yaml.safe_load(raw)
    if not isinstance(loaded, dict):
        raise ValueError(f"Policy file must contain a mapping: {policy_path}")
    return loaded
