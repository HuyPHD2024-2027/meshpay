#!/usr/bin/env python3

from __future__ import annotations

import heapq
import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from dtn.bundle import Bundle


class BundleStore:
    """Persistent local bundle store for one DTN node.

    This store is used by multiple router threads, so all cache/file operations
    are protected by a re-entrant lock.

    Behaviour:
        - bundle files are written atomically via tmp + os.replace()
        - events are appended to events.jsonl
        - deliveries are appended to delivered.log
        - confirmation_order acts as a vaccine for old transfer/signed bundles
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

        self.delivered_log = self.path / "delivered.log"
        self.events_log = self.path / "events.jsonl"

        self._lock = threading.RLock()

        # In-memory cache: bundle_id -> Bundle
        self._cache: Dict[str, Bundle] = {}
        self._cached_mtime: float = -1.0
        self._last_refresh_time: float = 0.0
        self._refresh_interval: float = 1.0

        # order_id values with confirmation_order in this store.
        self.confirmed_order_ids: set[str] = set()

    def bundle_path(self, bundle_id: str) -> Path:
        return self.path / f"{bundle_id}.json"

    def has(self, bundle_id: str) -> bool:
        with self._lock:
            if bundle_id in self._cache:
                return True

            return self.bundle_path(bundle_id).exists()

    def save(self, bundle: Bundle) -> None:
        with self._lock:
            path = self.bundle_path(bundle.bundle_id)
            tmp = self.path / f"{bundle.bundle_id}.json.tmp"

            payload = bundle.to_dict()

            with tmp.open("w", encoding="utf-8") as f:
                json.dump(payload, f, sort_keys=True)
                f.write("\n")

            os.replace(tmp, path)

            self._cache[bundle.bundle_id] = bundle

            if (
                isinstance(bundle.payload, dict)
                and bundle.payload.get("type") == "confirmation_order"
            ):
                order_id = (bundle.payload.get("data", {}).get("order_id") or bundle.payload.get("data", {}).get("i"))

                if order_id:
                    self.confirmed_order_ids.add(order_id)
                    self.prune_by_order_id(order_id)

            self._refresh_mtime_unlocked()

    def prune_by_order_id(self, order_id: str) -> None:
        with self._lock:
            to_delete = []

            for bundle_id, bundle in list(self._cache.items()):
                if not isinstance(bundle.payload, dict):
                    continue

                payload_type = bundle.payload.get("type")

                if payload_type not in {"transfer_order", "signed_transfer_order"}:
                    continue

                candidate_order_id = (bundle.payload.get("data", {}).get("order_id") or bundle.payload.get("data", {}).get("i"))

                if candidate_order_id == order_id:
                    to_delete.append(bundle_id)

            for bundle_id in to_delete:
                self._delete_bundle_files_unlocked(bundle_id)
                self._cache.pop(bundle_id, None)

            self._refresh_mtime_unlocked()

    def load(self, bundle_id: str) -> Optional[Bundle]:
        with self._lock:
            cached = self._cache.get(bundle_id)

            if cached is not None:
                return cached

            path = self.bundle_path(bundle_id)

            if not path.exists():
                return None

            try:
                with path.open("r", encoding="utf-8") as f:
                    bundle = Bundle.from_dict(json.load(f))

                self._cache[bundle_id] = bundle
                return bundle

            except Exception:
                return None

    def all(self) -> List[Bundle]:
        """Return all non-expired bundles."""

        with self._lock:
            self._refresh_cache_if_needed_unlocked()
            self._prune_expired_unlocked()

            return [
                bundle
                for bundle in self._cache.values()
                if not bundle.expired()
            ]

    def ids(self) -> set[str]:
        return {bundle.bundle_id for bundle in self.all()}

    def unknown_to_peer(
        self,
        peer_ids: Iterable[str],
        peer_node: str | None = None,
        limit: Optional[int] = None,
    ) -> List[Bundle]:
        if limit is not None and limit <= 0:
            return []

        known = set(peer_ids)

        bundles = [
            bundle
            for bundle in self.all()
            if bundle.bundle_id not in known and not bundle.expired()
        ]

        def priority(bundle: Bundle) -> tuple[int, int, float]:
            payload_type = None

            if isinstance(bundle.payload, dict):
                payload_type = bundle.payload.get("type")

            type_priority = {
                "confirmation_order": 0,
                "signed_transfer_order": 1,
                "transfer_order": 2,
            }.get(payload_type, 3)

            destination_priority = 0 if peer_node and bundle.dst == peer_node else 1

            return (type_priority, destination_priority, bundle.created_at)

        if limit is None or len(bundles) <= limit:
            return sorted(bundles, key=priority)

        return heapq.nsmallest(limit, bundles, key=priority)

    def record_event(self, event: dict) -> None:
        event = dict(event)
        event.setdefault("time", time.time())

        line = json.dumps(event, sort_keys=True)

        with self._lock:
            with self.events_log.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def mark_delivered(self, bundle: Bundle, node: str) -> bool:
        with self._lock:
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
                json.dump(record, f, indent=2, sort_keys=True)
                f.write("\n")

            with self.delivered_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, sort_keys=True) + "\n")

            # Avoid calling record_event() while holding a non-reentrant lock.
            # We use RLock, so this is safe.
            self.record_event(record)

            return True

    # ------------------------------------------------------------------
    # Internal helpers. Caller must hold self._lock.
    # ------------------------------------------------------------------

    def _refresh_mtime_unlocked(self) -> None:
        try:
            self._cached_mtime = os.stat(self.path).st_mtime
        except Exception:
            self._cached_mtime = -1.0
        self._last_refresh_time = time.time()

    def _refresh_cache_if_needed_unlocked(self) -> None:
        now = time.time()
        if now - self._last_refresh_time < self._refresh_interval:
            return
        self._last_refresh_time = now

        try:
            mtime = os.stat(self.path).st_mtime
        except Exception:
            mtime = -1.0

        if mtime == self._cached_mtime:
            return

        self._cached_mtime = mtime

        on_disk: Dict[str, str] = {}

        try:
            for entry in os.scandir(self.path):
                if not entry.is_file():
                    continue

                if not entry.name.endswith(".json"):
                    continue

                if entry.name.endswith(".json.tmp"):
                    continue

                on_disk[entry.name[:-5]] = entry.path

        except Exception:
            pass

        stale_ids = [
            bundle_id
            for bundle_id in list(self._cache.keys())
            if bundle_id not in on_disk
        ]

        for bundle_id in stale_ids:
            self._cache.pop(bundle_id, None)

        for bundle_id, file_path in on_disk.items():
            if bundle_id in self._cache:
                continue

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    bundle = Bundle.from_dict(json.load(f))

                self._cache[bundle_id] = bundle

            except Exception:
                continue

        self._rebuild_confirmed_order_ids_unlocked()

        for order_id in list(self.confirmed_order_ids):
            self.prune_by_order_id(order_id)

    def _rebuild_confirmed_order_ids_unlocked(self) -> None:
        confirmed = set()

        for bundle in self._cache.values():
            if not isinstance(bundle.payload, dict):
                continue

            if bundle.payload.get("type") != "confirmation_order":
                continue

            order_id = (bundle.payload.get("data", {}).get("order_id") or bundle.payload.get("data", {}).get("i"))

            if order_id:
                confirmed.add(order_id)

        self.confirmed_order_ids = confirmed

    def _prune_expired_unlocked(self) -> None:
        now = time.time()
        expired_ids = []

        for bundle_id, bundle in list(self._cache.items()):
            if now > bundle.created_at + bundle.ttl:
                expired_ids.append(bundle_id)

        if not expired_ids:
            return

        for bundle_id in expired_ids:
            self._delete_bundle_files_unlocked(bundle_id)
            self._cache.pop(bundle_id, None)

        self._refresh_mtime_unlocked()

    def _delete_bundle_files_unlocked(self, bundle_id: str) -> None:
        try:
            self.bundle_path(bundle_id).unlink(missing_ok=True)
        except Exception:
            pass

        try:
            (self.path / f"delivered-{bundle_id}.txt").unlink(missing_ok=True)
        except Exception:
            pass