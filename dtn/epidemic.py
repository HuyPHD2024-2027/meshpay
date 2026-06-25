#!/usr/bin/env python3

from __future__ import annotations

from typing import List, Optional, Set

from dtn.bundle import Bundle
from dtn.router import DTNRouter, inject_bundle, parse_args


class EpidemicRouter(DTNRouter):
    """Epidemic DTN routing policy.

    The base DTNRouter handles neighbour discovery, TCP exchange, backoff,
    and bundle reception. Epidemic routing only decides which bundles should
    be sent to a peer: every bundle the peer does not already know, up to the
    exchange limit.
    """

    def select_bundles_for_peer(
        self,
        peer_ids: Set[str],
        peer_node: str,
        local_snapshot: Optional[List[Bundle]] = None,
    ) -> List[Bundle]:
        known = set(peer_ids)
        bundles = local_snapshot if local_snapshot is not None else self.store.all()
        candidates = [b for b in bundles if b.bundle_id not in known]
        return candidates[: self.max_bundles_per_exchange]


def main() -> None:
    args = parse_args()

    if args.inject:
        if not args.dst:
            raise SystemExit("--inject requires --dst")
        if args.payload is None and args.payload_json is None:
            raise SystemExit("--inject requires --payload or --payload-json")
        inject_bundle(args)
        return

    router = EpidemicRouter(
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