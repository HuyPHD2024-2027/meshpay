#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
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
    """Epidemic DTN router with UDP request/reply neighbour discovery.

    Peer selection policy:
        - No static peer loop.
        - No periodic hello exchange.
        - Periodically broadcast a UDP "discover" request.
        - Reachable neighbours answer with a UDP unicast "peer" reply.
        - A TCP bundle exchange is attempted only with peers that replied.

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
    ) -> None:
        self.node = node
        self.store = BundleStore(store_path)

        self.discovery_port = int(discovery_port)
        self.exchange_port = int(exchange_port)
        self.discovery_interval = float(discovery_interval)
        self.connect_timeout = float(connect_timeout)
        self.socket_timeout = float(socket_timeout)
        self.max_backoff = float(max_backoff)
        self.max_parallel_exchanges = int(max_parallel_exchanges)
        self.contact_miss_log_interval = float(contact_miss_log_interval)
        self.success_cooldown = float(success_cooldown)

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

    def send_message(self, writer, msg: dict, compress: bool = True) -> None:
        json_bytes = json.dumps(msg).encode("utf-8")
        if compress:
            compressed = zlib.compress(json_bytes)
            encoded = base64.b64encode(compressed).decode("ascii")
            writer.write(encoded + "\n")
        else:
            writer.write(json.dumps(msg) + "\n")

    def recv_message(self, reader) -> dict:
        line = reader.readline()
        if not line:
            return {}
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                return {}
        try:
            compressed = base64.b64decode(line.encode("ascii"))
            json_bytes = zlib.decompress(compressed)
            return json.loads(json_bytes.decode("utf-8"))
        except Exception as exc:
            self.log(f"error decompressing message: {exc!r}")
            try:
                return json.loads(line)
            except Exception:
                return {}

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
                order_id = bundle.payload.get("data", {}).get("order_id")

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

            request = self.recv_message(reader)

            if not request or request.get("type") != "summary":
                return

            peer_node = request["node"]
            peer_ids: Set[str] = set(request["ids"])

            self.log(f"contact from peer={peer_node} ip={addr[0]}")

            bundles_for_peer = self.store.unknown_to_peer(peer_ids)

            response = {
                "type": "summary",
                "node": self.node,
                "ids": list(self.store.ids()),
                "bundle_count": len(bundles_for_peer),
            }

            self.send_message(writer, response)

            for bundle in bundles_for_peer:
                self.send_message(writer, bundle.to_dict())

            writer.flush()

            final = self.recv_message(reader)

            if not final:
                return

            peer_bundle_count = int(final.get("bundle_count", 0))

            received_count = 0

            for _ in range(peer_bundle_count):
                bundle_data = self.recv_message(reader)

                if not bundle_data:
                    break

                try:
                    bundle = Bundle.from_dict(bundle_data)

                    if self.remember_bundle(bundle):
                        received_count += 1

                except Exception as exc:
                    self.log(f"error decoding incoming bundle: {exc!r}")
                    break

            self.record_event(
                {
                    "event": "incoming_exchange",
                    "peer": peer_node,
                    "peer_ip": addr[0],
                    "sent": len(bundles_for_peer),
                    "received": received_count,
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
    ) -> None:
        key = (peer_node, peer_ip, peer_port)

        with self._exchange_mu:
            self._active_exchanges.discard(key)

            current_backoff = self._backoff.get(key, self.discovery_interval)

            if success:
                self._backoff[key] = self.success_cooldown
            else:
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

            request = {
                "type": "summary",
                "node": self.node,
                "ids": list(self.store.ids()),
            }

            self.send_message(writer, request)
            writer.flush()

            response = self.recv_message(reader)

            if not response:
                return

            peer_ids = set(response["ids"])
            peer_bundle_count = int(response.get("bundle_count", 0))

            received_count = 0

            for _ in range(peer_bundle_count):
                bundle_data = self.recv_message(reader)

                if not bundle_data:
                    break

                try:
                    bundle = Bundle.from_dict(bundle_data)

                    if self.remember_bundle(bundle):
                        received_count += 1

                except Exception as exc:
                    self.log(f"error decoding incoming bundle from peer: {exc!r}")
                    break

            bundles_for_peer = self.store.unknown_to_peer(peer_ids)

            final = {
                "type": "bundles",
                "node": self.node,
                "bundle_count": len(bundles_for_peer),
            }

            self.send_message(writer, final)

            for bundle in bundles_for_peer:
                self.send_message(writer, bundle.to_dict())

            writer.flush()

            self.record_event(
                {
                    "event": "exchange",
                    "peer": peer_node,
                    "peer_ip": peer_ip,
                    "peer_port": peer_port,
                    "sent": len(bundles_for_peer),
                    "received": received_count,
                }
            )

            self.log(
                f"exchanged with peer={peer_node} ip={peer_ip} "
                f"sent={len(bundles_for_peer)} received={received_count}"
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

            # Remember the peer.
            self.peers[peer_node] = peer_ip

            # Important:
            # A unicast peer reply is a stronger signal than a broadcast
            # discover request, so we attempt exactly one exchange.
            # To break symmetry and avoid redundant concurrent connections,
            # only the lexicographically smaller node initiates the exchange.
            if self.node < peer_node:
                self.record_event(
                    {
                        "event": "peer_reply_exchange_initiated",
                        "peer": peer_node,
                        "peer_ip": peer_ip,
                        "peer_port": peer_port,
                    }
                )
                thread = threading.Thread(
                    target=self.exchange_with_peer,
                    args=(peer_node, peer_ip, peer_port),
                    kwargs={"force": True},
                    daemon=True,
                )
                thread.start()
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

    def discovery_loop(self) -> None:
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
        self.log("peer selection mode=udp discover/peer only")
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

                jitter = random.uniform(0.0, self.discovery_interval * 0.5)
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
        self.discovery_loop()


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


def parse_peer(peer: str) -> Tuple[str, str]:
    """Backward-compatible parser for ignored --peer arguments."""

    if "=" not in peer:
        raise argparse.ArgumentTypeError(
            f"Invalid peer format: {peer}. Expected name=ip, e.g. sta2=10.0.0.2"
        )

    name, ip = peer.split("=", 1)

    if not name or not ip:
        raise argparse.ArgumentTypeError(
            f"Invalid peer format: {peer}. Expected name=ip"
        )

    return name, ip


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MeshPay Epidemic DTN router with UDP neighbour discovery"
    )

    parser.add_argument("--node", required=True)
    parser.add_argument("--store", required=True)

    # Backward compatibility:
    # Existing runtime code may still pass --peer.
    # This implementation accepts but ignores static peers.
    parser.add_argument(
        "--peer",
        action="append",
        type=parse_peer,
        default=[],
        help="Ignored. UDP neighbour discovery is used instead.",
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

    if args.peer:
        print(
            f"[{args.node}] ignoring {len(args.peer)} static --peer entries; "
            "UDP neighbour discovery is used",
            flush=True,
        )

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
    )

    router.run()


if __name__ == "__main__":
    main()