#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Set, Tuple

from dtn.bundle import Bundle
from dtn.store import BundleStore


DEFAULT_DISCOVERY_PORT = 45555
DEFAULT_EXCHANGE_PORT = 46666


class EpidemicRouter:
    """Simple Epidemic Routing daemon.

    Protocol:
    1. Start one daemon per Mininet-WiFi station.
    2. Each daemon has a local persistent bundle store.
    3. Nodes exchange summary vectors with neighbours.
    4. A node sends bundles unknown to the peer.
    """

    def __init__(
        self,
        node: str,
        store_path: str | Path,
        peers: Dict[str, str] | None = None,
        discovery_port: int = DEFAULT_DISCOVERY_PORT,
        exchange_port: int = DEFAULT_EXCHANGE_PORT,
        discovery_interval: float = 2.0,
    ):
        self.node = node
        self.store = BundleStore(store_path)
        self.peers = peers or {}

        self.discovery_port = discovery_port
        self.exchange_port = exchange_port
        self.discovery_interval = discovery_interval

        self.running = True
        self.last_exchange: Dict[Tuple[str, str], float] = {}

    def log(self, message: str) -> None:
        line = {
            "time": time.time(),
            "node": self.node,
            "event": "router_log",
            "message": message,
        }

        print(f"[{self.node}] {message}", flush=True)
        self.store.record_event(line)

    def remember_bundle(self, bundle: Bundle) -> bool:
        if bundle.expired():
            return False

        if self.store.has(bundle.bundle_id):
            return False

        bundle.add_hop(self.node)
        self.store.save(bundle)

        self.store.record_event(
            {
                "event": "received",
                "node": self.node,
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

    def tcp_server(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", self.exchange_port))
        server.listen(32)

        self.log(f"exchange server listening tcp/{self.exchange_port}")

        while self.running:
            try:
                conn, addr = server.accept()
            except OSError:
                break

            thread = threading.Thread(
                target=self.handle_incoming_exchange,
                args=(conn, addr),
                daemon=True,
            )
            thread.start()

    def handle_incoming_exchange(self, conn: socket.socket, addr) -> None:
        try:
            reader = conn.makefile("r")
            writer = conn.makefile("w")

            line = reader.readline()
            if not line:
                return

            request = json.loads(line)
            if request.get("type") != "summary":
                return

            peer_node = request["node"]
            peer_ids: Set[str] = set(request["ids"])

            self.log(f"contact from peer={peer_node} ip={addr[0]}")

            bundles_for_peer = self.store.unknown_to_peer(peer_ids)

            response = {
                "type": "summary",
                "node": self.node,
                "ids": list(self.store.ids()),
                "bundles": [bundle.to_dict() for bundle in bundles_for_peer],
            }

            writer.write(json.dumps(response) + "\n")
            writer.flush()

            final_line = reader.readline()
            if not final_line:
                return

            final = json.loads(final_line)

            received_count = 0
            for raw_bundle in final.get("bundles", []):
                if self.remember_bundle(Bundle.from_dict(raw_bundle)):
                    received_count += 1

            self.store.record_event(
                {
                    "event": "incoming_exchange",
                    "node": self.node,
                    "peer": peer_node,
                    "peer_ip": addr[0],
                    "sent": len(bundles_for_peer),
                    "received": received_count,
                }
            )

        except Exception as exc:
            self.log(f"incoming exchange error: {exc}")

        finally:
            try:
                conn.close()
            except Exception:
                pass

    def exchange_with_peer(
        self,
        peer_node: str,
        peer_ip: str,
        peer_port: int,
    ) -> None:
        key = (peer_node, peer_ip)
        now = time.time()

        if now - self.last_exchange.get(key, 0.0) < self.discovery_interval:
            return

        self.last_exchange[key] = now

        try:
            conn = socket.create_connection((peer_ip, peer_port), timeout=2)

            reader = conn.makefile("r")
            writer = conn.makefile("w")

            request = {
                "type": "summary",
                "node": self.node,
                "ids": list(self.store.ids()),
            }

            writer.write(json.dumps(request) + "\n")
            writer.flush()

            response_line = reader.readline()
            if not response_line:
                return

            response = json.loads(response_line)
            peer_ids = set(response["ids"])

            received_count = 0
            for raw_bundle in response.get("bundles", []):
                if self.remember_bundle(Bundle.from_dict(raw_bundle)):
                    received_count += 1

            bundles_for_peer = self.store.unknown_to_peer(peer_ids)

            final = {
                "type": "bundles",
                "node": self.node,
                "bundles": [bundle.to_dict() for bundle in bundles_for_peer],
            }

            writer.write(json.dumps(final) + "\n")
            writer.flush()

            self.store.record_event(
                {
                    "event": "exchange",
                    "node": self.node,
                    "peer": peer_node,
                    "peer_ip": peer_ip,
                    "sent": len(bundles_for_peer),
                    "received": received_count,
                }
            )

            self.log(
                f"exchanged with peer={peer_node} ip={peer_ip} "
                f"sent={len(bundles_for_peer)} received={received_count}"
            )

        except Exception as exc:
            self.store.record_event(
                {
                    "event": "exchange_failed",
                    "node": self.node,
                    "peer": peer_node,
                    "peer_ip": peer_ip,
                    "error": str(exc),
                }
            )

        finally:
            try:
                conn.close()
            except Exception:
                pass

    def static_peer_loop(self) -> None:
        """Actively try known peers.

        This is the important part for Mininet-WiFi experiments.
        The topology script knows all station IPs, so we should not rely only
        on UDP broadcast discovery.
        """

        if self.peers:
            self.log(f"static peers configured: {self.peers}")

        while self.running:
            for peer_node, peer_ip in self.peers.items():
                if peer_node == self.node:
                    continue

                threading.Thread(
                    target=self.exchange_with_peer,
                    args=(peer_node, peer_ip, self.exchange_port),
                    daemon=True,
                ).start()

            time.sleep(self.discovery_interval)

    def local_broadcast_addresses(self) -> List[str]:
        """Return subnet broadcast addresses, e.g. 10.0.0.255.

        This is only a fallback discovery mechanism. Static peers are the main
        mechanism for the Mininet-WiFi example.
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
            self.log(f"could not detect broadcast addresses: {exc}")

        broadcasts.add("255.255.255.255")

        return sorted(broadcasts)

    def discovery_loop(self) -> None:
        """UDP discovery fallback.

        Static peers are more reliable in Mininet-WiFi, but this remains useful
        for debugging and future dynamic discovery.
        """

        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        recv_sock.bind(("0.0.0.0", self.discovery_port))
        recv_sock.settimeout(1.0)

        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        broadcasts = self.local_broadcast_addresses()

        self.log(f"discovery active udp/{self.discovery_port}")
        self.log(f"broadcast targets={broadcasts}")

        while self.running:
            hello = {
                "type": "hello",
                "node": self.node,
                "exchange_port": self.exchange_port,
                "time": time.time(),
            }

            for broadcast in broadcasts:
                try:
                    send_sock.sendto(
                        json.dumps(hello).encode("utf-8"),
                        (broadcast, self.discovery_port),
                    )
                except Exception as exc:
                    self.store.record_event(
                        {
                            "event": "broadcast_failed",
                            "node": self.node,
                            "broadcast": broadcast,
                            "error": str(exc),
                        }
                    )

            deadline = time.time() + self.discovery_interval

            while time.time() < deadline:
                try:
                    data, addr = recv_sock.recvfrom(4096)
                    message = json.loads(data.decode("utf-8"))

                    if message.get("type") != "hello":
                        continue

                    peer_node = message.get("node")

                    if not peer_node or peer_node == self.node:
                        continue

                    peer_ip = addr[0]
                    peer_port = int(message["exchange_port"])

                    threading.Thread(
                        target=self.exchange_with_peer,
                        args=(peer_node, peer_ip, peer_port),
                        daemon=True,
                    ).start()

                except socket.timeout:
                    break
                except Exception:
                    continue

    def run(self) -> None:
        threading.Thread(target=self.tcp_server, daemon=True).start()
        threading.Thread(target=self.static_peer_loop, daemon=True).start()
        self.discovery_loop()


def inject_bundle(args: argparse.Namespace) -> None:
    store = BundleStore(args.store)

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
        f"src={bundle.src} dst={bundle.dst} payload={args.payload}"
    )


def parse_peer(peer: str) -> Tuple[str, str]:
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
    parser = argparse.ArgumentParser(description="MeshPay Epidemic DTN router")

    parser.add_argument("--node", required=True)
    parser.add_argument("--store", required=True)

    parser.add_argument(
        "--peer",
        action="append",
        type=parse_peer,
        default=[],
        help="Known peer in name=ip format. Can be repeated.",
    )

    parser.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT)
    parser.add_argument("--exchange-port", type=int, default=DEFAULT_EXCHANGE_PORT)
    parser.add_argument("--discovery-interval", type=float, default=2.0)

    parser.add_argument("--inject", action="store_true")
    parser.add_argument("--dst")
    parser.add_argument("--payload")
    parser.add_argument("--ttl", type=float, default=300.0)

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.inject:
        if not args.dst or args.payload is None:
            raise SystemExit("--inject requires --dst and --payload")

        inject_bundle(args)
        return

    peers = dict(args.peer)

    router = EpidemicRouter(
        node=args.node,
        store_path=args.store,
        peers=peers,
        discovery_port=args.discovery_port,
        exchange_port=args.exchange_port,
        discovery_interval=args.discovery_interval,
    )

    router.run()


if __name__ == "__main__":
    main()