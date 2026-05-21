"""SDN-DTN Application-Layer Routing Protocol for MeshPay.

Enforces traffic classification, epoch-based forwarding policies,
dynamic replication control, and active buffer pruning.
"""

from __future__ import annotations
import logging
import time
from typing import Any, Dict, List, Optional
from uuid import UUID

from meshpay.routing.dtn import DTNRoutingProtocol
from meshpay.types.transaction import MessageBufferItem

logger = logging.getLogger(__name__)


class SDNDTNRouting(DTNRoutingProtocol):
    """SDN-DTN Routing Protocol.
    
    Implements application-layer traffic classification, replication controls,
    and active buffer pruning guided by signed authority policies.
    """
    
    def __init__(self, node_id: str):
        super().__init__(node_id)
        self.node: Any = None
        self._cached_policy: Optional[Dict[str, Any]] = None
        self._replication_counts: Dict[str, int] = {}
        self._last_summary_sent: Dict[str, float] = {}
        self._last_policy_epoch_sent: Dict[str, int] = {}
        self.summary_cooldown = 2.0  # seconds
        self.priority_push_limits = {
            "transfer_request": 5,
            "transfer_response": 3,
            "confirmation_request": 5,
        }

        # Default replication limits for pull-based fallback. Critical-path
        # messages use priority push first, so these limits mainly constrain
        # later anti-entropy repair traffic.
        self.default_limits = {
            "transfer_request": 5,
            "transfer_response": 5,
            "confirmation_request": 5
        }

    def set_node(self, node: Any) -> None:
        """Set a reference to the active host node for buffer access."""
        self.node = node

    def on_neighbor_discovered(self, neighbor_id: str, current_buffer: Dict[str, MessageBufferItem]) -> None:
        """Handle neighbor discovery. 
        
        Authorities generate and push policies; clients exchange priority summaries.
        """
        if not self.node:
            return

        now = time.time()
        is_authority = getattr(self.node, "node_type", None) == "authority" or "authority" in str(type(self.node)).lower()

        if is_authority:
            # Embedded Controller Mode: send at most one policy per epoch to
            # each neighbor. Re-sending full policy on every discovery tick was
            # dominating SDN control overhead.
            policy = self._generate_active_policy()
            policy_epoch = policy["epoch"]
            if self._last_policy_epoch_sent.get(neighbor_id) != policy_epoch:
                logger.info(
                    f"[{self.node_id}] Controller: Dispatched SDN Forwarding Policy "
                    f"to {neighbor_id} (Epoch {policy_epoch})"
                )
                self._queue_routing_message(
                    recipient_id=neighbor_id,
                    protocol_type="sdn_policy",
                    data=policy
                )
                self._last_policy_epoch_sent[neighbor_id] = policy_epoch

            # Also advertise authority-created votes/confirmations, but respect
            # the same summary cooldown as mobile agents.
            last_sent = self._last_summary_sent.get(neighbor_id, 0.0)
            if now - last_sent > self.summary_cooldown:
                keys = [msg_id for msg_id, item in current_buffer.items()
                        if not item.is_expired]
                if keys:
                    logger.debug(f"[{self.node_id}] Controller: Sending buffer summary with {len(keys)} items to {neighbor_id}")
                    self._queue_routing_message(
                        recipient_id=neighbor_id,
                        protocol_type="sdn_summary",
                        data={"keys": keys}
                    )
                    self._last_summary_sent[neighbor_id] = now
        else:
            # Mobile Agent Mode: send priority summary if cooldown passed
            last_sent = self._last_summary_sent.get(neighbor_id, 0.0)
            if now - last_sent > self.summary_cooldown:
                # Advertise ALL non-expired messages in the summary.
                # Replication limits are enforced only during actual relay
                # (in _handle_request), NOT during advertisement. This ensures
                # neighbors can always discover what data we carry, even if
                # we have already relayed our copies.  The SDN advantage
                # comes from *controlled relay* and *priority queuing*, not
                # from hiding data from neighbors.
                keys = [msg_id for msg_id, item in current_buffer.items()
                        if not item.is_expired]

                logger.debug(f"[{self.node_id}] SDN Agent: Sending priority summary with {len(keys)} items to {neighbor_id}")
                self._queue_routing_message(
                    recipient_id=neighbor_id,
                    protocol_type="sdn_summary",
                    data={"keys": keys}
                )
                self._last_summary_sent[neighbor_id] = now

    def on_routing_message_received(
        self, sender_id: str, payload: Dict[str, Any], current_buffer: Dict[str, MessageBufferItem]
    ) -> None:
        """Handle received SDN-DTN routing messages."""
        p_type = payload.get("protocol_type")
        data = payload.get("data", {})

        if p_type == "sdn_policy":
            self._handle_policy(sender_id, data, current_buffer)
        elif p_type == "sdn_summary":
            self._handle_summary(sender_id, data.get("keys", []), current_buffer)
        elif p_type == "sdn_request":
            self._handle_request(sender_id, data.get("requested_keys", []), current_buffer)
        else:
            logger.warning(f"[{self.node_id}] Unknown SDN-DTN protocol_type: {p_type}")

    def on_message_added_to_buffer(self, msg_id: str, current_buffer: Dict[str, MessageBufferItem]) -> None:
        """Proactively notify neighbors of new local message additions."""
        if not self.node:
            return

        item = current_buffer.get(msg_id)
        if not item or item.is_expired:
            return

        # SDN-DTN fast path: push finality-critical bundles immediately.
        # Epidemic needs summary -> request -> relay; this skips that control
        # round trip for payment requests, votes, and confirmations.
        if self._queue_priority_pushes(msg_id, item):
            return

        now = time.time()
        keys = [msg_id]

        limit = self.default_limits.get(item.message_type, 99)
        if self._cached_policy and "replication_limits" in self._cached_policy:
            limit = self._cached_policy["replication_limits"].get(item.message_type, limit)

        rep_count = self._replication_counts.get(msg_id, 0)
        if rep_count >= limit:
            return

        # Collect all known neighbor IDs from both tracked exchanges and node state
        neighbor_ids = set(self._last_summary_sent.keys())
        if self.node and hasattr(self.node, 'state') and hasattr(self.node.state, 'neighbors'):
            neighbor_ids.update(self.node.state.neighbors.keys())

        for neighbor_id in neighbor_ids:
            logger.debug(f"[{self.node_id}] Proactively pushing summary for {msg_id} to {neighbor_id}")
            self._queue_routing_message(
                recipient_id=neighbor_id,
                protocol_type="sdn_summary",
                data={"keys": keys}
            )
            self._last_summary_sent[neighbor_id] = now

    def _neighbor_kind(self, neighbor_id: str) -> str:
        neighbors = getattr(getattr(self.node, "state", None), "neighbors", {}) if self.node else {}
        address = neighbors.get(neighbor_id)
        node_type = getattr(address, "node_type", "")
        value = getattr(node_type, "value", node_type)
        return str(value).lower()

    def _neighbors_by_kind(self, kind: str) -> List[str]:
        neighbors = getattr(getattr(self.node, "state", None), "neighbors", {}) if self.node else {}
        wanted = kind.lower()
        return sorted(
            neighbor_id for neighbor_id in neighbors
            if self._neighbor_kind(neighbor_id) == wanted
        )

    def _payload_transfer(self, item: MessageBufferItem) -> Dict[str, Any]:
        payload = item.payload or {}
        if isinstance(payload.get("transfer_order"), dict):
            return payload["transfer_order"]
        if isinstance(payload.get("confirmation_order"), dict):
            transfer = payload["confirmation_order"].get("transfer_order", {})
            return transfer if isinstance(transfer, dict) else {}
        return {}

    def _queue_priority_pushes(self, msg_id: str, item: MessageBufferItem) -> bool:
        candidates: List[str] = []
        msg_type = item.message_type
        transfer = self._payload_transfer(item)

        if msg_type == "transfer_request":
            candidates = self._neighbors_by_kind("authority")
        elif msg_type == "transfer_response":
            sender = str(transfer.get("sender", ""))
            if sender and sender in getattr(getattr(self.node, "state", None), "neighbors", {}):
                candidates = [sender]
            else:
                candidates = self._neighbors_by_kind("client")
        elif msg_type == "confirmation_request":
            recipient = str(transfer.get("recipient", ""))
            candidates = self._neighbors_by_kind("authority")
            if recipient and recipient in getattr(getattr(self.node, "state", None), "neighbors", {}):
                candidates.insert(0, recipient)

        if not candidates:
            return False

        limit = self.priority_push_limits.get(msg_type, len(candidates))
        pushed = 0
        for neighbor_id in candidates:
            if neighbor_id == self.node_id:
                continue
            count_key = f"{msg_id}->{neighbor_id}"
            if self._replication_counts.get(count_key, 0) > 0:
                continue
            self._queue_relay_transmission(recipient_id=neighbor_id, msg_id=msg_id)
            self._replication_counts[count_key] = 1
            pushed += 1
            if pushed >= limit:
                break

        if pushed:
            self._replication_counts[msg_id] = self._replication_counts.get(msg_id, 0) + pushed
            logger.debug(f"[{self.node_id}] SDN priority-pushed {msg_id} to {pushed} neighbors")
            if msg_type == "transfer_request":
                return pushed >= 4
            if msg_type == "transfer_response":
                sender = str(transfer.get("sender", ""))
                return bool(sender and sender in candidates[:pushed])
            return True
        return False

    def get_messages_to_send(self) -> List[Dict[str, Any]]:
        """Retrieve and sort messages using application-layer traffic classification.
        
        Prioritizes:
        - Class 1 (Highest): BCB votes and certificates (transfer_response, confirmation_request)
        - Class 2 (Medium): Settlement bundles (transfer_request)
        - Class 3 (Best Effort): SDN policies, summaries, requests, syncs, heartbeats
        """
        def get_priority(instr: Dict[str, Any]) -> int:
            if instr.get("type") == "relay":
                msg_id = instr.get("msg_id")
                if self.node and msg_id in self.node.message_buffer:
                    item = self.node.message_buffer[msg_id]
                    m_type = item.message_type
                    if m_type in ("transfer_response", "confirmation_request", "confirmation_response"):
                        return 1
                    if m_type == "transfer_request":
                        return 2
            # Control messages or gossip
            return 3

        # Sort outbox: lower score = higher priority (comes first)
        self._outbox.sort(key=get_priority)
        messages = self._outbox.copy()
        self._outbox.clear()
        return messages

    def _generate_active_policy(self) -> Dict[str, Any]:
        """Controller helper to generate signed epoch policy."""
        finalized_txs = []
        if self.node and hasattr(self.node, "get_finalized_transaction_ids"):
            finalized_txs = self.node.get_finalized_transaction_ids()

        return {
            "epoch": int(time.time() / 15),
            "controller_id": self.node_id,
            "replication_limits": {
                "transfer_request": 5,          # Push to all authorities for fast quorum
                "transfer_response": 5,         # Allow repair if direct sender push misses
                "confirmation_request": 5       # Push settlement certificates broadly
            },
            "finalized_transactions": finalized_txs,
            "signature": f"mock_sig_{self.node_id}_{int(time.time())}"
        }

    def _handle_policy(self, sender_id: str, policy: Dict[str, Any], current_buffer: Dict[str, MessageBufferItem]) -> None:
        """Process incoming SDN Forwarding Policy and prune buffer."""
        # 1. Update/Cache policy
        epoch = policy.get("epoch", 0)
        curr_epoch = self._cached_policy.get("epoch", -1) if self._cached_policy else -1

        if epoch >= curr_epoch:
            self._cached_policy = policy
            logger.debug(f"[{self.node_id}] Cached fresh SDN Policy (Epoch {epoch}) from Controller {sender_id}")

            # 2. Enforce active buffer pruning
            finalized_txs = policy.get("finalized_transactions", [])
            if finalized_txs:
                prune_list = []
                for msg_id, item in list(current_buffer.items()):
                    order_id = self._extract_order_id(msg_id, item)
                    if order_id and str(order_id) in finalized_txs:
                        prune_list.append(msg_id)

                for p_id in prune_list:
                    logger.info(f"[{self.node_id}] SDN Buffer Pruning: Purging finalized transaction {p_id} from buffer")
                    if p_id in current_buffer:
                        del current_buffer[p_id]


    def _key_order_id(self, msg_id: str) -> str:
        """Return the transfer order id component from a DTN message id."""
        return str(msg_id).split(":", 1)[0]

    def _extract_order_id(self, msg_id: str, item: MessageBufferItem) -> Optional[str]:
        """Best-effort extraction of the transfer order id carried by a bundle."""
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
        """Request missing keys that aren't pruned and satisfy policy limits."""
        local_keys = set(msg_id for msg_id, item in current_buffer.items() if not item.is_expired)
        
        # Don't request keys we already have or that are already finalized in cached policy
        finalized_txs = set()
        if self._cached_policy:
            finalized_txs = set(self._cached_policy.get("finalized_transactions", []))

        missing_keys = []
        for k in neighbor_keys:
            if k in local_keys:
                continue
            if self._key_order_id(k) in finalized_txs:
                continue
            missing_keys.append(k)

        if missing_keys:
            logger.debug(f"[{self.node_id}] Requesting {len(missing_keys)} missing messages from {sender_id}")
            self._queue_routing_message(
                recipient_id=sender_id,
                protocol_type="sdn_request",
                data={"requested_keys": missing_keys}
            )

    def _handle_request(self, sender_id: str, requested_keys: List[str], current_buffer: Dict[str, MessageBufferItem]) -> None:
        """Satisfy requested keys in priority order (Classification) and increment replication."""
        # 1. Classification & Sorting
        def get_item_priority(msg_id: str) -> int:
            item = current_buffer.get(msg_id)
            if item:
                if item.message_type in ("transfer_response", "confirmation_request", "confirmation_response"):
                    return 1
                if item.message_type == "transfer_request":
                    return 2
            return 3

        sorted_keys = sorted(requested_keys, key=get_item_priority)

        # 2. Replication-limited relay queuing
        for msg_id in sorted_keys:
            item = current_buffer.get(msg_id)
            if not item or item.is_expired:
                continue

            # Check replication count limit
            limit = self.default_limits.get(item.message_type, 99)
            if self._cached_policy and "replication_limits" in self._cached_policy:
                limit = self._cached_policy["replication_limits"].get(item.message_type, limit)

            rep_count = self._replication_counts.get(msg_id, 0)
            if rep_count >= limit:
                logger.debug(f"[{self.node_id}] SDN Relay Blocked: {msg_id} replication limit reached ({rep_count}/{limit})")
                continue

            # Relay and increment local replication count
            self._queue_relay_transmission(recipient_id=sender_id, msg_id=msg_id)
            self._replication_counts[msg_id] = rep_count + 1
            logger.debug(f"[{self.node_id}] Relaying {msg_id} to {sender_id} (Replication {rep_count + 1}/{limit})")
