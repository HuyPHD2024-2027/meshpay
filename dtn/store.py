#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from dtn.bundle import Bundle


class BundleStore:
    """Persistent local bundle store for one DTN node."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

        self.delivered_log = self.path / "delivered.log"
        self.events_log = self.path / "events.jsonl"

    def bundle_path(self, bundle_id: str) -> Path:
        return self.path / f"{bundle_id}.json"

    def has(self, bundle_id: str) -> bool:
        return self.bundle_path(bundle_id).exists()

    def save(self, bundle: Bundle) -> None:
        path = self.bundle_path(bundle.bundle_id)
        tmp = self.path / f"{bundle.bundle_id}.json.tmp"

        with tmp.open("w", encoding="utf-8") as f:
            json.dump(bundle.to_dict(), f)

        os.replace(tmp, path)

    def load(self, bundle_id: str) -> Optional[Bundle]:
        path = self.bundle_path(bundle_id)

        if not path.exists():
            return None

        try:
            with path.open("r", encoding="utf-8") as f:
                return Bundle.from_dict(json.load(f))
        except Exception:
            return None

    def all(self) -> List[Bundle]:
        bundles: List[Bundle] = []

        for path in self.path.glob("*.json"):
            try:
                with path.open("r", encoding="utf-8") as f:
                    bundle = Bundle.from_dict(json.load(f))

                if not bundle.expired():
                    bundles.append(bundle)

            except Exception:
                continue

        return bundles

    def ids(self) -> set[str]:
        return {bundle.bundle_id for bundle in self.all()}

    def unknown_to_peer(self, peer_ids: Iterable[str]) -> List[Bundle]:
        known = set(peer_ids)
        return [
            bundle
            for bundle in self.all()
            if bundle.bundle_id not in known and not bundle.expired()
        ]

    def record_event(self, event: dict) -> None:
        event = dict(event)
        event.setdefault("time", time.time())

        with self.events_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    def mark_delivered(self, bundle: Bundle, node: str) -> bool:
        marker = self.path / f"delivered-{bundle.bundle_id}.txt"

        if marker.exists():
            return False

        delivered_at = time.time()
        latency_ms = (delivered_at - bundle.created_at) * 1000.0

        record = {
            "time": delivered_at,
            "event": "delivered",
            "node": node,
            "bundle_id": bundle.bundle_id,
            "src": bundle.src,
            "dst": bundle.dst,
            "latency_ms": latency_ms,
            "size_bytes": bundle.size_bytes,
            "hops": bundle.hops,
            "payload": bundle.payload,
        }

        with marker.open("w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
            f.write("\n")

        with self.delivered_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

        self.record_event(record)

        return True