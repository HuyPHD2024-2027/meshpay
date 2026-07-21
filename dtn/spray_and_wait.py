#!/usr/bin/env python3

from __future__ import annotations

import threading
from typing import List, Optional, Set

from dtn.bundle import Bundle
from dtn.router import DTNRouter, inject_bundle, parse_args


DEFAULT_INITIAL_COPIES = 32


class SprayAndWaitRouter(DTNRouter):
    """Binary Spray-and-Wait DTN router.

    This lightweight version keeps copy-count state in memory only.  It does
    not read or write .spray_state files.  The forwarding rule is unchanged:
    direct-deliver to the destination, otherwise split copies in half while
    the local node still has more than one copy.
    """

    def __init__(self, *args, initial_copies: int = DEFAULT_INITIAL_COPIES, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.initial_copies = max(1, int(initial_copies))
        self._state_lock = threading.RLock()
        self._copies: dict[str, int] = {}
        self._pending_peer_copies: dict[tuple[str, str], int] = {}

    def _ensure_copies(self, bundle: Bundle) -> int:
        copies = self._copies.get(bundle.bundle_id)
        if copies is None:
            if bundle.src == self.node:
                copies = self.initial_copies
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
            # Remove stale pending metadata for this peer. New metadata will be
            # created for the bundles selected during this exchange.
            self._pending_peer_copies = {
                key: value
                for key, value in self._pending_peer_copies.items()
                if key[0] != peer_node
            }

            if local_snapshot is not None:
                current_ids = {b.bundle_id for b in local_snapshot}
                candidates = [b for b in local_snapshot if b.bundle_id not in known]
            else:
                current_ids = self.store.ids()
                candidates = self.store.unknown_to_peer(known, peer_node=peer_node)

            # Drop copy-count state for expired/pruned bundles.
            for bundle_id in list(self._copies):
                if bundle_id not in current_ids:
                    self._copies.pop(bundle_id, None)

            for bundle in candidates:
                copies = self._ensure_copies(bundle)

                if bundle.dst == peer_node:
                    assigned = max(1, copies)
                    selected.append(bundle)
                    self._pending_peer_copies[(peer_node, bundle.bundle_id)] = assigned
                    self.record_event({
                        "event": "spray_forwarded_direct",
                        "peer": peer_node,
                        "bundle_id": bundle.bundle_id,
                        "copies": copies,
                    })
                elif copies > 1:
                    assigned = copies // 2
                    retained = copies - assigned
                    self._copies[bundle.bundle_id] = retained
                    self._pending_peer_copies[(peer_node, bundle.bundle_id)] = assigned
                    selected.append(bundle)
                    self.record_event({
                        "event": "spray_forwarded",
                        "peer": peer_node,
                        "bundle_id": bundle.bundle_id,
                        "assigned_copies": assigned,
                        "retained_copies": retained,
                    })

                if len(selected) >= self.max_bundles_per_exchange:
                    break

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
        copies = max(1, copies)
        with self._state_lock:
            self._copies[bundle.bundle_id] = copies
        self.record_event({
            "event": "spray_copies_received",
            "peer": peer_node,
            "bundle_id": bundle.bundle_id,
            "copies": copies,
        })


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
        control_socket=args.control_socket,
        delivery_socket=args.delivery_socket,
    )
    router.run()


if __name__ == "__main__":
    main()