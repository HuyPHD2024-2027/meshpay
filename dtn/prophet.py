#!/usr/bin/env python3

from __future__ import annotations

import threading
import time
from typing import Any, List, Optional, Set

from dtn.bundle import Bundle
from dtn.router import DTNRouter, inject_bundle, parse_args


P_INIT = 0.75
BETA = 0.5
GAMMA = 0.995
EPSILON = 0.0
DEFAULT_REPLICATION_BUDGET = 6


class ProphetRouter(DTNRouter):
    """Pure, standard PRoPHET DTN router.

    Predictabilities are learned from direct/transitive contacts. Forwarding
    decisions are purely based on comparing delivery predictabilities to the
    destination (plus epsilon), with a simple replication budget cap.
    No MeshPay-specific role priors, payload type awareness, or authority prioritization.
    """

    def __init__(
        self,
        *args,
        p_init: float = P_INIT,
        beta: float = BETA,
        gamma: float = GAMMA,
        epsilon: float = EPSILON,
        replication_budget: int = DEFAULT_REPLICATION_BUDGET,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.p_init = float(p_init)
        self.beta = float(beta)
        self.gamma = float(gamma)
        self.epsilon = float(epsilon)
        self.replication_budget = max(1, int(replication_budget))

        self._state_lock = threading.RLock()
        self._predictabilities: dict[str, float] = {}
        self._peer_predictabilities: dict[str, dict[str, float]] = {}
        self._forwarded_to: dict[str, set[str]] = {}
        self._last_aged_at = time.time()

    # ------------------------------------------------------------------
    # PRoPHET math helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _age_predictabilities(self) -> None:
        now = time.time()
        elapsed = max(0.0, now - self._last_aged_at)
        if elapsed <= 0.0:
            return

        units = elapsed / max(self.discovery_interval, 1.0)
        factor = self.gamma ** units

        for node in list(self._predictabilities):
            aged = self._predictabilities[node] * factor
            if aged < 0.000001:
                self._predictabilities.pop(node, None)
            else:
                self._predictabilities[node] = aged

        self._last_aged_at = now

    def _update_direct(self, peer_node: str) -> None:
        previous = self._predictabilities.get(peer_node, 0.0)
        updated = previous + (1.0 - previous) * self.p_init
        self._predictabilities[peer_node] = self._clamp(updated)
        self.record_event({
            "event":    "prophet_predictability_updated",
            "peer":     peer_node,
            "target":   peer_node,
            "previous": previous,
            "updated":  self._predictabilities[peer_node],
            "reason":   "direct_contact",
        })

    def _update_transitive(self, peer_node: str, peer_preds: dict[str, float]) -> None:
        p_peer = self._predictabilities.get(peer_node, 0.0)
        if p_peer <= 0.0:
            return

        for target, peer_target_raw in peer_preds.items():
            if target in {self.node, peer_node}:
                continue
            try:
                peer_target = self._clamp(float(peer_target_raw))
            except Exception:
                continue
            previous  = self._predictabilities.get(target, 0.0)
            candidate = previous + (1.0 - previous) * p_peer * peer_target * self.beta
            candidate = self._clamp(candidate)
            if candidate <= previous:
                continue
            self._predictabilities[target] = candidate
            self.record_event({
                "event":    "prophet_predictability_updated",
                "peer":     peer_node,
                "target":   target,
                "previous": previous,
                "updated":  candidate,
                "reason":   "transitive",
            })

    # ------------------------------------------------------------------
    # Routing hooks
    # ------------------------------------------------------------------

    def summary_metadata(self) -> dict:
        with self._state_lock:
            self._age_predictabilities()
            return {
                "protocol":         "prophet",
                "predictabilities": dict(self._predictabilities),
            }

    def observe_peer_summary(self, peer_node: str, summary: dict) -> None:
        routing    = summary.get("routing", {})
        peer_preds = routing.get("predictabilities", {}) if isinstance(routing, dict) else {}
        if not isinstance(peer_preds, dict):
            peer_preds = {}

        clean_peer_preds: dict[str, float] = {}
        for target, value in peer_preds.items():
            try:
                clean_peer_preds[str(target)] = self._clamp(float(value))
            except Exception:
                continue

        with self._state_lock:
            self._age_predictabilities()
            self._peer_predictabilities[peer_node] = clean_peer_preds
            self._update_direct(peer_node)
            self._update_transitive(peer_node, clean_peer_preds)

    # ------------------------------------------------------------------
    # Scoring and replication control
    # ------------------------------------------------------------------

    def _can_replicate(self, bundle: Bundle, peer_node: str) -> bool:
        if bundle.dst == peer_node:
            return True
        peers = self._forwarded_to.setdefault(bundle.bundle_id, set())
        return peer_node in peers or len(peers) < self.replication_budget

    # ------------------------------------------------------------------
    # Bundle selection
    # ------------------------------------------------------------------

    def select_bundles_for_peer(
        self,
        peer_ids: Set[str],
        peer_node: str,
        local_snapshot: Optional[List[Bundle]] = None,
    ) -> List[Bundle]:
        known = set(peer_ids)
        selected: List[tuple[float, Bundle, float, float, bool]] = []

        with self._state_lock:
            self._age_predictabilities()
            peer_preds = self._peer_predictabilities.get(peer_node, {})

            if local_snapshot is not None:
                current_ids = {b.bundle_id for b in local_snapshot}
                candidates = [b for b in local_snapshot if b.bundle_id not in known]
            else:
                current_ids = self.store.ids()
                candidates = self.store.unknown_to_peer(known, peer_node=peer_node)

            # Drop forwarding history for bundles that are no longer in the
            # in-memory store.  This prevents stale per-bundle state growth.
            for bundle_id in list(self._forwarded_to):
                if bundle_id not in current_ids:
                    self._forwarded_to.pop(bundle_id, None)

            for bundle in candidates:
                local_score = self._predictabilities.get(bundle.dst, 0.0)
                peer_score  = peer_preds.get(bundle.dst, 0.0)
                direct      = bundle.dst == peer_node

                if not self._can_replicate(bundle, peer_node):
                    continue

                is_prophet_forward = peer_score > local_score + self.epsilon

                if direct or is_prophet_forward:
                    # Sort primarily by created_at (fifo or earliest first)
                    selected.append((
                        bundle.created_at,
                        bundle,
                        local_score,
                        peer_score,
                        direct,
                    ))

            selected = sorted(selected, key=lambda item: item[0])[: self.max_bundles_per_exchange]
            bundles: List[Bundle] = []

            for _created_at, bundle, local_score, peer_score, direct in selected:
                bundles.append(bundle)
                if not direct:
                    self._forwarded_to.setdefault(bundle.bundle_id, set()).add(peer_node)
                self.record_event({
                    "event":                "prophet_forwarded",
                    "peer":                 peer_node,
                    "bundle_id":            bundle.bundle_id,
                    "dst":                  bundle.dst,
                    "local_predictability": local_score,
                    "peer_predictability":  peer_score,
                    "direct_delivery":      direct,
                })

        return bundles


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    if args.inject:
        if not args.dst:
            raise SystemExit("--inject requires --dst")
        if args.payload is None and args.payload_json is None:
            raise SystemExit("--inject requires --payload or --payload-json")
        inject_bundle(args)
        return

    router = ProphetRouter(
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