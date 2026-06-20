#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import random
import socket
import subprocess
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from dtn import config
from dtn.bundle import Bundle
from dtn.store import BundleStore


DEFAULT_DISCOVERY_PORT = config.DEFAULT_DISCOVERY_PORT
DEFAULT_EXCHANGE_PORT = config.DEFAULT_EXCHANGE_PORT


class EpidemicRouter:
    """Epidemic DTN router with medium-specific neighbour discovery.

    Peer selection policy:
        - adhoc: UDP broadcast "discover" and unicast "peer" replies.
        - mesh: 802.11s neighbour table from `iw station dump`.
        - TCP bundle exchange is attempted only with discovered neighbours.

    Important:
        Receiving a broadcast discover_request is not enough to mark a peer
        as exchangeable. Only a unicast peer_reply is accepted as a usable
        neighbour. This avoids the false-peer storm seen in sparse Wi-Fi tests.
    """

    def __init__(
        self,
        node: str,
        store_path: str | Path,
        discovery_port: int = DEFAULT_DISCOVERY_PORT,
        exchange_port: int = DEFAULT_EXCHANGE_PORT,
        discovery_interval: float = config.DEFAULT_DISCOVERY_INTERVAL,
        connect_timeout: float = config.DEFAULT_CONNECT_TIMEOUT,
        socket_timeout: float = config.DEFAULT_SOCKET_TIMEOUT,
        max_backoff: float = config.DEFAULT_MAX_BACKOFF,
        max_parallel_exchanges: int = config.DEFAULT_MAX_PARALLEL_EXCHANGES,
        contact_miss_log_interval: float = config.DEFAULT_CONTACT_MISS_LOG_INTERVAL,
        success_cooldown: float = config.DEFAULT_SUCCESS_COOLDOWN,
        discovery_mode: str = "adhoc",
        wireless_iface: str | None = None,
        static_peers: List[Tuple[str, str, str | None]] | None = None,
    ) -> None:
        self.node = node
        self.store = BundleStore(store_path)

        if discovery_mode not in {"adhoc", "mesh"}:
            raise ValueError("discovery_mode must be one of: adhoc, mesh")

        self.discovery_port = int(discovery_port)
        self.exchange_port = int(exchange_port)
        self.discovery_interval = float(discovery_interval)
        self.discovery_mode = discovery_mode

        if self.discovery_mode == "mesh":
            self.connect_timeout = min(float(connect_timeout), 2.0)
            self.socket_timeout = max(float(socket_timeout), 30.0)
        else:
            self.connect_timeout = float(connect_timeout)
            self.socket_timeout = float(socket_timeout)

        self.max_bundles_per_exchange = config.DEFAULT_MAX_BUNDLES_PER_EXCHANGE

        if self.discovery_mode == "mesh":
            self.max_backoff = min(float(max_backoff), 2.0)
        else:
            self.max_backoff = float(max_backoff)
        self.max_parallel_exchanges = int(max_parallel_exchanges)
        self.contact_miss_log_interval = float(contact_miss_log_interval)
        if self.discovery_mode == "mesh":
            self.success_cooldown = min(float(success_cooldown), 0.25)
        else:
            self.success_cooldown = float(success_cooldown)
        self.wireless_iface = wireless_iface or f"{self.node}-wlan0"

        self.running = True

        # Exchange deduplication and backoff.
        self._exchange_mu = threading.Lock()
        self._active_exchanges: Set[Tuple[str, str, int]] = set()
        self._last_attempt: Dict[Tuple[str, str, int], float] = {}
        self._backoff: Dict[Tuple[str, str, int], float] = {}

        # Global exchange concurrency limiter.
        self._exchange_slots = threading.BoundedSemaphore(
            max(1, self.max_parallel_exchanges)
        )

        # Throttle noisy contact-miss logs.
        self._last_contact_miss_log: Dict[Tuple[str, str, int], float] = {}

        # Avoid replying repeatedly to the same discovery nonce.
        self._seen_discovery_nonces: Dict[str, float] = {}

        # Remember discovered peers.
        self.peers: Dict[str, str] = {}

        # Known nodes used by mesh mode to map `iw station dump` MACs to IPs.
        self.static_peers: Dict[str, Tuple[str, str | None]] = {}
        self.peers_by_mac: Dict[str, Tuple[str, str]] = {}

        for peer_node, peer_ip, peer_mac in static_peers or []:
            if peer_node == self.node:
                continue

            clean_mac = self.normalize_mac(peer_mac)
            self.static_peers[peer_node] = (peer_ip, clean_mac)

            if clean_mac:
                self.peers_by_mac[clean_mac] = (peer_node, peer_ip)

        self._last_mesh_neighbor_log: Dict[str, float] = {}
        self._last_mesh_unknown_log: Dict[str, float] = {}

        self.mesh_probe_peers_per_round = max(6, self.max_parallel_exchanges * 2)
        self.mesh_exchange_peers_per_tick = max(1, self.max_parallel_exchanges)
        self.mesh_peer_ttl = max(self.discovery_interval * 6.0, 6.0)
        self.mesh_reachable_peers: Dict[str, Tuple[str, int, float]] = {}
        self._last_mesh_reachable_log: Dict[str, float] = {}
        self._mesh_probe_cursor = 0
        self._mesh_exchange_cursor = 0
        self._mesh_empty_sync: Dict[Tuple[str, str, int], Tuple[float, int]] = {}

    @staticmethod
    def normalize_mac(mac: str | None) -> str | None:
        if not mac:
            return None

        mac = mac.strip().lower()
        if not mac:
            return None

        return mac

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, message: str) -> None:
        line = {
            "time": time.time(),
            "node": self.node,
            "event": "router_log",
            "message": message,
        }

        print(f"[{self.node}] {message}", flush=True)
        self.store.record_event(line)

    def record_event(self, event: dict) -> None:
        event = dict(event)
        event.setdefault("time", time.time())
        event.setdefault("node", self.node)
        self.store.record_event(event)

    def send_message(self, writer, msg: dict, compress: bool = True) -> int:
        json_bytes = json.dumps(msg).encode("utf-8")
        if compress:
            compressed = zlib.compress(json_bytes)
            encoded = base64.b64encode(compressed).decode("ascii")
            line = encoded + "\n"
        else:
            line = json.dumps(msg) + "\n"

        writer.write(line)
        return len(line.encode("utf-8"))

    def recv_message(self, reader) -> dict:
        message, _size = self.recv_message_with_size(reader)
        return message

    def recv_message_with_size(self, reader) -> tuple[dict, int]:
        line = reader.readline()
        if not line:
            return {}, 0

        size_bytes = len(line.encode("utf-8"))
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line), size_bytes
            except Exception:
                return {}, size_bytes
        try:
            compressed = base64.b64decode(line.encode("ascii"))
            json_bytes = zlib.decompress(compressed)
            return json.loads(json_bytes.decode("utf-8")), size_bytes
        except Exception as exc:
            self.log(f"error decompressing message: {exc!r}")
            try:
                return json.loads(line), size_bytes
            except Exception:
                return {}, size_bytes

    # ------------------------------------------------------------------
    # Bundle handling
    # ------------------------------------------------------------------

    def remember_bundle(self, bundle: Bundle) -> bool:
        """Store a received bundle if it is new and still valid."""

        if bundle.expired():
            return False

        # Vaccine-style pruning:
        # after a confirmation exists, ignore old transfer/signed bundles
        # for the same order_id.
        if isinstance(bundle.payload, dict):
            payload_type = bundle.payload.get("type")

            if payload_type in {"transfer_order", "signed_transfer_order"}:
                order_id = (bundle.payload.get("data", {}).get("order_id") or bundle.payload.get("data", {}).get("i"))

                if order_id and order_id in self.store.confirmed_order_ids:
                    return False

        if self.store.has(bundle.bundle_id):
            return False

        bundle.add_hop(self.node)
        self.store.save(bundle)

        self.record_event(
            {
                "event": "received",
                "bundle_id": bundle.bundle_id,
                "src": bundle.src,
                "dst": bundle.dst,
                "hops": bundle.hops,
                "size_bytes": bundle.size_bytes,
            }
        )

        self.log(
            f"received bundle={bundle.bundle_id} "
            f"src={bundle.src} dst={bundle.dst} hops={bundle.hops}"
        )

        if bundle.is_delivered_to(self.node):
            if self.store.mark_delivered(bundle, self.node):
                self.log(
                    f"DELIVERED bundle={bundle.bundle_id} "
                    f"payload={bundle.payload}"
                )

        return True

    def summary_metadata(self) -> dict:
        """Protocol-specific metadata sent with summary vectors."""

        return {}

    def observe_peer_summary(self, peer_node: str, summary: dict) -> None:
        """Let routing policies learn from a peer summary message."""

    def select_bundles_for_peer(
        self,
        peer_ids: Set[str],
        peer_node: str,
    ) -> List[Bundle]:
        """Return bundles this router should send to a peer."""

        return self.store.unknown_to_peer(
            peer_ids,
            peer_node=peer_node,
            limit=self.max_bundles_per_exchange,
        )

    def bundle_to_wire(self, bundle: Bundle, peer_node: str) -> dict:
        """Serialize a bundle for a specific peer."""

        return bundle.to_dict()

    def received_bundle_metadata(self, bundle_data: dict) -> dict:
        metadata = bundle_data.get("_routing")
        return metadata if isinstance(metadata, dict) else {}

    def on_bundle_received(
        self,
        bundle: Bundle,
        peer_node: str,
        metadata: dict,
        stored: bool,
    ) -> None:
        """Protocol-specific hook after receiving one bundle."""

    def exchange_completed(
        self,
        peer_node: str,
        sent_bundles: List[Bundle],
        received_count: int,
    ) -> None:
        """Protocol-specific hook after one TCP exchange completes."""

    # ------------------------------------------------------------------
    # TCP exchange server
    # ------------------------------------------------------------------

    def tcp_server(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            server.bind(("0.0.0.0", self.exchange_port))
            server.listen(32)
            server.settimeout(1.0)
        except Exception as exc:
            self.log(f"could not start exchange server: {exc!r}")
            return

        self.log(f"exchange server listening tcp/{self.exchange_port}")

        while self.running:
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as exc:
                self.log(f"exchange server accept error: {exc!r}")
                continue

            thread = threading.Thread(
                target=self.handle_incoming_exchange,
                args=(conn, addr),
                daemon=True,
            )
            thread.start()

        try:
            server.close()
        except Exception:
            pass

    def handle_incoming_exchange(self, conn: socket.socket, addr) -> None:
        try:
            conn.settimeout(self.socket_timeout)

            reader = conn.makefile("r")
            writer = conn.makefile("w")
            sent_bytes = 0
            received_bytes = 0

            request, message_bytes = self.recv_message_with_size(reader)
            received_bytes += message_bytes

            if not request or request.get("type") != "summary":
                return

            peer_node = request["node"]
            peer_ids: Set[str] = set(request["ids"])
            self.observe_peer_summary(peer_node, request)

            self.log(f"contact from peer={peer_node} ip={addr[0]}")

            bundles_for_peer = self.select_bundles_for_peer(peer_ids, peer_node)

            # Use snapshot() to get local IDs in the same lock+refresh
            # cycle as select_bundles_for_peer already did internally.
            _, local_ids = self.store.snapshot()
            response = {
                "type": "summary",
                "node": self.node,
                "ids": list(local_ids),
                "bundle_count": len(bundles_for_peer),
                "routing": self.summary_metadata(),
            }

            sent_bytes += self.send_message(writer, response)

            for bundle in bundles_for_peer:
                sent_bytes += self.send_message(
                    writer,
                    self.bundle_to_wire(bundle, peer_node),
                )

            writer.flush()

            final, message_bytes = self.recv_message_with_size(reader)
            received_bytes += message_bytes

            if not final:
                return

            peer_bundle_count = int(final.get("bundle_count", 0))

            received_count = 0

            for _ in range(peer_bundle_count):
                bundle_data, message_bytes = self.recv_message_with_size(reader)
                received_bytes += message_bytes

                if not bundle_data:
                    break

                try:
                    route_metadata = self.received_bundle_metadata(bundle_data)
                    bundle = Bundle.from_dict(bundle_data)
                    stored = self.remember_bundle(bundle)

                    if stored:
                        received_count += 1

                    self.on_bundle_received(
                        bundle=bundle,
                        peer_node=peer_node,
                        metadata=route_metadata,
                        stored=stored,
                    )

                except Exception as exc:
                    self.log(f"error decoding incoming bundle: {exc!r}")
                    break

            self.exchange_completed(
                peer_node=peer_node,
                sent_bundles=bundles_for_peer,
                received_count=received_count,
            )

            self.record_event(
                {
                    "event": "incoming_exchange",
                    "peer": peer_node,
                    "peer_ip": addr[0],
                    "sent": len(bundles_for_peer),
                    "received": received_count,
                    "sent_bytes": sent_bytes,
                    "received_bytes": received_bytes,
                }
            )

        except Exception as exc:
            if self.is_expected_contact_failure(exc):
                self.record_event(
                    {
                        "event": "incoming_contact_missed",
                        "peer_ip": addr[0] if addr else None,
                        "error": repr(exc),
                    }
                )
            else:
                self.log(f"incoming exchange error: {exc!r}")

        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # TCP exchange client
    # ------------------------------------------------------------------

    def is_expected_contact_failure(self, exc: Exception) -> bool:
        if isinstance(exc, TimeoutError):
            return True

        if isinstance(exc, socket.timeout):
            return True

        if isinstance(exc, OSError) and getattr(exc, "errno", None) in {101, 113}:
            # 101: Network is unreachable
            # 113: No route to host
            return True

        return False

    def should_attempt_exchange(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
        force: bool = False,
    ) -> bool:
        key = (peer_node, peer_ip, peer_port)
        now = time.time()

        with self._exchange_mu:
            if key in self._active_exchanges:
                return False

            last_attempt = self._last_attempt.get(key, 0.0)
            backoff = self._backoff.get(key, self.discovery_interval)

            # Enforce current backoff or success cooldown.
            # We ignore force=True for cooldown checks to prevent connection storming.
            if now - last_attempt < backoff:
                return False

            self._active_exchanges.add(key)
            self._last_attempt[key] = now

        return True

    def finish_exchange_attempt(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
        success: bool,
        deferred: bool = False,
    ) -> None:
        key = (peer_node, peer_ip, peer_port)

        with self._exchange_mu:
            self._active_exchanges.discard(key)

            current_backoff = self._backoff.get(key, self.discovery_interval)

            if success:
                jitter = random.uniform(0.0, 0.2)
                self._backoff[key] = self.success_cooldown + jitter
            elif not deferred:
                self._backoff[key] = min(current_backoff * 2.0, self.max_backoff)

    def exchange_with_peer(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
        force: bool = False,
    ) -> None:
        """Attempt one TCP summary-vector bundle exchange."""

        if peer_node == self.node:
            return

        if not self.should_attempt_exchange(
            peer_node=peer_node,
            peer_ip=peer_ip,
            peer_port=peer_port,
            force=force,
        ):
            return

        conn: Optional[socket.socket] = None
        success = False
        slot_acquired = False

        try:
            slot_acquired = self._exchange_slots.acquire(blocking=False)

            if not slot_acquired:
                self.record_event(
                    {
                        "event": "exchange_deferred",
                        "peer": peer_node,
                        "peer_ip": peer_ip,
                        "peer_port": peer_port,
                        "reason": "max_parallel_exchanges_reached",
                    }
                )
                return

            conn = socket.create_connection(
                (peer_ip, peer_port),
                timeout=self.connect_timeout,
            )
            conn.settimeout(self.socket_timeout)

            reader = conn.makefile("r")
            writer = conn.makefile("w")
            sent_bytes = 0
            received_bytes = 0

            # Single snapshot to get both local IDs and bundles in one
            # lock+refresh cycle — eliminates the second scandir() call.
            _local_bundles, local_ids = self.store.snapshot()

            # Compute once; reused for both the opening summary and the
            # final "bundles" message so PRoPHET doesn't age+save twice.
            routing_meta = self.summary_metadata()

            request = {
                "type": "summary",
                "node": self.node,
                "ids": list(local_ids),
                "routing": routing_meta,
            }

            sent_bytes += self.send_message(writer, request)
            writer.flush()

            response, message_bytes = self.recv_message_with_size(reader)
            received_bytes += message_bytes

            if not response:
                return

            peer_ids = set(response["ids"])
            peer_bundle_count = int(response.get("bundle_count", 0))
            self.observe_peer_summary(peer_node, response)

            received_count = 0

            for _ in range(peer_bundle_count):
                bundle_data, message_bytes = self.recv_message_with_size(reader)
                received_bytes += message_bytes

                if not bundle_data:
                    break

                try:
                    route_metadata = self.received_bundle_metadata(bundle_data)
                    bundle = Bundle.from_dict(bundle_data)
                    stored = self.remember_bundle(bundle)

                    if stored:
                        received_count += 1

                    self.on_bundle_received(
                        bundle=bundle,
                        peer_node=peer_node,
                        metadata=route_metadata,
                        stored=stored,
                    )

                except Exception as exc:
                    self.log(f"error decoding incoming bundle from peer: {exc!r}")
                    break

            bundles_for_peer = self.select_bundles_for_peer(peer_ids, peer_node)

            final = {
                "type": "bundles",
                "node": self.node,
                "bundle_count": len(bundles_for_peer),
                "routing": routing_meta,
            }

            sent_bytes += self.send_message(writer, final)

            for bundle in bundles_for_peer:
                sent_bytes += self.send_message(
                    writer,
                    self.bundle_to_wire(bundle, peer_node),
                )

            writer.flush()

            sent_count = len(bundles_for_peer)
            if self.discovery_mode == "mesh":
                self.note_mesh_exchange_work(
                    peer_node=peer_node,
                    peer_ip=peer_ip,
                    peer_port=peer_port,
                    sent_count=sent_count,
                    received_count=received_count,
                )

            self.exchange_completed(
                peer_node=peer_node,
                sent_bundles=bundles_for_peer,
                received_count=received_count,
            )

            self.record_event(
                {
                    "event": "exchange",
                    "peer": peer_node,
                    "peer_ip": peer_ip,
                    "peer_port": peer_port,
                    "sent": sent_count,
                    "received": received_count,
                    "sent_bytes": sent_bytes,
                    "received_bytes": received_bytes,
                }
            )

            self.log(
                f"exchanged with peer={peer_node} ip={peer_ip} "
                f"sent={sent_count} received={received_count}"
            )

            success = True

        except Exception as exc:
            if self.is_expected_contact_failure(exc):
                self.record_event(
                    {
                        "event": "contact_missed",
                        "peer": peer_node,
                        "peer_ip": peer_ip,
                        "peer_port": peer_port,
                        "error": repr(exc),
                    }
                )

                self.maybe_log_contact_miss(
                    peer_node=peer_node,
                    peer_ip=peer_ip,
                    peer_port=peer_port,
                    exc=exc,
                )

            else:
                self.record_event(
                    {
                        "event": "exchange_failed",
                        "peer": peer_node,
                        "peer_ip": peer_ip,
                        "peer_port": peer_port,
                        "error": repr(exc),
                    }
                )

                self.log(
                    f"exchange_failed peer={peer_node} "
                    f"ip={peer_ip} error={exc!r}"
                )

        finally:
            self.finish_exchange_attempt(
                peer_node=peer_node,
                peer_ip=peer_ip,
                peer_port=peer_port,
                success=success,
                deferred=not slot_acquired,
            )

            if slot_acquired:
                try:
                    self._exchange_slots.release()
                except Exception:
                    pass

            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

    def maybe_log_contact_miss(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
        exc: Exception,
    ) -> None:
        key = (peer_node, peer_ip, peer_port)
        now = time.time()
        last_log = self._last_contact_miss_log.get(key, 0.0)

        if now - last_log < self.contact_miss_log_interval:
            return

        self._last_contact_miss_log[key] = now

        self.log(
            f"contact_missed peer={peer_node} "
            f"ip={peer_ip} error={exc!r}"
        )

    def start_exchange_thread(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
        force: bool = True,
    ) -> None:
        thread = threading.Thread(
            target=self.exchange_with_peer,
            args=(peer_node, peer_ip, peer_port),
            kwargs={"force": force},
            daemon=True,
        )
        thread.start()

    # ------------------------------------------------------------------
    # UDP neighbour discovery: discover / peer
    # ------------------------------------------------------------------

    def local_broadcast_addresses(self) -> List[str]:
        """Return local IPv4 broadcast addresses.

        Example:
            10.0.0.255

        255.255.255.255 is added as fallback.
        """

        broadcasts = set()

        try:
            output = subprocess.check_output(
                ["ip", "-o", "-4", "addr", "show", "scope", "global"],
                text=True,
            )

            for line in output.splitlines():
                parts = line.split()

                if "inet" not in parts:
                    continue

                cidr = parts[parts.index("inet") + 1]
                interface = ipaddress.ip_interface(cidr)
                broadcasts.add(str(interface.network.broadcast_address))

        except Exception as exc:
            self.log(f"could not detect broadcast addresses: {exc!r}")

        broadcasts.add("255.255.255.255")

        return sorted(broadcasts)

    def prune_seen_discovery_nonces(self) -> None:
        now = time.time()
        ttl = max(self.discovery_interval * 5.0, 10.0)

        expired = [
            nonce
            for nonce, seen_at in self._seen_discovery_nonces.items()
            if now - seen_at > ttl
        ]

        for nonce in expired:
            self._seen_discovery_nonces.pop(nonce, None)

    def send_discovery_request(
        self,
        send_sock: socket.socket,
        broadcasts: List[str],
    ) -> None:
        nonce = str(uuid.uuid4())

        message = {
            "type": "discover",
            "node": self.node,
            "exchange_port": self.exchange_port,
            "nonce": nonce,
            "time": time.time(),
        }

        encoded = json.dumps(message).encode("utf-8")

        for broadcast in broadcasts:
            try:
                send_sock.sendto(
                    encoded,
                    (broadcast, self.discovery_port),
                )

                self.record_event(
                    {
                        "event": "discovery_request_sent",
                        "broadcast": broadcast,
                        "nonce": nonce,
                    }
                )

            except Exception as exc:
                self.record_event(
                    {
                        "event": "discovery_request_failed",
                        "broadcast": broadcast,
                        "nonce": nonce,
                        "error": repr(exc),
                    }
                )

    def select_static_probe_peers(self) -> List[str]:
        known = sorted(self.static_peers)

        if not known:
            return []

        target_count = min(len(known), max(6, self.max_parallel_exchanges * 2))
        selected: List[str] = []
        attempts = 0

        while len(selected) < target_count and attempts < len(known):
            peer_node = known[self._mesh_probe_cursor % len(known)]
            self._mesh_probe_cursor += 1
            attempts += 1

            if peer_node not in selected:
                selected.append(peer_node)

        return selected

    def send_static_unicast_discovery_requests(
        self,
        send_sock: socket.socket,
        selected_peers: List[str],
    ) -> None:
        """Probe known static peers when broadcast discovery is unreliable."""

        if not selected_peers:
            return

        nonce = str(uuid.uuid4())
        message = {
            "type": "discover",
            "node": self.node,
            "exchange_port": self.exchange_port,
            "nonce": nonce,
            "time": time.time(),
            "discovery_mode": self.discovery_mode,
        }
        encoded = json.dumps(message).encode("utf-8")
        sent = 0
        failed = 0

        for peer_node in selected_peers:
            peer = self.static_peers.get(peer_node)

            if peer is None:
                continue

            peer_ip, _peer_mac = peer

            try:
                send_sock.sendto(encoded, (peer_ip, self.discovery_port))
                sent += 1
            except Exception as exc:
                failed += 1
                self.record_event(
                    {
                        "event": "static_discovery_probe_failed",
                        "peer": peer_node,
                        "peer_ip": peer_ip,
                        "nonce": nonce,
                        "error": repr(exc),
                    }
                )

        self.record_event(
            {
                "event": "static_discovery_probe_round",
                "known_peers": len(self.static_peers),
                "selected": selected_peers,
                "sent": sent,
                "failed": failed,
                "nonce": nonce,
            }
        )

    def select_mesh_probe_peers(self, station_peers: Set[str]) -> List[str]:
        known = sorted(self.static_peers)
        selected: List[str] = []

        for peer_node in sorted(station_peers):
            if peer_node in self.static_peers and peer_node not in selected:
                selected.append(peer_node)

        if not known:
            return selected

        target_count = max(self.mesh_probe_peers_per_round, len(selected))
        attempts = 0

        while len(selected) < target_count and attempts < len(known):
            peer_node = known[self._mesh_probe_cursor % len(known)]
            self._mesh_probe_cursor += 1
            attempts += 1

            if peer_node not in selected:
                selected.append(peer_node)

        return selected

    def send_unicast_discovery_requests(
        self,
        send_sock: socket.socket,
        selected_peers: List[str],
    ) -> None:
        """Probe a paced subset of known peers with unicast discover messages."""

        nonce = str(uuid.uuid4())
        message = {
            "type": "discover",
            "node": self.node,
            "exchange_port": self.exchange_port,
            "nonce": nonce,
            "time": time.time(),
            "discovery_mode": "mesh",
        }
        encoded = json.dumps(message).encode("utf-8")
        sent = 0
        failed = 0

        for peer_node in selected_peers:
            peer = self.static_peers.get(peer_node)

            if peer is None:
                continue

            peer_ip, peer_mac = peer

            try:
                send_sock.sendto(encoded, (peer_ip, self.discovery_port))
                sent += 1
            except Exception as exc:
                failed += 1
                self.record_event(
                    {
                        "event": "mesh_discovery_probe_failed",
                        "peer": peer_node,
                        "peer_ip": peer_ip,
                        "peer_mac": peer_mac,
                        "nonce": nonce,
                        "error": repr(exc),
                    }
                )

        self.record_event(
            {
                "event": "mesh_discovery_probe_round",
                "known_peers": len(self.static_peers),
                "selected": selected_peers,
                "sent": sent,
                "failed": failed,
                "nonce": nonce,
            }
        )

    def send_peer_reply(
        self,
        send_sock: socket.socket,
        dst_ip: str,
        nonce: str | None,
    ) -> None:
        message = {
            "type": "peer",
            "node": self.node,
            "exchange_port": self.exchange_port,
            "nonce": nonce,
            "time": time.time(),
        }

        try:
            send_sock.sendto(
                json.dumps(message).encode("utf-8"),
                (dst_ip, self.discovery_port),
            )

            self.record_event(
                {
                    "event": "peer_reply_sent",
                    "dst_ip": dst_ip,
                    "nonce": nonce,
                }
            )

        except Exception as exc:
            self.record_event(
                {
                    "event": "peer_reply_failed",
                    "dst_ip": dst_ip,
                    "nonce": nonce,
                    "error": repr(exc),
                }
            )

    def handle_discovery_message(
        self,
        message: dict,
        addr,
        send_sock: socket.socket,
    ) -> None:
        message_type = message.get("type")
        peer_node = message.get("node")

        if not peer_node or peer_node == self.node:
            return

        peer_ip = addr[0]
        peer_port = int(message.get("exchange_port", self.exchange_port))
        nonce = message.get("nonce")

        if message_type == "discover":
            if nonce:
                if nonce in self._seen_discovery_nonces:
                    return

                self._seen_discovery_nonces[nonce] = time.time()

            self.record_event(
                {
                    "event": "discovery_request_received",
                    "peer": peer_node,
                    "peer_ip": peer_ip,
                    "peer_port": peer_port,
                    "nonce": nonce,
                }
            )

            # Important:
            # Do NOT remember this as an actual peer.
            # A broadcast packet only proves that this node heard the peer.
            # It does not prove that TCP exchange can complete.
            self.send_peer_reply(
                send_sock=send_sock,
                dst_ip=peer_ip,
                nonce=nonce,
            )

            return

        if message_type == "peer":
            self.record_event(
                {
                    "event": "peer_reply_received",
                    "peer": peer_node,
                    "peer_ip": peer_ip,
                    "peer_port": peer_port,
                    "nonce": nonce,
                }
            )

            if self.discovery_mode == "mesh":
                self.remember_mesh_reachable_peer(
                    peer_node=peer_node,
                    peer_ip=peer_ip,
                    peer_port=peer_port,
                    source="peer_reply",
                )
                return

            # Remember the peer. Keep one-sided initiation on discovery replies
            # to avoid redundant TCP storms in dense benchmarks.
            self.peers[peer_node] = peer_ip

            # Reset backoff: fresh peer reply proves reachability, so
            # clear any accumulated failure backoff for this peer.
            key = (peer_node, peer_ip, peer_port)
            with self._exchange_mu:
                jitter = random.uniform(0.0, 0.2)
                self._backoff[key] = self.success_cooldown + jitter

            if self.node < peer_node:
                self.record_event(
                    {
                        "event": "peer_reply_exchange_initiated",
                        "peer": peer_node,
                        "peer_ip": peer_ip,
                        "peer_port": peer_port,
                    }
                )
                self.start_exchange_thread(peer_node, peer_ip, peer_port, force=True)
            else:
                self.record_event(
                    {
                        "event": "peer_reply_exchange_skipped_symmetry",
                        "peer": peer_node,
                        "peer_ip": peer_ip,
                        "peer_port": peer_port,
                    }
                )

            return

    def adhoc_discovery_loop(self) -> None:
        """UDP request/reply neighbour discovery.

        This loop periodically broadcasts "discover".
        Nodes that receive it respond with unicast "peer".
        TCP exchange is attempted only after receiving a peer reply.
        """

        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            recv_sock.bind(("0.0.0.0", self.discovery_port))
        except Exception as exc:
            self.log(f"could not bind UDP discovery socket: {exc!r}")
            return

        recv_sock.settimeout(1.0)

        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        broadcasts = self.local_broadcast_addresses()

        self.log(f"UDP neighbour discovery active udp/{self.discovery_port}")
        self.log(f"broadcast targets={broadcasts}")
        self.log("peer selection mode=udp broadcast + static unicast discover/peer")
        self.log(
            f"discovery_interval={self.discovery_interval}s "
            f"connect_timeout={self.connect_timeout}s "
            f"socket_timeout={self.socket_timeout}s "
            f"max_parallel_exchanges={self.max_parallel_exchanges}"
        )

        # Randomize first discovery to avoid synchronized startup bursts.
        next_discovery = time.time() + random.uniform(0.0, self.discovery_interval)

        while self.running:
            now = time.time()

            if now >= next_discovery:
                self.prune_seen_discovery_nonces()
                self.send_discovery_request(
                    send_sock=send_sock,
                    broadcasts=broadcasts,
                )
                self.send_static_unicast_discovery_requests(
                    send_sock=send_sock,
                    selected_peers=self.select_static_probe_peers(),
                )

                jitter = random.uniform(0.0, self.discovery_interval * 0.25)
                next_discovery = now + self.discovery_interval + jitter

            try:
                data, addr = recv_sock.recvfrom(4096)
                message = json.loads(data.decode("utf-8"))

                self.handle_discovery_message(
                    message=message,
                    addr=addr,
                    send_sock=send_sock,
                )

            except socket.timeout:
                # Check if new bundles arrived while we were waiting.
                # If so, immediately re-probe all known peers to push
                # them out without waiting for the next discovery cycle.
                if self.store.new_bundle_event.is_set():
                    self.store.new_bundle_event.clear()
                    for peer_node, peer_ip in list(self.peers.items()):
                        self.start_exchange_thread(
                            peer_node,
                            peer_ip,
                            self.exchange_port,
                            force=False,
                        )
                continue

            except Exception:
                continue

        try:
            recv_sock.close()
        except Exception:
            pass

        try:
            send_sock.close()
        except Exception:
            pass

    def mesh_neighbor_macs(self) -> Set[str]:
        """Return direct 802.11s mesh neighbours from the kernel station table."""

        try:
            output = subprocess.check_output(
                ["iw", "dev", self.wireless_iface, "station", "dump"],
                text=True,
                stderr=subprocess.STDOUT,
                timeout=max(1.0, min(self.discovery_interval, 5.0)),
            )
        except FileNotFoundError as exc:
            self.record_event(
                {
                    "event": "mesh_neighbor_scan_failed",
                    "iface": self.wireless_iface,
                    "error": repr(exc),
                }
            )
            self.log("mesh discovery requires the `iw` command")
            return set()
        except Exception as exc:
            self.record_event(
                {
                    "event": "mesh_neighbor_scan_failed",
                    "iface": self.wireless_iface,
                    "error": repr(exc),
                }
            )
            return set()

        macs: Set[str] = set()

        for line in output.splitlines():
            line = line.strip()

            if not line.startswith("Station "):
                continue

            parts = line.split()

            if len(parts) >= 2:
                mac = self.normalize_mac(parts[1])

                if mac:
                    macs.add(mac)

        return macs

    def should_log_mesh_seen(self, key: str) -> bool:
        now = time.time()
        last_log = self._last_mesh_neighbor_log.get(key, 0.0)
        interval = max(self.discovery_interval * 5.0, 5.0)

        if now - last_log < interval:
            return False

        self._last_mesh_neighbor_log[key] = now
        return True

    def should_log_mesh_unknown(self, mac: str) -> bool:
        now = time.time()
        last_log = self._last_mesh_unknown_log.get(mac, 0.0)
        interval = max(self.discovery_interval * 10.0, 10.0)

        if now - last_log < interval:
            return False

        self._last_mesh_unknown_log[mac] = now
        return True

    def remember_mesh_reachable_peer(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
        source: str,
    ) -> None:
        if self.discovery_mode != "mesh":
            return

        now = time.time()
        self.peers[peer_node] = peer_ip
        self.mesh_reachable_peers[peer_node] = (peer_ip, peer_port, now)

        last_log = self._last_mesh_reachable_log.get(peer_node, 0.0)
        if now - last_log >= max(self.discovery_interval * 5.0, 5.0):
            self._last_mesh_reachable_log[peer_node] = now
            self.record_event(
                {
                    "event": "mesh_peer_reachable",
                    "peer": peer_node,
                    "peer_ip": peer_ip,
                    "peer_port": peer_port,
                    "source": source,
                }
            )

    def prune_mesh_reachable_peers(self) -> None:
        now = time.time()
        expired = [
            peer_node
            for peer_node, (_peer_ip, _peer_port, seen_at)
            in self.mesh_reachable_peers.items()
            if now - seen_at > self.mesh_peer_ttl
        ]

        for peer_node in expired:
            self.mesh_reachable_peers.pop(peer_node, None)

    def mesh_exchange_backoff_remaining(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
    ) -> float:
        key = (peer_node, peer_ip, peer_port)
        now = time.time()

        with self._exchange_mu:
            if key in self._active_exchanges:
                return self.max_backoff

            last_attempt = self._last_attempt.get(key, 0.0)
            backoff = self._backoff.get(key, self.discovery_interval)
            return max(0.0, backoff - (now - last_attempt))

    def mesh_empty_sync_remaining(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
    ) -> float:
        key = (peer_node, peer_ip, peer_port)
        empty_sync = self._mesh_empty_sync.get(key)

        if empty_sync is None:
            return 0.0

        until, bundle_count = empty_sync
        remaining = until - time.time()

        if remaining <= 0.0:
            self._mesh_empty_sync.pop(key, None)
            return 0.0

        current_count = len(self.store.ids())

        if current_count > bundle_count:
            self._mesh_empty_sync.pop(key, None)
            return 0.0

        return remaining

    def note_mesh_exchange_work(
        self,
        *,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
        sent_count: int,
        received_count: int,
    ) -> None:
        key = (peer_node, peer_ip, peer_port)

        if sent_count == 0 and received_count == 0:
            jitter = random.uniform(0.0, 0.2)
            self._mesh_empty_sync[key] = (
                time.time() + max(self.success_cooldown + jitter, min(self.discovery_interval, 0.5)),
                len(self.store.ids()),
            )
        else:
            self._mesh_empty_sync.pop(key, None)

    def schedule_mesh_exchanges(self) -> None:
        self.prune_mesh_reachable_peers()

        candidates = sorted(self.mesh_reachable_peers)

        if not candidates:
            return

        scheduled = 0
        inspected = 0

        while scheduled < self.mesh_exchange_peers_per_tick and inspected < len(candidates):
            peer_node = candidates[self._mesh_exchange_cursor % len(candidates)]
            self._mesh_exchange_cursor += 1
            inspected += 1

            peer_ip, peer_port, _seen_at = self.mesh_reachable_peers[peer_node]

            if self.node >= peer_node:
                self.record_event(
                    {
                        "event": "mesh_exchange_skipped_symmetry",
                        "peer": peer_node,
                        "peer_ip": peer_ip,
                        "peer_port": peer_port,
                    }
                )
                continue

            remaining = self.mesh_exchange_backoff_remaining(
                peer_node=peer_node,
                peer_ip=peer_ip,
                peer_port=peer_port,
            )

            if remaining > 0.0:
                self.record_event(
                    {
                        "event": "mesh_exchange_skipped_backoff",
                        "peer": peer_node,
                        "peer_ip": peer_ip,
                        "peer_port": peer_port,
                        "remaining_s": remaining,
                    }
                )
                continue

            empty_remaining = self.mesh_empty_sync_remaining(
                peer_node=peer_node,
                peer_ip=peer_ip,
                peer_port=peer_port,
            )

            if empty_remaining > 0.0:
                self.record_event(
                    {
                        "event": "mesh_exchange_skipped_no_work",
                        "peer": peer_node,
                        "peer_ip": peer_ip,
                        "peer_port": peer_port,
                        "remaining_s": empty_remaining,
                    }
                )
                continue

            self.record_event(
                {
                    "event": "mesh_exchange_scheduled",
                    "peer": peer_node,
                    "peer_ip": peer_ip,
                    "peer_port": peer_port,
                }
            )
            jitter = random.uniform(0.0, 0.5)
            threading.Timer(
                jitter,
                self.start_exchange_thread,
                args=(peer_node, peer_ip, peer_port, False)
            ).start()
            scheduled += 1

        if scheduled == 0:
            self.record_event(
                {
                    "event": "mesh_exchange_skipped_no_budget",
                    "reachable_peers": len(candidates),
                }
            )

    def mesh_discovery_loop(self) -> None:
        """Mesh neighbour discovery using station dump plus unicast probes."""

        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            recv_sock.bind(("0.0.0.0", self.discovery_port))
        except Exception as exc:
            self.log(f"could not bind mesh UDP discovery socket: {exc!r}")
            return

        recv_sock.settimeout(0.25)
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        broadcasts = self.local_broadcast_addresses()

        self.log(
            f"mesh neighbour discovery active iface={self.wireless_iface} "
            f"known_peers={len(self.static_peers)} mac_peers={len(self.peers_by_mac)}"
        )
        self.log("peer selection mode=mesh station dump + unicast discover/peer")
        self.log(
            f"discovery_interval={self.discovery_interval}s "
            f"connect_timeout={self.connect_timeout}s "
            f"socket_timeout={self.socket_timeout}s "
            f"max_parallel_exchanges={self.max_parallel_exchanges}"
        )

        next_scan = time.time() + random.uniform(0.0, self.discovery_interval)

        while self.running:
            now = time.time()

            if now >= next_scan:
                neighbor_macs = self.mesh_neighbor_macs()

                if not neighbor_macs:
                    self.record_event(
                        {
                            "event": "mesh_station_scan_empty",
                            "iface": self.wireless_iface,
                        }
                    )
                    self.send_discovery_request(
                        send_sock=send_sock,
                        broadcasts=broadcasts,
                    )

                station_peers: Set[str] = set()

                for mac in sorted(neighbor_macs):
                    peer = self.peers_by_mac.get(mac)

                    if peer is None:
                        if self.should_log_mesh_unknown(mac):
                            self.record_event(
                                {
                                    "event": "mesh_neighbor_unknown_mac",
                                    "iface": self.wireless_iface,
                                    "peer_mac": mac,
                                }
                            )
                        continue

                    peer_node, peer_ip = peer
                    station_peers.add(peer_node)
                    self.remember_mesh_reachable_peer(
                        peer_node=peer_node,
                        peer_ip=peer_ip,
                        peer_port=self.exchange_port,
                        source="station_dump",
                    )

                    if self.should_log_mesh_seen(peer_node):
                        self.record_event(
                            {
                                "event": "mesh_neighbor_seen",
                                "iface": self.wireless_iface,
                                "peer": peer_node,
                                "peer_ip": peer_ip,
                                "peer_mac": mac,
                                "peer_port": self.exchange_port,
                            }
                        )

                selected_peers = self.select_mesh_probe_peers(station_peers)
                self.prune_seen_discovery_nonces()
                self.send_unicast_discovery_requests(send_sock, selected_peers)
                self.schedule_mesh_exchanges()
                jitter = random.uniform(0.0, self.discovery_interval * 0.25)
                next_scan = now + self.discovery_interval + jitter

            try:
                data, addr = recv_sock.recvfrom(4096)
                message = json.loads(data.decode("utf-8"))
                self.handle_discovery_message(
                    message=message,
                    addr=addr,
                    send_sock=send_sock,
                )
            except socket.timeout:
                # Push newly-arrived bundles to reachable mesh peers
                # immediately, without waiting for the next scan cycle.
                if self.store.new_bundle_event.is_set():
                    self.store.new_bundle_event.clear()
                    self.schedule_mesh_exchanges()
                continue
            except Exception:
                continue

        try:
            recv_sock.close()
        except Exception:
            pass

        try:
            send_sock.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def run(self) -> None:
        threading.Thread(target=self.tcp_server, daemon=True).start()
        self.log(f"discovery_mode={self.discovery_mode}")

        if self.discovery_mode == "mesh":
            self.mesh_discovery_loop()
        else:
            self.adhoc_discovery_loop()


def inject_bundle(args: argparse.Namespace) -> None:
    """Inject one bundle into the local BundleStore."""

    store = BundleStore(args.store)

    if args.payload_json is not None:
        try:
            payload = json.loads(args.payload_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid --payload-json: {exc}") from exc
    else:
        payload = {
            "app": "meshpay.demo",
            "type": "text",
            "message": args.payload,
        }

    bundle = Bundle.create(
        src=args.node,
        dst=args.dst,
        payload=payload,
        ttl=args.ttl,
    )

    store.save(bundle)

    store.record_event(
        {
            "event": "created",
            "node": args.node,
            "bundle_id": bundle.bundle_id,
            "src": bundle.src,
            "dst": bundle.dst,
            "size_bytes": bundle.size_bytes,
            "payload": bundle.payload,
        }
    )

    print(
        f"Injected bundle={bundle.bundle_id} "
        f"src={bundle.src} dst={bundle.dst}"
    )


def parse_peer(peer: str) -> Tuple[str, str, str | None]:
    """Parse --peer name=ip[,mac] entries for mesh neighbour mapping."""

    if "=" not in peer:
        raise argparse.ArgumentTypeError(
            f"Invalid peer format: {peer}. Expected name=ip[,mac]"
        )

    name, value = peer.split("=", 1)
    parts = value.split(",", 1)
    ip = parts[0].strip()
    mac = parts[1].strip() if len(parts) == 2 else None

    if not name or not ip:
        raise argparse.ArgumentTypeError(
            f"Invalid peer format: {peer}. Expected name=ip[,mac]"
        )

    return name.strip(), ip, mac


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MeshPay Epidemic DTN router with medium-specific neighbour discovery"
    )

    parser.add_argument("--node", required=True)
    parser.add_argument("--store", required=True)

    parser.add_argument(
        "--peer",
        action="append",
        type=parse_peer,
        default=[],
        help="Known peer entry name=ip[,mac]. Mesh mode uses MACs to map station dump output.",
    )

    parser.add_argument(
        "--discovery-mode",
        choices=["adhoc", "mesh"],
        default="adhoc",
        help="Neighbour discovery mechanism to use.",
    )

    parser.add_argument(
        "--wireless-iface",
        help="Wireless interface for mesh station-table discovery. Defaults to <node>-wlan0.",
    )

    parser.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT)
    parser.add_argument("--exchange-port", type=int, default=DEFAULT_EXCHANGE_PORT)

    parser.add_argument(
        "--discovery-interval",
        type=float,
        default=config.DEFAULT_DISCOVERY_INTERVAL,
        help="UDP discovery request interval.",
    )

    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=config.DEFAULT_CONNECT_TIMEOUT,
        help="TCP connect timeout for bundle exchange.",
    )

    parser.add_argument(
        "--socket-timeout",
        type=float,
        default=config.DEFAULT_SOCKET_TIMEOUT,
        help="TCP read/write timeout for bundle exchange.",
    )

    parser.add_argument(
        "--max-backoff",
        type=float,
        default=config.DEFAULT_MAX_BACKOFF,
        help="Maximum per-peer exchange retry backoff.",
    )

    parser.add_argument(
        "--max-parallel-exchanges",
        type=int,
        default=config.DEFAULT_MAX_PARALLEL_EXCHANGES,
        help="Maximum simultaneous outgoing TCP exchanges per node.",
    )

    parser.add_argument(
        "--contact-miss-log-interval",
        type=float,
        default=config.DEFAULT_CONTACT_MISS_LOG_INTERVAL,
        help="Minimum seconds between printed contact_missed logs per peer.",
    )

    parser.add_argument(
        "--success-cooldown",
        type=float,
        default=config.DEFAULT_SUCCESS_COOLDOWN,
        help="Cooldown interval (in seconds) after a successful exchange.",
    )

    parser.add_argument("--inject", action="store_true")
    parser.add_argument("--dst")
    parser.add_argument("--payload")
    parser.add_argument("--payload-json")
    parser.add_argument("--ttl", type=float, default=300.0)

    return parser.parse_args()


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
    )

    router.run()


if __name__ == "__main__":
    main()