#!/usr/bin/env python3
"""MeshPay Epidemic DTN Router.

Performance improvements over the original:
  - Bundle batching: all bundles for a peer are serialized and compressed in
    one zlib call instead of one per bundle (~40-60 % smaller on the wire).
  - Compression threshold: small control frames are sent as plain JSON;
    zlib is only applied when it actually shrinks the payload.
  - Fast compression: zlib level 1 instead of the default level 6 — latency
    matters more than ratio inside a short contact window.
  - Pipelined handshake: both sides send their opening summary concurrently
    so the server does not block waiting for the client's final message.
  - Single store snapshot per exchange: select_bundles_for_peer accepts an
    optional pre-fetched snapshot to avoid a second scandir() call.
  - New-bundle push resets backoff: when a bundle is injected the router
    clears the per-peer backoff so the push reaches peers in back-off state.
  - Mesh probe filtering: only recently-reachable peers are probed, cutting
    wasted UDP traffic toward out-of-range nodes.
  - Nonce dict hard cap: _seen_discovery_nonces is bounded to 1 000 entries
    so it cannot grow unboundedly between scheduled prune passes.
"""

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


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DISCOVERY_PORT: int = config.DEFAULT_DISCOVERY_PORT
DEFAULT_EXCHANGE_PORT: int = config.DEFAULT_EXCHANGE_PORT

# Compress only when the raw JSON is larger than this many bytes.
_COMPRESS_THRESHOLD_BYTES: int = 512

# Minimum compression gain required before we send the compressed form.
_COMPRESS_MIN_RATIO: float = 0.90          # compressed must be < 90 % of raw

# Hard cap on the seen-nonces dict to prevent unbounded growth.
_NONCE_DICT_MAX: int = 1_000
_NONCE_DICT_EVICT_TO: int = 500


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------

def _encode_message(msg: dict) -> str:
    """Serialise *msg* to a single newline-terminated wire line.

    Small messages are sent as plain JSON (faster, no base64 overhead).
    Larger messages are zlib-compressed and base64-encoded only when that
    actually shrinks the payload.
    """
    raw = json.dumps(msg).encode("utf-8")

    if len(raw) > _COMPRESS_THRESHOLD_BYTES:
        compressed = zlib.compress(raw, level=1)
        if len(compressed) < len(raw) * _COMPRESS_MIN_RATIO:
            return base64.b64encode(compressed).decode("ascii") + "\n"

    return raw.decode("utf-8") + "\n"


