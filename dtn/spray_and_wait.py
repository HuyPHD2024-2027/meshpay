#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path
from typing import List, Set, Optional

from dtn.bundle import Bundle
from dtn.epidemic import EpidemicRouter, inject_bundle, parse_args


DEFAULT_INITIAL_COPIES = 8


class SprayAndWaitRouter(EpidemicRouter):
    """Binary Spray-and-Wait DTN router."""

    def __init__(self, *args, initial_copies: int = DEFAULT_INITIAL_COPIES, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.initial_copies = max(1, int(initial_copies))
        self._state_path = Path(self.store.path) / ".spray_state"
        self._state_lock = threading.RLock()
        self._copies: dict[str, int] = {}
        self._pending_peer_copies: dict[tuple[str, str], int] = {}
        self._last_state_save = 0.0   # epoch; 0 forces a save on first call
        self._state_save_interval = 2.0  # throttle disk writes to every 2 s
        self._load_state()

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        copies = data.get("copies", {})
        if isinstance(copies, dict):
            self._copies = {str(k): max(1, int(v)) for k, v in copies.items()}

    def _save_state(self) -> None:
        """Persist copy counts to disk at most every `_state_save_interval` s."""
        import time as _time
        now = _time.time()
        if now - self._last_state_save < self._state_save_interval:
            return
        self._last_state_save = now
        tmp = self._state_path.with_suffix(".tmp")
        payload = {
            "protocol": "spray-and-wait",
            "initial_copies": self.initial_copies,
            "copies": self._copies,
        }
        tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self._state_path)

    def _ensure_copies(self, bundle: Bundle) -> int:
        copies = self._copies.get(bundle.bundle_id)
        if copies is None:
            if bundle.src == self.node:
                base = self.initial_copies
                # Confirmation orders are the most critical bundles —
                # give them extra copies to reach all authorities + recipient.
                if (
                    isinstance(bundle.payload, dict)
                    and bundle.payload.get("type") == "confirmation_order"
                ):
                    base = min(base * 2, 14)
                copies = base
            else:
                copies = 1
            self._copies[bundle.bundle_id] = copies
        return copies

    def select_bundles_for_peer(
        self, 
        peer_ids: Set[str], 
        peer_node: str,
        local_snapshot: Optional[List[Bundle]] = None,
    ) -> List[Bundle]:
        known = set(peer_ids)
        selected: List[Bundle] = []

        with self._state_lock:
            self._pending_peer_copies = {
                key: value
                for key, value in self._pending_peer_copies.items()
                if key[0] != peer_node
            }

            # Use the pre-fetched snapshot to save disk I/O if provided
            if local_snapshot is not None:
                candidates = [b for b in local_snapshot if b.bundle_id not in known]
            else:
                candidates = self.store.unknown_to_peer(known, peer_node=peer_node)

            for bundle in candidates:
                copies = self._ensure_copies(bundle)

                if bundle.dst == peer_node:
                    assigned = max(1, copies)
                    selected.append(bundle)
                    self._pending_peer_copies[(peer_node, bundle.bundle_id)] = assigned
                    self.record_event(
                        {
                            "event": "spray_forwarded_direct",
                            "peer": peer_node,
                            "bundle_id": bundle.bundle_id,
                            "copies": copies,
                        }
                    )
                    continue

                if copies <= 1:
                    continue

                assigned = copies // 2
                retained = copies - assigned
                self._copies[bundle.bundle_id] = retained
                self._pending_peer_copies[(peer_node, bundle.bundle_id)] = assigned
                selected.append(bundle)
                self.record_event(
                    {
                        "event": "spray_forwarded",
                        "peer": peer_node,
                        "bundle_id": bundle.bundle_id,
                        "assigned_copies": assigned,
                        "retained_copies": retained,
                    }
                )

                if len(selected) >= self.max_bundles_per_exchange:
                    break

            self._save_state()

        return selected

    def bundle_to_wire(self, bundle: Bundle, peer_node: str) -> dict:
        data = bundle.to_dict()
        copies = self._pending_peer_copies.get((peer_node, bundle.bundle_id), 1)
        data["_routing"] = {
            "protocol": "spray-and-wait",
            "copies": max(1, int(copies)),
        }
        return data

    def on_bundle_received(
        self,
        bundle: Bundle,
        peer_node: str,
        metadata: dict,
        stored: bool,
    ) -> None:
        if not stored:
            return
        copies = int(metadata.get("copies", 1)) if metadata else 1
        with self._state_lock:
            self._copies[bundle.bundle_id] = max(1, copies)
            self._save_state()
        self.record_event(
            {
                "event": "spray_copies_received",
                "peer": peer_node,
                "bundle_id": bundle.bundle_id,
                "copies": max(1, copies),
            }
        )


def main() -> None:
    args = parse_args()

    if args.inject:
        if not args.dst:
            raise SystemExit("--inject requires --dst")
        if args.payload is None and args.payload_json is None:
            raise SystemExit("--inject requires --payload or --payload-json")
        inject_bundle(args)
        return

    router = SprayAndWaitRouter(
        node=args.node,
        store_path=args.store,
        discovery_port=args.discovery_port,
        exchange_port=args.exchange_port,
        discovery_interval=args.discovery_interval,
        connect_timeout=args.connect_timeout,
        socket_timeout=args.socket_timeout,
        max_backoff=args.max_backoff,
        max_parallel_exchanges=args.max_parallel_exchanges,
        contact_miss_log_interval=args.contact_miss_log_interval,
        success_cooldown=args.success_cooldown,
        discovery_mode=args.discovery_mode,
        wireless_iface=args.wireless_iface,
        static_peers=args.peer,
    )
    router.run()


if __name__ == "__main__":
    main()