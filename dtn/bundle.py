#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class Bundle:
    """A DTN bundle.

    The Epidemic router only understands routing metadata:
    source, destination, creation time, TTL, hop trace, and opaque payload.

    Future MeshPay/FastPay payment messages should be placed inside `payload`.
    The DTN layer must not interpret payment fields.
    """

    bundle_id: str
    src: str
    dst: str
    payload: Dict[str, Any]
    created_at: float
    ttl: float
    hops: List[str] = field(default_factory=list)
    size_bytes: int = 0

    @classmethod
    def create(
        cls,
        src: str,
        dst: str,
        payload: Dict[str, Any],
        ttl: float = 300.0,
    ) -> "Bundle":
        created_at = time.time()

        raw = json.dumps(
            {
                "src": src,
                "dst": dst,
                "payload": payload,
                "created_at": created_at,
            },
            sort_keys=True,
        ).encode("utf-8")

        bundle_id = hashlib.sha256(raw).hexdigest()[:24]
        size_bytes = len(json.dumps(payload).encode("utf-8"))

        return cls(
            bundle_id=bundle_id,
            src=src,
            dst=dst,
            payload=payload,
            created_at=created_at,
            ttl=ttl,
            hops=[src],
            size_bytes=size_bytes,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Bundle":
        return cls(
            bundle_id=data["bundle_id"],
            src=data["src"],
            dst=data["dst"],
            payload=data["payload"],
            created_at=float(data["created_at"]),
            ttl=float(data["ttl"]),
            hops=list(data.get("hops", [])),
            size_bytes=int(data.get("size_bytes", 0)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def expired(self, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return current > self.created_at + self.ttl

    def add_hop(self, node: str) -> None:
        if node not in self.hops:
            self.hops.append(node)

    def is_delivered_to(self, node: str) -> bool:
        return self.dst == node