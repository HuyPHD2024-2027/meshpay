"""Shared mesh networking behavior for MeshPay nodes.

Provides the :class:`MeshMixin` that encapsulates neighbor management,
UDP broadcast discovery, ping-based reachability, and hop-limited mesh
relay logic.  Both :class:`Client` and :class:`WiFiAuthority` inherit
from this mixin so that the networking code is defined in one place.
"""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from queue import Queue
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from meshpay.types import Address, PeerInfo
from meshpay.messages import (
    Message,
    MessageType,
    PeerDiscoveryMessage,
)
from mn_wifi.services.core.config import (
    DEFAULT_RELAY_TTL,
    DISCOVERY_PORT,
    DISCOVERY_INTERVAL,
    NEIGHBOR_TIMEOUT,
)


class MeshMixin:
    """Shared mesh networking behavior for Client and Authority nodes."""

    # Subclasses override to advertise their capabilities.
    _service_capabilities: List[str] = ["relay"]

    # ------------------------------------------------------------------
    # Neighbor management
    # ------------------------------------------------------------------

    def _init_mesh(self) -> None:
        """Initialise mesh-specific data structures (call from __init__)."""
        from meshpay.routing.epidemic import EpidemicRouting
        
        self.p2p_connections: Dict[str, PeerInfo] = {}
        # message_queue is used by TCPTransport as its receive buffer.
        self.message_queue: Queue[Message] = Queue()
        
        # DTN Store-Carry-Forward buffer and Routing Protocol
        self.message_buffer = {}  # Dict[str, MessageBufferItem]
        self.routing_protocol = EpidemicRouting(getattr(self, 'name', 'unknown'))

    def add_neighbor(self, node_id: str, address: Address) -> None:
        """Register a neighbour node."""
        self.state.neighbors[node_id] = address
        self.p2p_connections[node_id] = PeerInfo(address=address, last_seen=time.time())

    def remove_neighbor(self, node_id: str) -> None:
        """Remove a neighbour node."""
        self.state.neighbors.pop(node_id, None)
        self.p2p_connections.pop(node_id, None)

    def get_neighbor_last_seen(self, node_id: str) -> Optional[float]:
        """Return the last seen timestamp for a specific neighbor."""
        peer = self.p2p_connections.get(node_id)
        return peer.last_seen if peer else None

    def get_neighbors(self) -> Dict[str, Address]:
        """Return a snapshot of the current neighbour table, filtering out stale ones."""
        now = time.time()
        stale_ids = [
            nid for nid, peer in self.p2p_connections.items()
            if now - peer.last_seen > NEIGHBOR_TIMEOUT
        ]
        for nid in stale_ids:
            self.remove_neighbor(nid)
        return dict(self.state.neighbors)

    def _is_reachable(self, ip: str) -> bool:
        """Check if an IP is reachable via ping (validates actual wireless link)."""
        try:
            proc = self.popen(
                ['ping', '-c', '1', '-W', '1', ip],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            proc.communicate(timeout=3.0)
            return proc.returncode == 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Discovery service (UDP broadcast)
    # ------------------------------------------------------------------

    def _start_discovery_service(self) -> None:
        """Start background threads for neighbour discovery."""
        threading.Thread(target=self._discovery_listen_loop, daemon=True).start()
        threading.Thread(target=self._discovery_broadcast_loop, daemon=True).start()

    def _discovery_broadcast_loop(self) -> None:
        """Periodically broadcast presence to neighbours."""
        while self._running:
            try:
                # Prune stale neighbors
                self.get_neighbors()

                pos = getattr(self, "position", (0.0, 0.0, 0.0))
                # Ensure it's a tuple of floats
                try:
                    pos_tuple = (float(pos[0]), float(pos[1]), float(pos[2]))
                except (IndexError, TypeError, ValueError):
                    pos_tuple = (0.0, 0.0, 0.0)

                discovery = PeerDiscoveryMessage(
                    node_info=self.address,
                    service_capabilities=self._service_capabilities,
                    network_metrics=None,
                    position=pos_tuple
                )

                msg = Message(
                    message_id=uuid4(),
                    message_type=MessageType.PEER_DISCOVERY,
                    sender=self.address,
                    recipient=None,
                    timestamp=time.time(),
                    payload=discovery.to_payload(),
                )

                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    data = msg.to_json().encode('utf-8')
                    # Use both broadcast and directed if we have interfaces
                    sock.sendto(data, ('10.255.255.255', DISCOVERY_PORT))

            except Exception as e:
                if hasattr(self, "logger"):
                    self.logger.debug(f"Discovery broadcast error: {e}")

            time.sleep(DISCOVERY_INTERVAL)

    def _discovery_listen_loop(self) -> None:
        """Listen for neighbour advertisements."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('0.0.0.0', DISCOVERY_PORT))
            except Exception as e:
                self.logger.error(f"Failed to bind discovery port: {e}")
                return

            while self._running:
                try:
                    data, addr = sock.recvfrom(4096)
                    msg = Message.from_json(data.decode('utf-8'))

                    if msg.message_type == MessageType.PEER_DISCOVERY:
                        peer_info = PeerDiscoveryMessage.from_payload(msg.payload)
                        peer_addr = peer_info.node_info

                        if peer_addr.node_id == self.name:
                            continue

                        if peer_addr.node_id not in self.state.neighbors:
                            if self._is_reachable(peer_addr.ip_address):
                                self.add_neighbor(peer_addr.node_id, peer_addr)
                                # Update position and calculate signal for PeerInfo
                                peer = self.p2p_connections.get(peer_addr.node_id)
                                if peer:
                                    peer.position = peer_info.position
                                # Trigger DTN discovery response
                                self.routing_protocol.on_neighbor_discovered(peer_addr.node_id, self.message_buffer)
                                self._flush_routing_outbox()
                        else:
                            if self._is_reachable(peer_addr.ip_address):
                                peer = self.p2p_connections.get(peer_addr.node_id)
                                if peer:
                                    peer.last_seen = time.time()
                                    peer.position = peer_info.position
                                # Trigger DTN discovery (Epidemic handles cooldowns)
                                self.routing_protocol.on_neighbor_discovered(peer_addr.node_id, self.message_buffer)
                                self._flush_routing_outbox()
                            else:
                                self.remove_neighbor(peer_addr.node_id)

                except Exception as e:
                    self.logger.error(f"Discovery receive error: {e}")
                    continue

    # ------------------------------------------------------------------
    # DTN Integration Bridge
    # ------------------------------------------------------------------

    def _flush_routing_outbox(self) -> None:
        """Drain the routing protocol's outbox and send over transport.

        Two kinds of outbox items:
          - ``type='routing'`` → control messages (summary vectors, requests)
            wrapped as ``ROUTING_MESSAGE`` with the protocol payload.
          - ``type='relay'`` → actual buffered data requested by a peer,
            wrapped as ``ROUTING_MESSAGE`` with ``protocol_type='dtn_bundle'``.
        """
        from meshpay.messages import RoutingMessage
        from meshpay.types.transaction import MessageBufferItem

        outbox = self.routing_protocol.get_messages_to_send()

        for instr in outbox:
            recipient_id = instr.get("recipient_id")
            recipient_addr = self.state.neighbors.get(recipient_id)
            if not recipient_addr:
                continue

            msg_type = instr.get("type")

            if msg_type == "routing":
                # Control message (e.g. epidemic_summary, epidemic_request)
                routing_msg = RoutingMessage(
                    protocol_type=instr["payload"]["protocol_type"],
                    data=instr["payload"]["data"],
                )
            elif msg_type == "relay":
                # Actual buffered data — wrap as dtn_bundle
                msg_id = instr.get("msg_id")
                if msg_id not in self.message_buffer:
                    continue
                item = self.message_buffer[msg_id]
                routing_msg = RoutingMessage(
                    protocol_type="dtn_bundle",
                    data={
                        "message_id": item.message_id,
                        "message_type": item.message_type,
                        "payload": item.payload,
                        "sender_id": item.sender_id,
                        "ttl": item.ttl,
                    },
                )
            else:
                continue

            msg = Message(
                message_id=uuid4(),
                message_type=MessageType.ROUTING_MESSAGE,
                sender=self.address,
                recipient=recipient_addr,
                timestamp=time.time(),
                payload=routing_msg.to_payload(),
            )
            self.transport.send_message(msg, recipient_addr)

    def _handle_routing_message(self, message: Message) -> None:
        """Dispatch an incoming ROUTING_MESSAGE.

        - Routing control (epidemic_summary, epidemic_request, …) →
          forwarded to ``self.routing_protocol``.
        - ``dtn_bundle`` → stored in ``message_buffer``, protocol notified,
          and application hook invoked.
        """
        from meshpay.messages import RoutingMessage
        from meshpay.types.transaction import MessageBufferItem

        routing_msg = RoutingMessage.from_payload(message.payload)
        sender_id = message.sender.node_id
        
        # Ensure we have a route back to the sender even if UDP discovery failed asymmetrically
        if sender_id not in self.state.neighbors:
            self.state.neighbors[sender_id] = message.sender

        if routing_msg.protocol_type == "dtn_bundle":
            # ── Incoming data bundle ──
            bundle = routing_msg.data
            msg_id = bundle.get("message_id")

            if msg_id in self.message_buffer:
                return  # already have it (dedup)

            new_ttl = bundle.get("ttl", 1) - 1
            if new_ttl <= 0:
                return  # expired

            item = MessageBufferItem(
                message_id=msg_id,
                message_type=bundle.get("message_type"),
                payload=bundle.get("payload"),
                sender_id=bundle.get("sender_id"),
                ttl=new_ttl,
            )
            self.message_buffer[msg_id] = item

            # Notify routing protocol (so it appears in future summaries)
            self.routing_protocol.on_message_added_to_buffer(msg_id, self.message_buffer)

            # Application-layer hook (overridden by Client / Authority)
            self.on_dtn_bundle_received(item)
        else:
            # ── Routing control message ──
            self.routing_protocol.on_routing_message_received(
                sender_id,
                routing_msg.to_payload(),
                self.message_buffer,
            )

        # Always flush — the protocol may have enqueued replies
        self._flush_routing_outbox()

    def on_dtn_bundle_received(self, item) -> None:
        """Application-layer hook called when a new DTN bundle arrives.

        Subclasses (Client, Authority) override this to process the
        inner payload (e.g. handle transfer requests, responses, etc.).
        The default implementation does nothing (pure relay node).
        """
        pass

    # ------------------------------------------------------------------
    # Telemetry and state exposure methods
    # ------------------------------------------------------------------

    def get_link_stats(self) -> Dict[str, Any]:
        """Expose wireless link metrics for the SDN controller layer.

        Dynamically discovers wireless interfaces and parses signal strength,
        tx/rx bytes, and SINR from the driver (iw) or simulation fallback.
        """
        import subprocess
        try:
            # 1. Discover the primary wireless interface
            # Stations usually have 'wlan0' or 'mp0' (mesh)
            intfs = []
            if hasattr(self, "params") and "wlan" in self.params:
                intfs = self.node.params["wlan"] if hasattr(self, "node") else self.params["wlan"]
            
            if not intfs:
                name = getattr(self, "name", "unknown")
                intfs = [f"{name}-wlan0", f"{name}-mp0"]

            # 2. Try to get metrics for each interface
            stats: Dict[str, Any] = {}
            for intf in intfs:
                # Try station dump (Standard/AP) and mpath dump (Mesh)
                cmd = ["iw", "dev", intf, "station", "dump"]
                proc = self.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                raw, _ = proc.communicate(timeout=1.0)
                
                if raw:
                    parsed = self._parse_iw_station_dump(raw)
                    if parsed:
                        stats.update(parsed)
                        break # Found active peer stats

            # 3. Fallback to Mininet-WiFi simulation metrics or geometry calculation
            if not stats.get("signal") or stats["signal"] == -100.0:
                best_rssi = -100.0
                
                # Check for simulation-layer RSSI first (Mininet-WiFi sometimes populates it)
                if hasattr(self, "wintfs") and len(self.wintfs) > 0:
                    w_intf = next(iter(self.wintfs.values())) if isinstance(self.wintfs, dict) else self.wintfs[0]
                    if hasattr(w_intf, "rssi") and w_intf.rssi != 0:
                        best_rssi = float(w_intf.rssi)

                # Geometry-based estimation (Cheating fallback for demo)
                if best_rssi == -100.0:
                    my_pos = getattr(self, "position", (0,0,0))
                    for node_id, peer in self.p2p_connections.items():
                        if peer.position:
                            # Calculate distance
                            dx = float(my_pos[0]) - float(peer.position[0])
                            dy = float(my_pos[1]) - float(peer.position[1])
                            dist = (dx**2 + dy**2)**0.5
                            # Simple Log-Distance path loss model: RSSI = Ptx - 10 * exp * log10(dist)
                            # Using exp=4.0 and Ptx=15 as a heuristic
                            import math
                            if dist < 1.0: dist = 1.0
                            rssi_est = 15 - 10 * 4.0 * math.log10(dist)
                            best_rssi = max(best_rssi, rssi_est)
                
                # Final emergency fallback if still N/A
                if best_rssi == -100.0 and hasattr(self, "params") and "rssi" in self.params:
                    best_rssi = float(self.params["rssi"])
                
                stats["signal"] = best_rssi if best_rssi != -100.0 else None

            # 4. Capture SINR/SNR
            if not stats.get("sinr"):
                if hasattr(self, "wintfs") and len(self.wintfs) > 0:
                    w_intf = next(iter(self.wintfs.values())) if isinstance(self.wintfs, dict) else self.wintfs[0]
                    stats["sinr"] = getattr(w_intf, "snr", None)
                    
            return stats
        except Exception as e:
            if hasattr(self, "logger"):
                import traceback
                self.logger.error(f"get_link_stats failed: {repr(e)}\n{traceback.format_exc()}")
            return {}

    @staticmethod
    def _parse_iw_station_dump(raw: str) -> Dict[str, Any]:
        """Parse output of ``iw dev ... station dump`` into aggregated metrics."""
        aggregated: Dict[str, Any] = {
            "neighbor_count": 0,
            "signal": -100.0, # Start with floor
            "tx_bytes": 0,
            "rx_bytes": 0,
            "tx_retries": 0,
            "tx_failed": 0
        }
        
        if not raw:
            return {}

        current_stations = 0
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("Station"):
                current_stations += 1
                continue
                
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip().lower().replace(" ", "_")
            val = val.strip()
            
            # Clean units
            for suffix in (" dBm", " MBit/s", " bytes", " packets", " ms"):
                if val.endswith(suffix):
                    val = val[: -len(suffix)]
                    break
            
            try:
                num_val = float(val) if "." in val else int(val)
                if key == "signal":
                    # Keep the strongest signal
                    aggregated["signal"] = max(aggregated["signal"], num_val)
                elif key in ["tx_bytes", "rx_bytes", "tx_retries", "tx_failed"]:
                    # Sum traffic across all neighbors
                    aggregated[key] += num_val
                else:
                    aggregated[key] = num_val
            except (ValueError, TypeError):
                aggregated[key] = val
        
        if current_stations == 0:
            return {}
            
        aggregated["neighbor_count"] = current_stations
        return aggregated

    def get_buffer_occupancy(self) -> int:
        """Return the current size of the DTN message buffer."""
        return len(getattr(self, "message_buffer", {}))

    def get_encounter_history(self) -> List[str]:
        """Return a list of currently active neighbors."""
        # get_neighbors updates p2p_connections and filters out stale ones
        return list(self.get_neighbors().keys())

