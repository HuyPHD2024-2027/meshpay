#!/usr/bin/env python3

from __future__ import annotations

import heapq
import json
import os
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional

from dtn.bundle import Bundle


_METRIC_EVENTS = {
    "created",
    "received",
    "delivered",
    "exchange",
    "incoming_exchange",
    "exchange_failed",
    "exchange_deferred",
    "contact_missed",
    "incoming_contact_missed",
}


def _env_true(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class BundleStore:
    """In-memory DTN bundle store with optional cold-path metrics logs.

    Hot-path benchmark operation is in memory plus IPC sockets:
      - bundle injection enters the running daemon through a Unix control socket
      - delivered payment payloads are emitted through a Unix delivery socket

    No bundle JSON files are persisted and there is no inbox.jsonl polling in
    this version.  ``events.jsonl`` and ``delivered.log`` remain optional debug
    outputs only, controlled by environment variables.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        max_events: int = 0,
    ) -> None:
        self.path = Path(path) if path is not None else Path("")

        self._write_event_log = _env_true("MESHPAY_DTN_EVENT_LOG", False)
        self._write_delivered_log = _env_true("MESHPAY_DTN_DELIVERED_LOG", False)
        self._event_filter = os.environ.get("MESHPAY_DTN_EVENT_FILTER", "metrics").strip().lower()

        # The directory is still needed for Unix-domain socket paths and final
        # debug files, but we no longer persist one JSON file per bundle.
        if self.path:
            self.path.mkdir(parents=True, exist_ok=True)

        self.events_log = self.path / "events.jsonl"
        self.delivered_log = self.path / "delivered.log"

        self._lock = threading.RLock()
        self._bundles: dict[str, Bundle] = {}
        self._confirmed_order_ids: set[str] = set()
        self._delivered_ids: set[str] = set()
        self._bundle_order_ids: dict[str, str] = {}

        self.max_events = max(0, int(max_events))
        self.events: list[dict] = []

        self.new_bundle_event = threading.Event()
        self.diagnostics: dict[str, int] = {
            "bundles_saved": 0,
            "bundles_deleted": 0,
            "confirmation_prunes": 0,
            "events_recorded": 0,
            "events_written": 0,
            "delivered_written": 0,
        }

    @property
    def confirmed_order_ids(self) -> set[str]:
        with self._lock:
            return set(self._confirmed_order_ids)

    @confirmed_order_ids.setter
    def confirmed_order_ids(self, value: set[str]) -> None:
        with self._lock:
            self._confirmed_order_ids = set(value)

    def has(self, bundle_id: str) -> bool:
        with self._lock:
            return bundle_id in self._bundles

    def save(self, bundle: Bundle) -> None:
        """Store a bundle in memory and wake exchange loops."""
        with self._lock:
            self._bundles[bundle.bundle_id] = bundle
            self._index_bundle_unlocked(bundle)
            self.diagnostics["bundles_saved"] += 1

            if self._payload_type(bundle) == "confirmation_order":
                order_id = self._order_id_for_bundle(bundle)
                if order_id:
                    self._confirmed_order_ids.add(order_id)
                    self._prune_by_order_id_unlocked(order_id)

        self.new_bundle_event.set()

    def load(self, bundle_id: str) -> Optional[Bundle]:
        with self._lock:
            bundle = self._bundles.get(bundle_id)
            if bundle is None or bundle.expired():
                return None
            return bundle

    def all(self) -> List[Bundle]:
        with self._lock:
            self._prune_expired_unlocked()
            return list(self._bundles.values())

    def ids(self) -> set[str]:
        with self._lock:
            self._prune_expired_unlocked()
            return set(self._bundles)

    def snapshot(self) -> tuple[list[Bundle], set[str]]:
        with self._lock:
            self._prune_expired_unlocked()
            return list(self._bundles.values()), set(self._bundles)

    def unknown_to_peer(
        self,
        peer_ids: Iterable[str],
        peer_node: str | None = None,
        limit: Optional[int] = None,
    ) -> List[Bundle]:
        if limit is not None and limit <= 0:
            return []

        known = set(peer_ids)
        with self._lock:
            self._prune_expired_unlocked()
            bundles = [b for bid, b in self._bundles.items() if bid not in known]

        def priority(bundle: Bundle) -> tuple[int, int, float]:
            ptype = self._payload_type(bundle)
            type_rank = {
                "confirmation_order": 0,
                "signed_transfer_order": 1,
                "transfer_order": 2,
            }.get(ptype, 3)
            dst_rank = 0 if peer_node and bundle.dst == peer_node else 1
            return type_rank, dst_rank, bundle.created_at

        if limit is None or len(bundles) <= limit:
            return sorted(bundles, key=priority)
        return heapq.nsmallest(limit, bundles, key=priority)

    def prune_by_order_id(self, order_id: str) -> None:
        with self._lock:
            self._prune_by_order_id_unlocked(order_id)

    def mark_delivered(self, bundle: Bundle, node: str) -> bool:
        """Record delivery in memory. Returns False if already delivered."""
        with self._lock:
            if bundle.bundle_id in self._delivered_ids:
                return False
            self._delivered_ids.add(bundle.bundle_id)

        event = {
            "event": "delivered",
            "node": node,
            "bundle_id": bundle.bundle_id,
            "src": bundle.src,
            "dst": bundle.dst,
            "latency_ms": (time.time() - bundle.created_at) * 1000.0,
            "size_bytes": bundle.size_bytes,
            "hops": bundle.hops,
            "payload": bundle.payload,
        }

        if self._write_delivered_log:
            try:
                self.delivered_log.parent.mkdir(parents=True, exist_ok=True)
                with self.delivered_log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(event, sort_keys=True) + "\n")
                with self._lock:
                    self.diagnostics["delivered_written"] += 1
            except Exception:
                pass

        self.record_event(event)
        return True

    def record_event(self, event: dict) -> None:
        """Record an in-memory event and optionally append cold debug metrics."""
        event = dict(event)
        event.setdefault("time", time.time())

        with self._lock:
            self.diagnostics["events_recorded"] += 1
            if self.max_events > 0:
                self.events.append(event)
                if len(self.events) > self.max_events:
                    del self.events[: len(self.events) - self.max_events]

        if not self._write_event_log:
            return
        if self._event_filter == "metrics" and event.get("event") not in _METRIC_EVENTS:
            return
        try:
            self.events_log.parent.mkdir(parents=True, exist_ok=True)
            with self.events_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, sort_keys=True) + "\n")
            with self._lock:
                self.diagnostics["events_written"] += 1
        except Exception:
            pass

    @staticmethod
    def _payload_type(bundle: Bundle) -> str | None:
        if isinstance(bundle.payload, dict):
            return bundle.payload.get("type")
        return None

    @staticmethod
    def _order_id_for_bundle(bundle: Bundle) -> str | None:
        if not isinstance(bundle.payload, dict):
            return None
        data = bundle.payload.get("data", {})
        if not isinstance(data, dict):
            return None
        order_id = data.get("order_id") or data.get("i")
        return str(order_id) if order_id else None

    def _index_bundle_unlocked(self, bundle: Bundle) -> None:
        order_id = self._order_id_for_bundle(bundle)
        if order_id:
            self._bundle_order_ids[bundle.bundle_id] = order_id
            if self._payload_type(bundle) == "confirmation_order":
                self._confirmed_order_ids.add(order_id)
        else:
            self._bundle_order_ids.pop(bundle.bundle_id, None)

    def _prune_by_order_id_unlocked(self, order_id: str) -> None:
        to_delete = [
            bid
            for bid, bundle in self._bundles.items()
            if self._payload_type(bundle) in {"transfer_order", "signed_transfer_order"}
            and self._order_id_for_bundle(bundle) == str(order_id)
        ]
        for bid in to_delete:
            self._bundles.pop(bid, None)
            self._bundle_order_ids.pop(bid, None)
            self.diagnostics["bundles_deleted"] += 1
        if to_delete:
            self.diagnostics["confirmation_prunes"] += len(to_delete)

    def _prune_expired_unlocked(self) -> None:
        now = time.time()
        expired = [bid for bid, bundle in self._bundles.items() if bundle.expired(now)]
        for bid in expired:
            self._bundles.pop(bid, None)
            self._bundle_order_ids.pop(bid, None)
            self.diagnostics["bundles_deleted"] += 1