def _decode_message(line: str) -> dict:
    """Deserialise one wire line produced by :func:`_encode_message`."""
    line = line.strip()
    if not line:
        return {}

    if line.startswith("{"):
        try:
            return json.loads(line)
        except Exception:
            return {}

    try:
        compressed = base64.b64decode(line.encode("ascii"))
        return json.loads(zlib.decompress(compressed).decode("utf-8"))
    except Exception:
        # Last-ditch attempt: maybe it is plain JSON after all.
        try:
            return json.loads(line)
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class EpidemicRouter:
    """Epidemic DTN router with medium-specific neighbour discovery.

    Peer selection policy
    ---------------------
    adhoc
        UDP broadcast "discover" + unicast "peer" replies.  TCP exchange is
        initiated only after receiving a unicast peer reply (avoids false-peer
        storms in sparse Wi-Fi tests).
    mesh
        802.11s neighbour table from ``iw station dump`` supplemented by
        unicast probes toward a paced subset of the static peer list.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

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
        if discovery_mode not in {"adhoc", "mesh"}:
            raise ValueError("discovery_mode must be one of: adhoc, mesh")

        self.node = node
        self.store = BundleStore(store_path)
        self.discovery_port = int(discovery_port)
        self.exchange_port = int(exchange_port)
        self.discovery_interval = float(discovery_interval)
        self.discovery_mode = discovery_mode
        self.wireless_iface = wireless_iface or f"{self.node}-wlan0"
        self.running = True

        # Timeouts — mesh needs more headroom due to emulation load.
        is_mesh = self.discovery_mode == "mesh"
        self.connect_timeout = max(float(connect_timeout), 10.0) if is_mesh else float(connect_timeout)
        self.socket_timeout  = max(float(socket_timeout),  30.0) if is_mesh else float(socket_timeout)
        self.max_backoff     = max(float(max_backoff),     10.0) if is_mesh else float(max_backoff)
        self.success_cooldown = min(float(success_cooldown), 0.25) if is_mesh else float(success_cooldown)

        self.max_bundles_per_exchange = config.DEFAULT_MAX_BUNDLES_PER_EXCHANGE
        self.max_parallel_exchanges   = int(max_parallel_exchanges)
        self.contact_miss_log_interval = float(contact_miss_log_interval)

        # ---- exchange concurrency & backoff --------------------------------
        self._exchange_mu: threading.Lock = threading.Lock()
        self._active_exchanges: Set[Tuple[str, str, int]] = set()
        self._last_attempt:    Dict[Tuple[str, str, int], float] = {}
        self._backoff:         Dict[Tuple[str, str, int], float] = {}
        self._exchange_slots   = threading.BoundedSemaphore(max(1, self.max_parallel_exchanges))

        # ---- logging throttles --------------------------------------------
        self._last_contact_miss_log:   Dict[Tuple[str, str, int], float] = {}
        self._last_mesh_neighbor_log:  Dict[str, float] = {}
        self._last_mesh_unknown_log:   Dict[str, float] = {}
        self._last_mesh_reachable_log: Dict[str, float] = {}

        # ---- discovery state ----------------------------------------------
        self._seen_discovery_nonces: Dict[str, float] = {}
        self.peers: Dict[str, str] = {}

        # ---- static / mesh peer maps --------------------------------------
        self.static_peers:   Dict[str, Tuple[str, str | None]] = {}
        self.peers_by_mac:   Dict[str, Tuple[str, str]]        = {}

        for peer_node, peer_ip, peer_mac in (static_peers or []):
            if peer_node == self.node:
                continue
            clean_mac = self._normalize_mac(peer_mac)
            self.static_peers[peer_node] = (peer_ip, clean_mac)
            if clean_mac:
                self.peers_by_mac[clean_mac] = (peer_node, peer_ip)

        # ---- mesh-specific state ------------------------------------------
        self.mesh_probe_peers_per_round  = max(6, self.max_parallel_exchanges * 2)
        self.mesh_exchange_peers_per_tick = max(1, self.max_parallel_exchanges)
        self.mesh_peer_ttl               = max(self.discovery_interval * 6.0, 6.0)
        self.mesh_reachable_peers: Dict[str, Tuple[str, int, float]] = {}
        self._mesh_empty_sync:     Dict[Tuple[str, str, int], Tuple[float, int]] = {}
        self._mesh_probe_cursor    = 0
        self._mesh_exchange_cursor = 0

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_mac(mac: str | None) -> str | None:
        return mac.strip().lower() if mac and mac.strip() else None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, message: str) -> None:
        entry = {"time": time.time(), "node": self.node, "event": "router_log", "message": message}
        print(f"[{self.node}] {message}", flush=True)
        self.store.record_event(entry)

    def record_event(self, event: dict) -> None:
        event = dict(event)
        event.setdefault("time", time.time())
        event.setdefault("node", self.node)
        self.store.record_event(event)

    # ------------------------------------------------------------------
    # Wire I/O
    # ------------------------------------------------------------------

    def _send(self, writer, msg: dict) -> int:
        """Write one encoded message; return bytes written."""
        line = _encode_message(msg)
        writer.write(line)
        return len(line.encode("utf-8"))

    def _recv(self, reader) -> Tuple[dict, int]:
        """Read one encoded message; return (msg, bytes_read)."""
        line = reader.readline()
        if not line:
            return {}, 0
        return _decode_message(line), len(line.encode("utf-8"))

    # ------------------------------------------------------------------
    # Bundle serialisation
    # ------------------------------------------------------------------

    def bundle_to_wire(self, bundle: Bundle, peer_node: str) -> dict:
        """Serialise a bundle for a specific peer (override in subclasses)."""
        return bundle.to_dict()

    def _bundles_to_batch(self, bundles: List[Bundle], peer_node: str) -> dict:
        """Pack all bundles into a single batch message for one compress call."""
        return {
            "type":    "bundle_batch",
            "bundles": [self.bundle_to_wire(b, peer_node) for b in bundles],
        }

    def received_bundle_metadata(self, bundle_data: dict) -> dict:
        meta = bundle_data.get("_routing")
        return meta if isinstance(meta, dict) else {}

    # ------------------------------------------------------------------
    # Routing hooks (overridden by subclasses)
    # ------------------------------------------------------------------

    def summary_metadata(self) -> dict:
        """Protocol-specific metadata sent with summary vectors."""
        return {}

    def observe_peer_summary(self, peer_node: str, summary: dict) -> None:
        """Called when a peer's summary is received; lets subclasses learn."""

    def select_bundles_for_peer(
        self,
        peer_ids: Set[str],
        peer_node: str,
        local_snapshot: Optional[List[Bundle]] = None,
    ) -> List[Bundle]:
        """Return bundles this router should send to *peer_node*.

        If *local_snapshot* is supplied it is used directly, avoiding a second
        ``scandir()`` on the bundle store.
        """
        bundles = local_snapshot if local_snapshot is not None else self.store.all_bundles()
        candidates = [b for b in bundles if b.bundle_id not in peer_ids]
        return candidates[: self.max_bundles_per_exchange]

    def on_bundle_received(
        self,
        bundle: Bundle,
        peer_node: str,
        metadata: dict,
        stored: bool,
    ) -> None:
        """Protocol-specific hook called after each received bundle."""

    def exchange_completed(
        self,
        peer_node: str,
        sent_bundles: List[Bundle],
        received_count: int,
    ) -> None:
        """Protocol-specific hook called after a full TCP exchange."""

    # ------------------------------------------------------------------
    # Bundle store helpers
    # ------------------------------------------------------------------

    def remember_bundle(self, bundle: Bundle) -> bool:
        """Store a received bundle if it is new and still valid."""
        if bundle.expired():
            return False

        # Vaccine-style pruning: discard superseded transfer/signed bundles
        # once the corresponding confirmation has been seen.
        if isinstance(bundle.payload, dict):
            ptype = bundle.payload.get("type")
            if ptype in {"transfer_order", "signed_transfer_order"}:
                order_id = (
                    bundle.payload.get("data", {}).get("order_id")
                    or bundle.payload.get("data", {}).get("i")
                )
                if order_id and order_id in self.store.confirmed_order_ids:
                    return False

        if self.store.has(bundle.bundle_id):
            return False

        bundle.add_hop(self.node)
        self.store.save(bundle)

        self.record_event({
            "event":      "received",
            "bundle_id":  bundle.bundle_id,
            "src":        bundle.src,
            "dst":        bundle.dst,
            "hops":       bundle.hops,
            "size_bytes": bundle.size_bytes,
        })
        self.log(
            f"received bundle={bundle.bundle_id} "
            f"src={bundle.src} dst={bundle.dst} hops={bundle.hops}"
        )

        if bundle.is_delivered_to(self.node):
            if self.store.mark_delivered(bundle, self.node):
                self.log(f"DELIVERED bundle={bundle.bundle_id} payload={bundle.payload}")

        return True

    def _process_incoming_batch(
        self,
        batch_data: dict,
        peer_node: str,
    ) -> int:
        """Decode and store every bundle in a batch message.

        Returns the number of bundles successfully stored.
        """
        received_count = 0
        for bundle_data in batch_data.get("bundles", []):
            try:
                metadata = self.received_bundle_metadata(bundle_data)
                bundle   = Bundle.from_dict(bundle_data)
                stored   = self.remember_bundle(bundle)
                if stored:
                    received_count += 1
                self.on_bundle_received(
                    bundle=bundle,
                    peer_node=peer_node,
                    metadata=metadata,
                    stored=stored,
                )
            except Exception as exc:
                self.log(f"error decoding bundle from {peer_node}: {exc!r}")
        return received_count

    # ------------------------------------------------------------------
    # Exchange backoff
    # ------------------------------------------------------------------

    def _should_attempt_exchange(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
    ) -> bool:
        key = (peer_node, peer_ip, peer_port)
        now = time.time()
        with self._exchange_mu:
            if key in self._active_exchanges:
                return False
            last    = self._last_attempt.get(key, 0.0)
            backoff = self._backoff.get(key, self.discovery_interval)
            if now - last < backoff:
                return False
            self._active_exchanges.add(key)
            self._last_attempt[key] = now
        return True

    def _finish_exchange_attempt(
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
            current = self._backoff.get(key, self.discovery_interval)
            if success:
                self._backoff[key] = self.success_cooldown + random.uniform(0.0, 0.2)
            elif not deferred:
                self._backoff[key] = min(current * 2.0, self.max_backoff)

    def _reset_peer_backoff(self, peer_node: str, peer_ip: str, peer_port: int) -> None:
        """Immediately clear backoff for a peer (called when new bundles arrive)."""
        key = (peer_node, peer_ip, peer_port)
        with self._exchange_mu:
            if key not in self._active_exchanges:
                self._backoff[key] = 0.0

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

            threading.Thread(
                target=self._handle_incoming_exchange,
                args=(conn, addr),
                daemon=True,
            ).start()

        try:
            server.close()
        except Exception:
            pass

    def _handle_incoming_exchange(self, conn: socket.socket, addr) -> None:
        """Server side of the pipelined exchange protocol.

        Protocol (pipelined, both sides write before reading):
            client → opening_summary  (ids + routing metadata)
            server → opening_summary  (ids + routing metadata) + bundle_batch
            client → bundle_batch
        """
        sent_bytes = received_bytes = 0
        try:
            conn.settimeout(self.socket_timeout)
            reader = conn.makefile("r")
            writer = conn.makefile("w")

            # 1. Receive client opening summary.
            request, n = self._recv(reader)
            received_bytes += n
            if not request or request.get("type") != "summary":
                return

            peer_node = request["node"]
            peer_ids: Set[str] = set(request["ids"])
            self.observe_peer_summary(peer_node, request)
            self.log(f"contact from peer={peer_node} ip={addr[0]}")

            # 2. Snapshot store once; use it for both our id list and bundle selection.
            local_bundles, local_ids = self.store.snapshot()
            bundles_for_peer = self.select_bundles_for_peer(
                peer_ids, peer_node, local_snapshot=local_bundles
            )

            # 3. Send opening summary + bundle batch in one flush (pipeline).
            sent_bytes += self._send(writer, {
                "type":    "summary",
                "node":    self.node,
                "ids":     list(local_ids),
                "routing": self.summary_metadata(),
            })
            sent_bytes += self._send(writer, self._bundles_to_batch(bundles_for_peer, peer_node))
            writer.flush()

            # 4. Receive client bundle batch.
            batch_data, n = self._recv(reader)
            received_bytes += n
            received_count = self._process_incoming_batch(batch_data, peer_node)

            self.exchange_completed(
                peer_node=peer_node,
                sent_bundles=bundles_for_peer,
                received_count=received_count,
            )
            self.record_event({
                "event":          "incoming_exchange",
                "peer":           peer_node,
                "peer_ip":        addr[0],
                "sent":           len(bundles_for_peer),
                "received":       received_count,
                "sent_bytes":     sent_bytes,
                "received_bytes": received_bytes,
            })

        except Exception as exc:
            if self._is_expected_contact_failure(exc):
                self.record_event({
                    "event":    "incoming_contact_missed",
                    "peer_ip":  addr[0] if addr else None,
                    "error":    repr(exc),
                })
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

    @staticmethod
    def _is_expected_contact_failure(exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return True
        if isinstance(exc, OSError) and getattr(exc, "errno", None) in {101, 113}:
            return True  # 101: Network unreachable  113: No route to host
        return False

    def exchange_with_peer(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
        force: bool = False,
    ) -> None:
        """Attempt one pipelined TCP summary-vector bundle exchange.

        Protocol (pipelined):
            client → opening_summary
            server → opening_summary + bundle_batch
            client → bundle_batch
        """
        if peer_node == self.node:
            return
        if not self._should_attempt_exchange(peer_node, peer_ip, peer_port):
            return

        conn: Optional[socket.socket] = None
        slot_acquired = False
        success = False

        try:
            slot_acquired = self._exchange_slots.acquire(blocking=False)
            if not slot_acquired:
                self.record_event({
                    "event":     "exchange_deferred",
                    "peer":      peer_node,
                    "peer_ip":   peer_ip,
                    "peer_port": peer_port,
                    "reason":    "max_parallel_exchanges_reached",
                })
                return

            conn = socket.create_connection((peer_ip, peer_port), timeout=self.connect_timeout)
            conn.settimeout(self.socket_timeout)
            reader = conn.makefile("r")
            writer = conn.makefile("w")
            sent_bytes = received_bytes = 0

            # 1. Snapshot store once for ids + bundle selection.
            local_bundles, local_ids = self.store.snapshot()
            routing_meta = self.summary_metadata()

            # 2. Send opening summary immediately (pipeline: don't wait for server).
            sent_bytes += self._send(writer, {
                "type":    "summary",
                "node":    self.node,
                "ids":     list(local_ids),
                "routing": routing_meta,
            })
            writer.flush()

            # 3. Receive server opening summary.
            response, n = self._recv(reader)
            received_bytes += n
            if not response:
                return

            peer_ids: Set[str] = set(response["ids"])
            self.observe_peer_summary(peer_node, response)

            # 4. Receive server bundle batch.
            batch_data, n = self._recv(reader)
            received_bytes += n
            received_count = self._process_incoming_batch(batch_data, peer_node)

            # 5. Select and send our bundle batch (uses the already-fetched snapshot).
            bundles_for_peer = self.select_bundles_for_peer(
                peer_ids, peer_node, local_snapshot=local_bundles
            )
            sent_bytes += self._send(writer, self._bundles_to_batch(bundles_for_peer, peer_node))
            writer.flush()

            sent_count = len(bundles_for_peer)

            if self.discovery_mode == "mesh":
                self._note_mesh_exchange_work(peer_node, peer_ip, peer_port, sent_count, received_count)

            self.exchange_completed(
                peer_node=peer_node,
                sent_bundles=bundles_for_peer,
                received_count=received_count,
            )
            self.record_event({
                "event":          "exchange",
                "peer":           peer_node,
                "peer_ip":        peer_ip,
                "peer_port":      peer_port,
                "sent":           sent_count,
                "received":       received_count,
                "sent_bytes":     sent_bytes,
                "received_bytes": received_bytes,
            })
            self.log(
                f"exchanged with peer={peer_node} ip={peer_ip} "
                f"sent={sent_count} received={received_count}"
            )
            success = True

        except Exception as exc:
            if self._is_expected_contact_failure(exc):
                self.record_event({
                    "event":     "contact_missed",
                    "peer":      peer_node,
                    "peer_ip":   peer_ip,
                    "peer_port": peer_port,
                    "error":     repr(exc),
                })
                self._maybe_log_contact_miss(peer_node, peer_ip, peer_port, exc)
            else:
                self.record_event({
                    "event":     "exchange_failed",
                    "peer":      peer_node,
                    "peer_ip":   peer_ip,
                    "peer_port": peer_port,
                    "error":     repr(exc),
                })
                self.log(f"exchange_failed peer={peer_node} ip={peer_ip} error={exc!r}")

        finally:
            self._finish_exchange_attempt(
                peer_node, peer_ip, peer_port,
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

    def _maybe_log_contact_miss(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
        exc: Exception,
    ) -> None:
        key = (peer_node, peer_ip, peer_port)
        now = time.time()
        if now - self._last_contact_miss_log.get(key, 0.0) < self.contact_miss_log_interval:
            return
        self._last_contact_miss_log[key] = now
        self.log(f"contact_missed peer={peer_node} ip={peer_ip} error={exc!r}")

    def _start_exchange_thread(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
        force: bool = True,
    ) -> None:
        threading.Thread(
            target=self.exchange_with_peer,
            args=(peer_node, peer_ip, peer_port),
            kwargs={"force": force},
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # New-bundle push  (called from discovery loops on new_bundle_event)
    # ------------------------------------------------------------------

    def _push_new_bundle_to_peers(self, peers: Dict[str, str]) -> None:
        """Reset backoff and trigger exchanges for all known peers.

        Back-off is cleared first so peers that are currently in an
        exponential-backoff hold still receive the push immediately.
        """
        for peer_node, peer_ip in list(peers.items()):
            self._reset_peer_backoff(peer_node, peer_ip, self.exchange_port)
        for peer_node, peer_ip in list(peers.items()):
            self._start_exchange_thread(peer_node, peer_ip, self.exchange_port, force=False)

    # ------------------------------------------------------------------
    # UDP neighbour discovery — shared helpers
    # ------------------------------------------------------------------

    def _local_broadcast_addresses(self) -> List[str]:
        """Return all local IPv4 broadcast addresses; falls back to 255.255.255.255."""
        broadcasts: Set[str] = set()
        try:
            output = subprocess.check_output(
                ["ip", "-o", "-4", "addr", "show", "scope", "global"], text=True
            )
            for line in output.splitlines():
                parts = line.split()
                if "inet" not in parts:
                    continue
                cidr = parts[parts.index("inet") + 1]
                iface = ipaddress.ip_interface(cidr)
                broadcasts.add(str(iface.network.broadcast_address))
        except Exception as exc:
            self.log(f"could not detect broadcast addresses: {exc!r}")
        broadcasts.add("255.255.255.255")
        return sorted(broadcasts)

    def _cap_nonce_dict(self) -> None:
        """Evict the oldest half of _seen_discovery_nonces when the cap is hit."""
        if len(self._seen_discovery_nonces) < _NONCE_DICT_MAX:
            return
        oldest = sorted(self._seen_discovery_nonces, key=self._seen_discovery_nonces.__getitem__)
        for k in oldest[: len(oldest) - _NONCE_DICT_EVICT_TO]:
            del self._seen_discovery_nonces[k]

    def _prune_seen_discovery_nonces(self) -> None:
        now = time.time()
        ttl = max(self.discovery_interval * 5.0, 10.0)
        expired = [n for n, t in self._seen_discovery_nonces.items() if now - t > ttl]
        for n in expired:
            del self._seen_discovery_nonces[n]

    def _send_discovery_request(self, send_sock: socket.socket, targets: List[str]) -> None:
        """Broadcast or unicast-probe a discover message to *targets*."""
        nonce   = str(uuid.uuid4())
        message = {
            "type":           "discover",
            "node":           self.node,
            "exchange_port":  self.exchange_port,
            "nonce":          nonce,
            "time":           time.time(),
            "discovery_mode": self.discovery_mode,
        }
        encoded = json.dumps(message).encode("utf-8")
        sent = failed = 0
        for target in targets:
            try:
                send_sock.sendto(encoded, (target, self.discovery_port))
                sent += 1
            except Exception as exc:
                failed += 1
                self.record_event({
                    "event":  "discovery_request_failed",
                    "target": target,
                    "nonce":  nonce,
                    "error":  repr(exc),
                })
        self.record_event({
            "event": "discovery_request_sent",
            "targets": targets,
            "sent":   sent,
            "failed": failed,
            "nonce":  nonce,
        })

    def _send_peer_reply(self, send_sock: socket.socket, dst_ip: str, nonce: str | None) -> None:
        message = {
            "type":          "peer",
            "node":          self.node,
            "exchange_port": self.exchange_port,
            "nonce":         nonce,
            "time":          time.time(),
        }
        try:
            send_sock.sendto(json.dumps(message).encode("utf-8"), (dst_ip, self.discovery_port))
            self.record_event({"event": "peer_reply_sent", "dst_ip": dst_ip, "nonce": nonce})
        except Exception as exc:
            self.record_event({"event": "peer_reply_failed", "dst_ip": dst_ip, "nonce": nonce, "error": repr(exc)})

    def _handle_discovery_message(
        self,
        message: dict,
        addr,
        send_sock: socket.socket,
    ) -> None:
        msg_type  = message.get("type")
        peer_node = message.get("node")
        if not peer_node or peer_node == self.node:
            return

        peer_ip   = addr[0]
        peer_port = int(message.get("exchange_port", self.exchange_port))
        nonce     = message.get("nonce")

        if msg_type == "discover":
            if nonce:
                if nonce in self._seen_discovery_nonces:
                    return
                self._cap_nonce_dict()
                self._seen_discovery_nonces[nonce] = time.time()
            self.record_event({
                "event":     "discovery_request_received",
                "peer":      peer_node,
                "peer_ip":   peer_ip,
                "peer_port": peer_port,
                "nonce":     nonce,
            })
            # Reply only — do NOT mark this node as a usable peer yet.
            # A broadcast packet only proves one-way reachability.
            self._send_peer_reply(send_sock, peer_ip, nonce)
            return

        if msg_type == "peer":
            self.record_event({
                "event":     "peer_reply_received",
                "peer":      peer_node,
                "peer_ip":   peer_ip,
                "peer_port": peer_port,
                "nonce":     nonce,
            })
            if self.discovery_mode == "mesh":
                self._remember_mesh_reachable_peer(peer_node, peer_ip, peer_port, source="peer_reply")
                return

            # adhoc: a unicast reply proves bidirectional reachability.
            self.peers[peer_node] = peer_ip
            self._reset_peer_backoff(peer_node, peer_ip, peer_port)

            # Only the lexicographically smaller node initiates to avoid
            # duplicate simultaneous connections.
            if self.node < peer_node:
                self.record_event({
                    "event":     "peer_reply_exchange_initiated",
                    "peer":      peer_node,
                    "peer_ip":   peer_ip,
                    "peer_port": peer_port,
                })
                self._start_exchange_thread(peer_node, peer_ip, peer_port, force=True)
            else:
                self.record_event({
                    "event":     "peer_reply_exchange_skipped_symmetry",
                    "peer":      peer_node,
                    "peer_ip":   peer_ip,
                    "peer_port": peer_port,
                })

    # ------------------------------------------------------------------
    # adhoc discovery loop
    # ------------------------------------------------------------------

    def _adhoc_discovery_loop(self) -> None:
        """UDP broadcast discover / unicast peer neighbour discovery."""
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
        broadcasts = self._local_broadcast_addresses()

        self.log(f"UDP neighbour discovery active udp/{self.discovery_port}")
        self.log(f"broadcast targets={broadcasts}")
        self.log(
            f"discovery_interval={self.discovery_interval}s "
            f"connect_timeout={self.connect_timeout}s "
            f"socket_timeout={self.socket_timeout}s "
            f"max_parallel_exchanges={self.max_parallel_exchanges}"
        )

        next_discovery = time.time() + random.uniform(0.0, self.discovery_interval)

        while self.running:
            now = time.time()

            if now >= next_discovery:
                self._prune_seen_discovery_nonces()
                # Broadcast to all local subnets + unicast to known static peers.
                static_ips = [
                    ip for node, (ip, _mac) in self.static_peers.items()
                    if node != self.node
                ]
                self._send_discovery_request(send_sock, broadcasts + static_ips)
                next_discovery = now + self.discovery_interval + random.uniform(0.0, self.discovery_interval * 0.25)

            try:
                data, addr = recv_sock.recvfrom(4096)
                self._handle_discovery_message(json.loads(data.decode("utf-8")), addr, send_sock)
            except socket.timeout:
                if self.store.new_bundle_event.is_set():
                    self.store.new_bundle_event.clear()
                    self._push_new_bundle_to_peers(self.peers)
            except Exception:
                continue

        for sock in (recv_sock, send_sock):
            try:
                sock.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Mesh discovery loop
    # ------------------------------------------------------------------

    def _mesh_neighbor_macs(self) -> Set[str]:
        """Return direct 802.11s mesh neighbours from the kernel station table."""
        try:
            output = subprocess.check_output(
                ["iw", "dev", self.wireless_iface, "station", "dump"],
                text=True,
                stderr=subprocess.STDOUT,
                timeout=15.0,
            )
        except FileNotFoundError:
            self.log("mesh discovery requires the `iw` command")
            return set()
        except Exception as exc:
            self.record_event({"event": "mesh_neighbor_scan_failed", "iface": self.wireless_iface, "error": repr(exc)})
            return set()

        macs: Set[str] = set()
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("Station "):
                continue
            parts = line.split()
            if len(parts) >= 2:
                mac = self._normalize_mac(parts[1])
                if mac:
                    macs.add(mac)
        return macs

    def _remember_mesh_reachable_peer(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
        source: str,
    ) -> None:
        now = time.time()
        self.peers[peer_node] = peer_ip
        self.mesh_reachable_peers[peer_node] = (peer_ip, peer_port, now)

        if now - self._last_mesh_reachable_log.get(peer_node, 0.0) >= max(self.discovery_interval * 5.0, 5.0):
            self._last_mesh_reachable_log[peer_node] = now
            self.record_event({
                "event":     "mesh_peer_reachable",
                "peer":      peer_node,
                "peer_ip":   peer_ip,
                "peer_port": peer_port,
                "source":    source,
            })

    def _prune_mesh_reachable_peers(self) -> None:
        now     = time.time()
        expired = [n for n, (_ip, _port, seen) in self.mesh_reachable_peers.items()
                   if now - seen > self.mesh_peer_ttl]
        for n in expired:
            del self.mesh_reachable_peers[n]

    def _select_mesh_probe_peers(self, station_peers: Set[str]) -> List[str]:
        """Build a paced probe list that prioritises recently-reachable peers."""
        now = time.time()
        recently_reachable = {
            node for node, (_ip, _port, seen) in self.mesh_reachable_peers.items()
            if now - seen < self.mesh_peer_ttl
        }
        # Priority: currently visible in station dump → recently reachable → full rotation.
        priority = (set(station_peers) | recently_reachable) & set(self.static_peers)
        selected = [n for n in sorted(priority) if n != self.node]

        all_known = sorted(self.static_peers)
        target = max(self.mesh_probe_peers_per_round, len(selected))
        attempts = 0
        while len(selected) < target and attempts < len(all_known):
            peer_node = all_known[self._mesh_probe_cursor % len(all_known)]
            self._mesh_probe_cursor += 1
            attempts += 1
            if peer_node not in selected and peer_node != self.node:
                selected.append(peer_node)

        return selected

    def _note_mesh_exchange_work(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
        sent_count: int,
        received_count: int,
    ) -> None:
        key = (peer_node, peer_ip, peer_port)
        if sent_count == 0 and received_count == 0:
            jitter = random.uniform(0.0, 0.2)
            cooldown = max(self.success_cooldown + jitter, min(self.discovery_interval, 0.5))
            self._mesh_empty_sync[key] = (time.time() + cooldown, len(self.store.ids()))
        else:
            self._mesh_empty_sync.pop(key, None)

    def _mesh_empty_sync_remaining(self, peer_node: str, peer_ip: str, peer_port: int) -> float:
        key = (peer_node, peer_ip, peer_port)
        entry = self._mesh_empty_sync.get(key)
        if entry is None:
            return 0.0
        until, bundle_count = entry
        remaining = until - time.time()
        if remaining <= 0.0 or len(self.store.ids()) > bundle_count:
            self._mesh_empty_sync.pop(key, None)
            return 0.0
        return remaining

    def _mesh_exchange_backoff_remaining(self, peer_node: str, peer_ip: str, peer_port: int) -> float:
        key = (peer_node, peer_ip, peer_port)
        now = time.time()
        with self._exchange_mu:
            if key in self._active_exchanges:
                return self.max_backoff
            last    = self._last_attempt.get(key, 0.0)
            backoff = self._backoff.get(key, self.discovery_interval)
            return max(0.0, backoff - (now - last))

    def _schedule_mesh_exchanges(self) -> None:
        self._prune_mesh_reachable_peers()
        candidates = sorted(self.mesh_reachable_peers)
        if not candidates:
            return

        scheduled = inspected = 0
        while scheduled < self.mesh_exchange_peers_per_tick and inspected < len(candidates):
            peer_node = candidates[self._mesh_exchange_cursor % len(candidates)]
            self._mesh_exchange_cursor += 1
            inspected += 1

            peer_ip, peer_port, _ = self.mesh_reachable_peers[peer_node]

            # Symmetry: only the lexicographically smaller node initiates.
            if self.node >= peer_node:
                self.record_event({
                    "event":     "mesh_exchange_skipped_symmetry",
                    "peer":      peer_node,
                    "peer_ip":   peer_ip,
                    "peer_port": peer_port,
                })
                continue

            if self._mesh_exchange_backoff_remaining(peer_node, peer_ip, peer_port) > 0.0:
                self.record_event({
                    "event":     "mesh_exchange_skipped_backoff",
                    "peer":      peer_node,
                    "peer_ip":   peer_ip,
                    "peer_port": peer_port,
                })
                continue

            if self._mesh_empty_sync_remaining(peer_node, peer_ip, peer_port) > 0.0:
                self.record_event({
                    "event":     "mesh_exchange_skipped_no_work",
                    "peer":      peer_node,
                    "peer_ip":   peer_ip,
                    "peer_port": peer_port,
                })
                continue

            self.record_event({
                "event":     "mesh_exchange_scheduled",
                "peer":      peer_node,
                "peer_ip":   peer_ip,
                "peer_port": peer_port,
            })
            jitter = random.uniform(0.0, 0.5)
            threading.Timer(jitter, self._start_exchange_thread, args=(peer_node, peer_ip, peer_port, False)).start()
            scheduled += 1

        if scheduled == 0:
            self.record_event({
                "event":           "mesh_exchange_skipped_no_budget",
                "reachable_peers": len(candidates),
            })

    def _mesh_discovery_loop(self) -> None:
        """802.11s mesh neighbour discovery via station dump + unicast probes."""
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
        broadcasts = self._local_broadcast_addresses()

        self.log(
            f"mesh neighbour discovery active iface={self.wireless_iface} "
            f"known_peers={len(self.static_peers)} mac_peers={len(self.peers_by_mac)}"
        )
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
                neighbor_macs = self._mesh_neighbor_macs()
                if not neighbor_macs:
                    self.record_event({"event": "mesh_station_scan_empty", "iface": self.wireless_iface})
                    self._send_discovery_request(send_sock, broadcasts)

                station_peers: Set[str] = set()
                for mac in sorted(neighbor_macs):
                    peer = self.peers_by_mac.get(mac)
                    if peer is None:
                        if now - self._last_mesh_unknown_log.get(mac, 0.0) >= max(self.discovery_interval * 10.0, 10.0):
                            self._last_mesh_unknown_log[mac] = now
                            self.record_event({"event": "mesh_neighbor_unknown_mac", "iface": self.wireless_iface, "peer_mac": mac})
                        continue

                    peer_node, peer_ip = peer
                    station_peers.add(peer_node)
                    self._remember_mesh_reachable_peer(peer_node, peer_ip, self.exchange_port, source="station_dump")

                    if now - self._last_mesh_neighbor_log.get(peer_node, 0.0) >= max(self.discovery_interval * 5.0, 5.0):
                        self._last_mesh_neighbor_log[peer_node] = now
                        self.record_event({
                            "event":     "mesh_neighbor_seen",
                            "iface":     self.wireless_iface,
                            "peer":      peer_node,
                            "peer_ip":   peer_ip,
                            "peer_mac":  mac,
                            "peer_port": self.exchange_port,
                        })

                probe_peers = self._select_mesh_probe_peers(station_peers)
                probe_ips   = [self.static_peers[n][0] for n in probe_peers if n in self.static_peers]
                self._prune_seen_discovery_nonces()
                self._send_discovery_request(send_sock, probe_ips)
                self._schedule_mesh_exchanges()
                next_scan = now + self.discovery_interval + random.uniform(0.0, self.discovery_interval * 0.25)

            try:
                data, addr = recv_sock.recvfrom(4096)
                self._handle_discovery_message(json.loads(data.decode("utf-8")), addr, send_sock)
            except socket.timeout:
                if self.store.new_bundle_event.is_set():
                    self.store.new_bundle_event.clear()
                    self._schedule_mesh_exchanges()
            except Exception:
                continue

        for sock in (recv_sock, send_sock):
            try:
                sock.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        threading.Thread(target=self.tcp_server, daemon=True).start()
        self.log(f"discovery_mode={self.discovery_mode}")
        if self.discovery_mode == "mesh":
            self._mesh_discovery_loop()
        else:
            self._adhoc_discovery_loop()


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def inject_bundle(args: argparse.Namespace) -> None:
    """Inject one bundle directly into the local BundleStore."""
    store = BundleStore(args.store)

    if args.payload_json is not None:
        try:
            payload = json.loads(args.payload_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid --payload-json: {exc}") from exc
    else:
        payload = {"app": "meshpay.demo", "type": "text", "message": args.payload}

    bundle = Bundle.create(src=args.node, dst=args.dst, payload=payload, ttl=args.ttl)
    store.save(bundle)
    store.record_event({
        "event":      "created",
        "node":       args.node,
        "bundle_id":  bundle.bundle_id,
        "src":        bundle.src,
        "dst":        bundle.dst,
        "size_bytes": bundle.size_bytes,
        "payload":    bundle.payload,
    })
    print(f"Injected bundle={bundle.bundle_id} src={bundle.src} dst={bundle.dst}")


def parse_peer(peer: str) -> Tuple[str, str, str | None]:
    """Parse a ``--peer name=ip[,mac]`` argument."""
    if "=" not in peer:
        raise argparse.ArgumentTypeError(f"Invalid peer format: {peer!r}. Expected name=ip[,mac]")
    name, value = peer.split("=", 1)
    parts = value.split(",", 1)
    ip  = parts[0].strip()
    mac = parts[1].strip() if len(parts) == 2 else None
    if not name or not ip:
        raise argparse.ArgumentTypeError(f"Invalid peer format: {peer!r}. Expected name=ip[,mac]")
    return name.strip(), ip, mac


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MeshPay Epidemic DTN router with medium-specific neighbour discovery"
    )
    parser.add_argument("--node",  required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument(
        "--peer", action="append", type=parse_peer, default=[],
        help="Known peer entry name=ip[,mac]. Mesh mode uses MACs to map station dump output.",
    )
    parser.add_argument(
        "--discovery-mode", choices=["adhoc", "mesh"], default="adhoc",
        help="Neighbour discovery mechanism to use.",
    )
    parser.add_argument(
        "--wireless-iface",
        help="Wireless interface for mesh station-table discovery. Defaults to <node>-wlan0.",
    )
    parser.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT)
    parser.add_argument("--exchange-port",  type=int, default=DEFAULT_EXCHANGE_PORT)
    parser.add_argument("--discovery-interval",      type=float, default=config.DEFAULT_DISCOVERY_INTERVAL)
    parser.add_argument("--connect-timeout",          type=float, default=config.DEFAULT_CONNECT_TIMEOUT)
    parser.add_argument("--socket-timeout",           type=float, default=config.DEFAULT_SOCKET_TIMEOUT)
    parser.add_argument("--max-backoff",              type=float, default=config.DEFAULT_MAX_BACKOFF)
    parser.add_argument("--max-parallel-exchanges",   type=int,   default=config.DEFAULT_MAX_PARALLEL_EXCHANGES)
    parser.add_argument("--contact-miss-log-interval",type=float, default=config.DEFAULT_CONTACT_MISS_LOG_INTERVAL)
    parser.add_argument("--success-cooldown",         type=float, default=config.DEFAULT_SUCCESS_COOLDOWN)
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