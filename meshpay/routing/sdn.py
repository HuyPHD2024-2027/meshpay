"""Policy-gated SDN-DTN routing protocol for MeshPay.

SDN forwarding rules are used only after a signed authority policy reaches
quorum. Until then, the protocol falls back to epidemic anti-entropy so payment
bundles can still move opportunistically.
"""

from __future__ import annotations

import copy
import logging
import time
from typing import Any, Dict, List, Optional

from meshpay.policy import (
    PolicyStore,
    build_default_policy,
    canonical_policy_hash,
    default_authority_key,
    default_authority_keys,
    get_forwarding_rule,
    get_interface_preference,
    get_message_priority,
    get_replication_limit,
    load_policy_file,
)
from meshpay.policy.model import committee_authorities, policy_signatures
from meshpay.routing.dtn import DTNRoutingProtocol
from meshpay.routing.epidemic import EpidemicRouting
from meshpay.types.transaction import MessageBufferItem

logger = logging.getLogger(__name__)


class SDNDTNRouting(DTNRoutingProtocol):
    """SDN-DTN routing gated by signed quorum-approved policies."""

    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.node: Any = None
        self.policy_store = PolicyStore()
        self._fallback = EpidemicRouting(node_id)
        self._cached_policy: Optional[Dict[str, Any]] = None
        self._policy_template: Optional[Dict[str, Any]] = None
        self._replication_counts: Dict[str, int] = {}
        self._last_summary_sent: Dict[str, float] = {}
        self._last_policy_marker_sent: Dict[str, str] = {}
        self.summary_cooldown = 3.0
        self.default_limits = {
            "transfer_request": 5,
            "transfer_response": 5,
            "confirmation_request": 5,
        }

    def set_node(self, node: Any) -> None:
        """Set the host node and load optional policy configuration."""

        self.node = node
        params = getattr(node, "params", {}) or {}
        authority_keys = params.get("policy_authority_keys") or {}
        if not authority_keys:
            authority_keys = default_authority_keys(self._known_authorities())
        self.policy_store = PolicyStore(authority_keys=authority_keys)

        policy = params.get("policy") or params.get("sdn_policy")
        policy_file = params.get("policy_file") or params.get("sdn_policy_file")
        if policy:
            self._policy_template = copy.deepcopy(policy)
        elif policy_file:
            self._policy_template = load_policy_file(str(policy_file))

    def on_neighbor_discovered(self, neighbor_id: str, current_buffer: Dict[str, MessageBufferItem]) -> None:
        """Send policy fragments and route according to active policy or fallback."""

        if not self.node:
            return

        self._send_policy_fragment(neighbor_id)

        active_policy = self._active_policy()
        if not active_policy:
            self._fallback.on_neighbor_discovered(neighbor_id, current_buffer)
            self._drain_fallback_outbox()
            return

        # Proactively push any matching "push" messages in the buffer to the neighbor if eligible
        for msg_id, item in list(current_buffer.items()):
            if item.is_expired:
                continue
            msg_type = item.message_type
            rule = get_forwarding_rule(active_policy, msg_type)
            if rule.get("action") == "push":
                candidates = self._policy_targets(msg_type, item, active_policy)
                if neighbor_id in candidates:
                    limit = get_replication_limit(active_policy, msg_type, self.default_limits.get(msg_type, len(candidates)))
                    rep_count = self._replication_counts.get(msg_id, 0)
                    count_key = f"{msg_id}->{neighbor_id}"
                    if rep_count < limit and self._replication_counts.get(count_key, 0) == 0:
                        self._queue_relay_transmission(
                            recipient_id=neighbor_id,
                            msg_id=msg_id,
                            interface_preference=get_interface_preference(active_policy, msg_type),
                        )
                        self._replication_counts[count_key] = 1
                        self._replication_counts[msg_id] = rep_count + 1
                        logger.debug("[%s] SDN proactive discovery-push %s to neighbor %s", self.node_id, msg_id, neighbor_id)

        now = time.time()
        last_sent = self._last_summary_sent.get(neighbor_id, 0.0)
        if now - last_sent <= self.summary_cooldown:
            return

        keys = [msg_id for msg_id, item in current_buffer.items() if not item.is_expired]
        logger.debug("[%s] SDN-DTN: sending summary with %s items to %s", self.node_id, len(keys), neighbor_id)
        self._queue_routing_message(
            recipient_id=neighbor_id,
            protocol_type="sdn_summary",
            data={"keys": keys, "policy_hash": canonical_policy_hash(active_policy)},
        )
        self._last_summary_sent[neighbor_id] = now

    def on_routing_message_received(
        self,
        sender_id: str,
        payload: Dict[str, Any],
        current_buffer: Dict[str, MessageBufferItem],
    ) -> None:
        """Handle SDN policy/control messages or delegate fallback control."""

        p_type = payload.get("protocol_type")
        data = payload.get("data", {})

        if p_type == "sdn_policy":
            self._handle_policy(sender_id, data, current_buffer)
        elif p_type == "sdn_summary":
            if self._active_policy():
                self._handle_summary(sender_id, data.get("keys", []), current_buffer)
            else:
                logger.debug("[%s] Ignoring sdn_summary from %s: active policy not yet reached quorum", self.node_id, sender_id)
        elif p_type == "sdn_request":
            if self._active_policy():
                self._handle_request(sender_id, data.get("requested_keys", []), current_buffer)
            else:
                logger.debug("[%s] Ignoring sdn_request from %s: active policy not yet reached quorum", self.node_id, sender_id)
        elif str(p_type or "").startswith("epidemic_"):
            self._fallback.on_routing_message_received(sender_id, payload, current_buffer)
            self._drain_fallback_outbox()
        else:
            logger.warning("[%s] Unknown SDN-DTN protocol_type: %s", self.node_id, p_type)

    def on_message_added_to_buffer(self, msg_id: str, current_buffer: Dict[str, MessageBufferItem]) -> None:
        """Route newly buffered messages according to active policy or fallback."""

        if not self.node:
            return

        active_policy = self._active_policy()
        if not active_policy:
            self._fallback.on_message_added_to_buffer(msg_id, current_buffer)
            self._drain_fallback_outbox()
            return

        item = current_buffer.get(msg_id)
        if not item or item.is_expired:
            return

        if self._queue_priority_pushes(msg_id, item, active_policy):
            return

        limit = get_replication_limit(active_policy, item.message_type, self.default_limits.get(item.message_type, 99))
        if self._replication_counts.get(msg_id, 0) >= limit:
            return

        now = time.time()
        neighbors = getattr(getattr(self.node, "state", None), "neighbors", {})
        for neighbor_id in neighbors:
            self._queue_routing_message(
                recipient_id=neighbor_id,
                protocol_type="sdn_summary",
                data={"keys": [msg_id], "policy_hash": canonical_policy_hash(active_policy)},
            )
            self._last_summary_sent[neighbor_id] = now

    def get_messages_to_send(self) -> List[Dict[str, Any]]:
        """Retrieve and sort messages using active policy traffic classes."""

        policy = self._active_policy()

        def get_priority(instr: Dict[str, Any]) -> int:
            if instr.get("type") == "relay":
                msg_id = instr.get("msg_id")
                if self.node and msg_id in getattr(self.node, "message_buffer", {}):
                    item = self.node.message_buffer[msg_id]
                    return get_message_priority(policy, item.message_type, default=3)
            if instr.get("type") == "routing":
                protocol_type = instr.get("payload", {}).get("protocol_type", "")
                return get_message_priority(policy, protocol_type, default=3)
            return 3

        self._outbox.sort(key=get_priority)
        messages = self._outbox.copy()
        self._outbox.clear()
        return messages

    def _active_policy(self) -> Optional[Dict[str, Any]]:
        return self.policy_store.active_policy or self._cached_policy

    def _drain_fallback_outbox(self) -> None:
        self._outbox.extend(self._fallback.get_messages_to_send())

    def _is_authority(self) -> bool:
        address = getattr(self.node, "address", None)
        node_type = getattr(address, "node_type", getattr(self.node, "node_type", ""))
        value = getattr(node_type, "value", node_type)
        return str(value).lower() == "authority" or "authority" in str(type(self.node)).lower()

    def _known_authorities(self) -> List[str]:
        if self._policy_template:
            authorities = committee_authorities(self._policy_template)
            if authorities:
                return authorities

        authorities = set()
        state = getattr(self.node, "state", None)
        if state is not None:
            for authority in getattr(state, "committee_members", set()) or set():
                authorities.add(str(authority))
            for authority in getattr(state, "committee", []) or []:
                authorities.add(str(getattr(authority, "name", authority)))
        if self._is_authority():
            authorities.add(self.node_id)
        return sorted(authorities)

    def _best_policy(self) -> Optional[Dict[str, Any]]:
        if self.policy_store.active_policy:
            return copy.deepcopy(self.policy_store.active_policy)
        if self.policy_store.pending:
            from meshpay.policy.model import policy_signatures
            best_hash = max(
                self.policy_store.pending.keys(),
                key=lambda h: len(policy_signatures(self.policy_store.pending[h])),
            )
            return copy.deepcopy(self.policy_store.pending[best_hash])
        return None

    def _base_policy(self) -> Dict[str, Any]:
        if self._policy_template:
            policy = copy.deepcopy(self._policy_template)
        else:
            now = time.time()
            epoch_duration = 3600
            epoch = int(now / epoch_duration)
            epoch_start = epoch * epoch_duration
            policy = build_default_policy(
                self._known_authorities(),
                epoch=epoch,
                valid_from=epoch_start,
                valid_until=epoch_start + 7200,
            )

        policy.setdefault("signatures", {})
        if not committee_authorities(policy):
            policy.setdefault("committee", {})["authorities"] = self._known_authorities()
        return policy

    def _send_policy_fragment(self, neighbor_id: str) -> None:
        policy = self._best_policy()
        if not policy:
            if self._is_authority():
                policy = self._base_policy()
            else:
                return

        if self._is_authority():
            authorities = committee_authorities(policy)
            if self.node_id not in authorities:
                authorities.append(self.node_id)
                policy.setdefault("committee", {})["authorities"] = sorted(authorities)

            from meshpay.policy.model import policy_signatures
            signatures = policy_signatures(policy)
            if self.node_id not in signatures:
                key = self.policy_store.authority_keys.get(self.node_id, default_authority_key(self.node_id))
                from meshpay.policy import sign_policy
                policy = sign_policy(policy, self.node_id, key)
                self.policy_store.add_policy_fragment(policy)
                # Re-read best policy to ensure we have the updated/merged signatures
                policy = self._best_policy() or policy

        self._queue_policy_for_neighbor(neighbor_id, policy)

    def _queue_policy_for_neighbor(self, neighbor_id: str, policy: Dict[str, Any]) -> None:
        marker = f"{canonical_policy_hash(policy)}:{len(policy_signatures(policy))}"
        if self._last_policy_marker_sent.get(neighbor_id) == marker:
            return
        self._queue_routing_message(
            recipient_id=neighbor_id,
            protocol_type="sdn_policy",
            data=copy.deepcopy(policy),
        )
        self._last_policy_marker_sent[neighbor_id] = marker

    def _gossip_policy(self, policy: Dict[str, Any], exclude: Optional[str] = None) -> None:
        neighbors = getattr(getattr(self.node, "state", None), "neighbors", {}) if self.node else {}
        for neighbor_id in sorted(neighbors):
            if neighbor_id == exclude:
                continue
            self._queue_policy_for_neighbor(neighbor_id, policy)

    def _neighbor_kind(self, neighbor_id: str) -> str:
        neighbors = getattr(getattr(self.node, "state", None), "neighbors", {}) if self.node else {}
        address = neighbors.get(neighbor_id)
        node_type = getattr(address, "node_type", "")
        value = getattr(node_type, "value", node_type)
        return str(value).lower()

    def _neighbors_by_kind(self, kind: str) -> List[str]:
        neighbors = getattr(getattr(self.node, "state", None), "neighbors", {}) if self.node else {}
        wanted = kind.lower()
        return sorted(neighbor_id for neighbor_id in neighbors if self._neighbor_kind(neighbor_id) == wanted)

    def _payload_transfer(self, item: MessageBufferItem) -> Dict[str, Any]:
        payload = item.payload or {}
        if isinstance(payload.get("transfer_order"), dict):
            return payload["transfer_order"]
        if isinstance(payload.get("confirmation_order"), dict):
            transfer = payload["confirmation_order"].get("transfer_order", {})
            return transfer if isinstance(transfer, dict) else {}
        return {}

    def _policy_targets(self, msg_type: str, item: MessageBufferItem, policy: Dict[str, Any]) -> List[str]:
        rule = get_forwarding_rule(policy, msg_type)
        target = str(rule.get("target", "")).lower()
        transfer = self._payload_transfer(item)
        neighbors = getattr(getattr(self.node, "state", None), "neighbors", {}) if self.node else {}

        if target == "authority_neighbors":
            return self._neighbors_by_kind("authority")
        if target == "sender_client":
            sender = str(transfer.get("sender", ""))
            if sender and sender in neighbors:
                return [sender]
            return self._neighbors_by_kind("client")
        if target == "authorities_and_recipient":
            recipient = str(transfer.get("recipient", ""))
            candidates = self._neighbors_by_kind("authority")
            if recipient and recipient in neighbors:
                candidates.insert(0, recipient)
            return candidates
        if target == "all_neighbors":
            return sorted(neighbors.keys())
        return []

    def _queue_priority_pushes(self, msg_id: str, item: MessageBufferItem, policy: Dict[str, Any]) -> bool:
        msg_type = item.message_type
        rule = get_forwarding_rule(policy, msg_type)
        if rule.get("action") != "push":
            return False

        candidates = self._policy_targets(msg_type, item, policy)
        if not candidates:
            return False

        limit = get_replication_limit(policy, msg_type, self.default_limits.get(msg_type, len(candidates)))
        interface_preference = get_interface_preference(policy, msg_type)
        pushed = 0
        for neighbor_id in candidates:
            if neighbor_id == self.node_id:
                continue
            count_key = f"{msg_id}->{neighbor_id}"
            if self._replication_counts.get(count_key, 0) > 0:
                continue
            self._queue_relay_transmission(
                recipient_id=neighbor_id,
                msg_id=msg_id,
                interface_preference=interface_preference,
            )
            self._replication_counts[count_key] = 1
            pushed += 1
            if pushed >= limit:
                break

        if not pushed:
            return False
        self._replication_counts[msg_id] = self._replication_counts.get(msg_id, 0) + pushed
        logger.debug("[%s] SDN policy-pushed %s to %s neighbors", self.node_id, msg_id, pushed)
        return True

    def _handle_policy(self, sender_id: str, policy: Dict[str, Any], current_buffer: Dict[str, MessageBufferItem]) -> None:
        result = self.policy_store.add_policy_fragment(policy)
        if not result.accepted:
            logger.debug("[%s] Rejected SDN policy from %s: %s", self.node_id, sender_id, result.reason)
            return

        if self._is_authority() and result.policy:
            from meshpay.policy.model import policy_signatures
            signatures = policy_signatures(result.policy)
            if self.node_id not in signatures:
                key = self.policy_store.authority_keys.get(self.node_id, default_authority_key(self.node_id))
                from meshpay.policy import sign_policy
                signed_frag = sign_policy(result.policy, self.node_id, key)
                result = self.policy_store.add_policy_fragment(signed_frag)

        if result.policy:
            best_p = self.policy_store.get_pending_policy(result.policy_hash) or self.policy_store.active_policy
            if best_p:
                self._gossip_policy(best_p, exclude=sender_id)

        if result.active and result.policy:
            self._cached_policy = result.policy
            logger.info(
                "[%s] Activated SDN policy %s with %s/%s signatures",
                self.node_id,
                result.policy_hash,
                result.signature_count,
                result.required_signatures,
            )
            self._prune_buffer(current_buffer, result.policy)
        else:
            logger.debug(
                "[%s] Stored SDN policy fragment %s (%s/%s signatures)",
                self.node_id,
                result.policy_hash,
                result.signature_count,
                result.required_signatures,
            )

    def _prune_buffer(self, current_buffer: Dict[str, MessageBufferItem], policy: Dict[str, Any]) -> None:
        buffer_rules = policy.get("buffer_rules") or {}
        if buffer_rules.get("prune_finalized_transactions", True) is False:
            return

        finalized_txs = set(str(tx_id) for tx_id in policy.get("finalized_transactions", []))
        if finalized_txs:
            for msg_id, item in list(current_buffer.items()):
                order_id = self._extract_order_id(msg_id, item)
                if order_id and str(order_id) in finalized_txs:
                    logger.info("[%s] SDN pruning finalized transaction bundle %s", self.node_id, msg_id)
                    current_buffer.pop(msg_id, None)

        max_items = int(buffer_rules.get("max_buffer_items", 0) or 0)
        if max_items and len(current_buffer) > max_items:
            drop_policy = str(buffer_rules.get("drop_policy", "low_priority_first"))
            if drop_policy == "low_priority_first":
                ordered = sorted(
                    current_buffer.items(),
                    key=lambda entry: (get_message_priority(policy, entry[1].message_type, default=3), entry[1].created_at),
                    reverse=True,
                )
                drop_count = len(current_buffer) - max_items
                for msg_id, _item in ordered[:drop_count]:
                    current_buffer.pop(msg_id, None)

    def _key_order_id(self, msg_id: str) -> str:
        return str(msg_id).split(":", 1)[0]

    def _extract_order_id(self, msg_id: str, item: MessageBufferItem) -> Optional[str]:
        payload = item.payload or {}
        if "transfer_order" in payload and isinstance(payload["transfer_order"], dict):
            return str(payload["transfer_order"].get("order_id"))
        if "confirmation_order" in payload and isinstance(payload["confirmation_order"], dict):
            conf = payload["confirmation_order"]
            transfer = conf.get("transfer_order", {})
            if isinstance(transfer, dict) and transfer.get("order_id"):
                return str(transfer.get("order_id"))
            if conf.get("order_id"):
                return str(conf.get("order_id"))
        if payload.get("order_id"):
            return str(payload.get("order_id"))
        return self._key_order_id(msg_id)

    def _handle_summary(self, sender_id: str, neighbor_keys: List[str], current_buffer: Dict[str, MessageBufferItem]) -> None:
        policy = self._active_policy()
        local_keys = {msg_id for msg_id, item in current_buffer.items() if not item.is_expired}
        finalized_txs = set(str(tx_id) for tx_id in (policy or {}).get("finalized_transactions", []))

        missing_keys = []
        for key in neighbor_keys:
            if key in local_keys:
                continue
            if self._key_order_id(key) in finalized_txs:
                continue
            missing_keys.append(key)

        if missing_keys:
            self._queue_routing_message(
                recipient_id=sender_id,
                protocol_type="sdn_request",
                data={"requested_keys": missing_keys},
            )

    def _handle_request(self, sender_id: str, requested_keys: List[str], current_buffer: Dict[str, MessageBufferItem]) -> None:
        policy = self._active_policy()

        def item_priority(msg_id: str) -> int:
            item = current_buffer.get(msg_id)
            return get_message_priority(policy, item.message_type, default=3) if item else 3

        for msg_id in sorted(requested_keys, key=item_priority):
            item = current_buffer.get(msg_id)
            if not item or item.is_expired:
                continue

            limit = get_replication_limit(policy, item.message_type, self.default_limits.get(item.message_type, 99))
            rep_count = self._replication_counts.get(msg_id, 0)
            if rep_count >= limit:
                logger.debug("[%s] SDN relay blocked for %s: limit %s/%s", self.node_id, msg_id, rep_count, limit)
                continue

            self._queue_relay_transmission(
                recipient_id=sender_id,
                msg_id=msg_id,
                interface_preference=get_interface_preference(policy, item.message_type),
            )
            self._replication_counts[msg_id] = rep_count + 1
