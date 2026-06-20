#!/usr/bin/env python3

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, List, Set

from dtn.bundle import Bundle
from dtn.epidemic import EpidemicRouter, inject_bundle, parse_args


P_INIT = 0.75
BETA = 0.5
GAMMA = 0.995
EPSILON = 0.15
DEFAULT_REPLICATION_BUDGET = 8
DEFAULT_TRANSFER_REPLICATION_BUDGET = 14

PAYLOAD_PRIORITIES = {
    "confirmation_order": 0,
    "signed_transfer_order": 1,
    "transfer_order": 2,
}


def account_host(account: str | None) -> str | None:
    if not account:
        return None
    return str(account).split("/", 1)[0]


def payload_type(bundle: Bundle) -> str:
    if isinstance(bundle.payload, dict):
        return str(bundle.payload.get("type", ""))
    return ""


def payload_data(bundle: Bundle) -> dict[str, Any]:
    if not isinstance(bundle.payload, dict):
        return {}
    data = bundle.payload.get("data", {})
    return data if isinstance(data, dict) else {}


class ProphetRouter(EpidemicRouter):
    """MeshPay-aware PRoPHET DTN router.

    The base PRoPHET score is still learned from direct and transitive
    contacts, but MeshPay payment objects also get role priors so quorum
    messages are routed toward authorities, senders, and recipients even
    before the contact graph has fully converged.
    """

    def __init__(
        self,
        *args,
        p_init: float = P_INIT,
        beta: float = BETA,
        gamma: float = GAMMA,
        epsilon: float = EPSILON,
        replication_budget: int = DEFAULT_REPLICATION_BUDGET,
        transfer_replication_budget: int = DEFAULT_TRANSFER_REPLICATION_BUDGET,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.p_init = float(p_init)
        self.beta = float(beta)
        self.gamma = float(gamma)
        self.epsilon = float(epsilon)
        self.replication_budget = max(1, int(replication_budget))
        self.transfer_replication_budget = max(1, int(transfer_replication_budget))
        self._state_path = Path(self.store.path) / ".prophet_state"
        self._state_lock = threading.RLock()
        self._predictabilities: dict[str, float] = {}
        self._peer_predictabilities: dict[str, dict[str, float]] = {}
        self._forwarded_to: dict[str, set[str]] = {}
        self._last_aged_at = time.time()
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

        preds = data.get("predictabilities", {})
        if isinstance(preds, dict):
            self._predictabilities = {
                str(node): self._clamp(float(value))
                for node, value in preds.items()
            }

        forwarded = data.get("forwarded_to", {})
        if isinstance(forwarded, dict):
            self._forwarded_to = {
                str(bundle_id): {str(peer) for peer in peers if peer}
                for bundle_id, peers in forwarded.items()
                if isinstance(peers, list)
            }

        self._last_aged_at = float(data.get("last_aged_at", time.time()))

    def _save_state(self) -> None:
        """Persist PRoPHET state to disk at most every `_state_save_interval` s."""
        now = time.time()
        if now - self._last_state_save < self._state_save_interval:
            return
        self._last_state_save = now
        tmp = self._state_path.with_suffix(".tmp")
        payload = {
            "protocol": "prophet",
            "p_init": self.p_init,
            "beta": self.beta,
            "gamma": self.gamma,
            "epsilon": self.epsilon,
            "replication_budget": self.replication_budget,
            "transfer_replication_budget": self.transfer_replication_budget,
            "last_aged_at": self._last_aged_at,
            "predictabilities": self._predictabilities,
            "forwarded_to": {
                bundle_id: sorted(peers)
                for bundle_id, peers in self._forwarded_to.items()
            },
        }
        tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self._state_path)

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, value))

    @staticmethod
    def _is_authority(node: str | None) -> bool:
        return bool(node and str(node).startswith("auth"))

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
        self.record_event(
            {
                "event": "prophet_predictability_updated",
                "peer": peer_node,
                "target": peer_node,
                "previous": previous,
                "updated": self._predictabilities[peer_node],
                "reason": "direct_contact",
            }
        )

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
            previous = self._predictabilities.get(target, 0.0)
            candidate = previous + (1.0 - previous) * p_peer * peer_target * self.beta
            candidate = self._clamp(candidate)
            if candidate <= previous:
                continue
            self._predictabilities[target] = candidate
            self.record_event(
                {
                    "event": "prophet_predictability_updated",
                    "peer": peer_node,
                    "target": target,
                    "previous": previous,
                    "updated": candidate,
                    "reason": "transitive",
                }
            )

    def summary_metadata(self) -> dict:
        with self._state_lock:
            self._age_predictabilities()
            self._save_state()
            return {
                "protocol": "prophet",
                "predictabilities": dict(self._predictabilities),
                "role": "authority" if self._is_authority(self.node) else "client",
            }

    def observe_peer_summary(self, peer_node: str, summary: dict) -> None:
        routing = summary.get("routing", {})
        peer_preds = routing.get("predictabilities", {}) if isinstance(routing, dict) else {}
        if not isinstance(peer_preds, dict):
            peer_preds = {}

        clean_peer_preds = {}
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
            self._save_state()

    def _route_hints(self, bundle: Bundle) -> dict[str, Any]:
        if not isinstance(bundle.payload, dict):
            return {}
        hints = bundle.payload.get("_meshpay_route", {})
        return hints if isinstance(hints, dict) else {}

    def _role_prior(self, bundle: Bundle, node: str) -> float:
        ptype = payload_type(bundle)
        data = payload_data(bundle)
        hints = self._route_hints(bundle)
        authority_targets = set(hints.get("authority_targets", []))

        if bundle.dst == node:
            return 1.0

        if ptype == "transfer_order":
            if node in authority_targets or self._is_authority(node):
                return 0.95

        if ptype == "signed_transfer_order":
            sender_host = hints.get("sender_host") or account_host(data.get("sender") or data.get("s"))
            if sender_host == node:
                return 0.95

        if ptype == "confirmation_order":
            recipient_host = hints.get("recipient_host") or account_host(data.get("recipient") or data.get("r"))
            if recipient_host == node:
                return 0.90
            if node in authority_targets or self._is_authority(node):
                return 0.85

        return 0.0

    def _score_for_node(
        self,
        bundle: Bundle,
        node: str,
        predictabilities: dict[str, float],
    ) -> float:
        return max(
            predictabilities.get(bundle.dst, 0.0),
            self._role_prior(bundle, node),
        )

    def _can_replicate(self, bundle: Bundle, peer_node: str) -> bool:
        if bundle.dst == peer_node:
            return True
        if self._role_prior(bundle, peer_node) > 0.0:
            return True

        ptype = payload_type(bundle)
        budget = self.replication_budget
        if ptype == "transfer_order":
            # Transfer orders need extra copies at authorities to form quorum,
            # but globally raising relay copies crowds out signatures and confirmations.
            if bundle.dst == peer_node or self._is_authority(peer_node):
                budget = self.transfer_replication_budget
        elif ptype == "confirmation_order":
            budget *= 4
        elif ptype == "signed_transfer_order":
            budget *= 2

        peers = self._forwarded_to.setdefault(bundle.bundle_id, set())
        return peer_node in peers or len(peers) < budget

    def _priority_tuple(self, bundle: Bundle, peer_score: float) -> tuple[int, float, float]:
        ptype = payload_type(bundle)
        return (
            PAYLOAD_PRIORITIES.get(ptype, 3),
            -peer_score,
            bundle.created_at,
        )

    def select_bundles_for_peer(self, peer_ids: Set[str], peer_node: str) -> List[Bundle]:
        known = set(peer_ids)
        selected: List[tuple[tuple[int, float, float], Bundle, float, float, bool]] = []

        with self._state_lock:
            self._age_predictabilities()
            peer_preds = self._peer_predictabilities.get(peer_node, {})

            for bundle in self.store.unknown_to_peer(known, peer_node=peer_node):
                local_score = self._score_for_node(bundle, self.node, self._predictabilities)
                peer_score = self._score_for_node(bundle, peer_node, peer_preds)
                direct = bundle.dst == peer_node
                role_boost = self._role_prior(bundle, peer_node) > 0.0

                if not self._can_replicate(bundle, peer_node):
                    self.record_event(
                        {
                            "event": "prophet_skipped_replication_budget",
                            "peer": peer_node,
                            "bundle_id": bundle.bundle_id,
                            "dst": bundle.dst,
                            "payload_type": payload_type(bundle),
                        }
                    )
                    continue

                should_forward = direct or role_boost or peer_score + self.epsilon >= local_score
                if should_forward:
                    selected.append((self._priority_tuple(bundle, peer_score), bundle, local_score, peer_score, direct))
                else:
                    self.record_event(
                        {
                            "event": "prophet_skipped_lower_predictability",
                            "peer": peer_node,
                            "bundle_id": bundle.bundle_id,
                            "dst": bundle.dst,
                            "payload_type": payload_type(bundle),
                            "local_predictability": local_score,
                            "peer_predictability": peer_score,
                            "epsilon": self.epsilon,
                        }
                    )

            selected = sorted(selected, key=lambda item: item[0])[: self.max_bundles_per_exchange]
            bundles: List[Bundle] = []

            for _priority, bundle, local_score, peer_score, direct in selected:
                bundles.append(bundle)
                if not direct:
                    self._forwarded_to.setdefault(bundle.bundle_id, set()).add(peer_node)
                self.record_event(
                    {
                        "event": "prophet_forwarded",
                        "peer": peer_node,
                        "bundle_id": bundle.bundle_id,
                        "dst": bundle.dst,
                        "payload_type": payload_type(bundle),
                        "local_predictability": local_score,
                        "peer_predictability": peer_score,
                        "direct_delivery": direct,
                        "role_prior": self._role_prior(bundle, peer_node),
                    }
                )

            self._save_state()

        return bundles


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
    )
    router.run()


if __name__ == "__main__":
    main()
