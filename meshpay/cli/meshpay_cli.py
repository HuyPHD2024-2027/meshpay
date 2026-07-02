#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
import shlex
import socket
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional
from uuid import UUID

from mininet.log import error, info
from mn_wifi.cli import CLI
from dtn import config as dtn_config
from meshpay.benchmark.payment_metrics import collect_payment_metrics
from meshpay.offline.virtual_accounts import account_host
from meshpay.mininet_cmd import safe_node_cmd, node_cmd_lock

from meshpay.offline.dtn_adapter import DTNAdapter
from meshpay.types.transaction import (
    ConfirmationOrder,
    SignedTransferOrder,
    TransferOrder,
)


class MeshPayRuntime:
    """Runtime controller for the interactive MeshPay offline demo.

    Responsibilities:
        - start/stop one Epidemic Routing daemon per node
        - inject MeshPay payment payloads into DTN through Unix control sockets
        - receive delivered payment payloads through a Unix delivery socket
        - call Client/Authority protocol handlers
        - inject outgoing response payloads
    """

    def __init__(
        self,
        net,
        clients: Iterable,
        authorities: Iterable,
        routing: str,
        router_file: str | Path,
        log_dir: str | Path,
        root_dir: str | Path,
        discovery_interval: float = dtn_config.DEFAULT_DISCOVERY_INTERVAL,
        payment_poll_interval: float = dtn_config.DEFAULT_PAYMENT_POLL_INTERVAL,
        medium: str = "mesh",
        bundle_ttl: float = 900.0,
    ) -> None:
        self.net = net
        self.clients = list(clients)
        self.authorities = list(authorities)
        self.nodes = self.clients + self.authorities

        self.routing = routing
        self.router_file = Path(router_file)
        self.log_dir = Path(log_dir)
        self.root_dir = Path(root_dir)

        if medium not in {"adhoc", "mesh"}:
            raise ValueError("medium must be one of: adhoc, mesh")

        self.discovery_interval = discovery_interval
        self.payment_poll_interval = payment_poll_interval
        self.medium = medium
        self.bundle_ttl = float(bundle_ttl)

        self.file_offsets: dict[str, int] = {}
        self.processes = []
        self.running = False
        self.payment_thread: Optional[threading.Thread] = None
        self.processed_lines: dict[str, int] = {}
        self.started_at: Optional[float] = None

        self.payment_log = self.log_dir / "payment.log"
        self._log_lock = threading.RLock()
        self._payment_events: list[dict] = []
        self._payment_log_flushed = 0
        # Keep payment.log out of the hot path by default, but make it
        # possible to get a live-ish log for long interactive runs without
        # restoring per-event writes. 0 means flush only on explicit calls
        # such as metrics/paymentlog/benchmark-finalization/stop.
        self._payment_log_flush_events = max(
            0,
            int(os.environ.get("MESHPAY_PAYMENT_LOG_FLUSH_EVENTS", "0")),
        )

        self.socket_dir = self._make_socket_dir()
        self.delivery_socket = self.socket_dir / "delivery.sock"
        self.delivery_thread: Optional[threading.Thread] = None
        self._delivery_server: Optional[socket.socket] = None
        self._node_by_name = {node.name: node for node in self.nodes}

        # Mininet node.cmd() is not thread-safe.  Use the shared per-node
        # command lock from meshpay.mininet_cmd so payment injection, attack
        # controller code, debug commands, and cleanup all serialize access to
        # the same Mininet node shell.
        self._node_cmd_locks: dict[str, threading.RLock] = {
            node.name: node_cmd_lock(node)
            for node in self.nodes
        }

    def start(self) -> None:
        self.started_at = time.time()
        self.running = True
        # Create payment.log immediately for compatibility with existing
        # CLI/benchmark tooling.  Events are still buffered and flushed in
        # batches, so this does not reintroduce hot-path file I/O.
        self.ensure_payment_log()
        self.start_delivery_listener()
        self.start_dtn_routers()
        time.sleep(2)

    def stop(self) -> None:
        self.running = False
        self.flush_payment_log()
        self.stop_delivery_listener()
        self.stop_dtn_routers()
        self.cleanup_ipc_sockets()

    def _make_socket_dir(self) -> Path:
        """Return a short filesystem path for Unix-domain IPC sockets.

        Linux limits AF_UNIX pathname sockets to about 108 bytes. Benchmark
        log directories are often deeply nested, for example under
        logs/benchmarks/scripts/<long-run-name>/..., so placing sockets inside
        log_dir can fail with ``OSError: AF_UNIX path too long``. Keep the
        socket directory under /tmp and use the long log_dir only for logs.
        """
        override = os.environ.get("MESHPAY_SOCKET_DIR") or os.environ.get("MESHPAY_IPC_DIR")
        if override:
            return Path(override)

        digest = hashlib.sha1(str(self.log_dir.resolve()).encode("utf-8")).hexdigest()[:10]
        return Path("/tmp") / f"meshpay-{os.getpid()}-{digest}"

    def prepare_ipc_sockets(self) -> None:
        self.socket_dir.mkdir(parents=True, exist_ok=True)
        for path in [self.delivery_socket, *[self.control_socket_for(node.name) for node in self.nodes]]:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def cleanup_ipc_sockets(self) -> None:
        for path in [self.delivery_socket, *[self.control_socket_for(node.name) for node in self.nodes]]:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
        try:
            self.socket_dir.rmdir()
        except OSError:
            pass

    def wireless_iface_for(self, node) -> str:
        wlans = getattr(node, "params", {}).get("wlan")

        if wlans:
            return str(wlans[0])

        wintfs = getattr(node, "wintfs", {})

        if isinstance(wintfs, dict):
            for intf in wintfs.values():
                name = getattr(intf, "name", "")
                if name:
                    return str(name)

        elif isinstance(wintfs, list):
            for intf in wintfs:
                name = getattr(intf, "name", "")
                if name:
                    return str(name)

        return f"{node.name}-wlan0"

    def node_ip(self, node) -> str:
        ip_method = getattr(node, "IP", None)

        if callable(ip_method):
            ip = str(ip_method()).strip()
            if ip:
                return ip.split("/", 1)[0]

        params = getattr(node, "params", {})
        ip = str(params.get("ip", "")).strip()

        if ip:
            return ip.split("/", 1)[0]

        raise ValueError(f"could not determine IP for node {node.name}")

    def clean_mac(self, mac: str) -> str:
        mac = mac.strip().lower()

        if len(mac) == 17 and mac.count(":") == 5:
            return mac

        return ""

    def node_mac(self, node) -> str:
        iface = self.wireless_iface_for(node)
        mac = self.clean_mac(self.node_cmd(node, f"cat /sys/class/net/{shlex.quote(iface)}/address").strip())

        if mac:
            return mac

        wintfs = getattr(node, "wintfs", {})

        if isinstance(wintfs, dict):
            for intf in wintfs.values():
                if getattr(intf, "name", None) == iface:
                    mac = self.clean_mac(str(getattr(intf, "mac", "")))

                    if mac:
                        return mac

        elif isinstance(wintfs, list):
            for intf in wintfs:
                if getattr(intf, "name", None) == iface:
                    mac = self.clean_mac(str(getattr(intf, "mac", "")))

                    if mac:
                        return mac

        return ""

    def peer_table(self) -> dict[str, tuple[str, str]]:
        table: dict[str, tuple[str, str]] = {}

        for node in self.nodes:
            table[node.name] = (self.node_ip(node), self.node_mac(node))

        return table

    def peer_args_for(self, node, peer_table: dict[str, tuple[str, str]]) -> str:
        args: list[str] = []

        for peer_name, (peer_ip, peer_mac) in sorted(peer_table.items()):
            if peer_name == node.name:
                continue

            value = f"{peer_name}={peer_ip}"

            if peer_mac:
                value = f"{value},{peer_mac}"

            args.append(f"--peer {shlex.quote(value)}")

        return " ".join(args)

    def node_cmd(self, node, cmd: str) -> str:
        """Run node.cmd() under a per-node lock.

        Mininet's node shell asserts when two threads use node.cmd() on the
        same node at the same time.  This helper is intentionally narrow: it
        protects shell-command based control actions such as one-shot DTN
        injection and debug log reads.
        """
        lock = self._node_cmd_locks.setdefault(node.name, node_cmd_lock(node))
        with lock:
            return safe_node_cmd(node, cmd)

    def dtn_env(self) -> dict[str, str]:
        """Environment inherited by DTN daemons.

        Benchmark hot-path delivery uses Unix sockets, so DTN event files and
        delivered.log are disabled by default.  Enable them only for debugging.
        """
        return {
            "PYTHONPATH": str(self.root_dir),
            "MESHPAY_PERSIST_BUNDLES": os.environ.get("MESHPAY_PERSIST_BUNDLES", "0"),
            "MESHPAY_SKIP_DELIVERY_RECEIPTS": os.environ.get("MESHPAY_SKIP_DELIVERY_RECEIPTS", "1"),
            "MESHPAY_DTN_EVENT_LOG": os.environ.get("MESHPAY_DTN_EVENT_LOG", "0"),
            "MESHPAY_DTN_DELIVERED_LOG": os.environ.get("MESHPAY_DTN_DELIVERED_LOG", "0"),
            "MESHPAY_DTN_EVENT_FILTER": os.environ.get("MESHPAY_DTN_EVENT_FILTER", "metrics"),
        }

    def shell_env_prefix(self, env: dict[str, str]) -> str:
        return " ".join(
            f"{key}={shlex.quote(str(value))}"
            for key, value in sorted(env.items())
            if value is not None
        )

    @staticmethod
    def bundle_id_from_inject_output(output: str) -> Optional[str]:
        marker = "Injected bundle="
        for line in output.splitlines():
            if marker not in line:
                continue
            tail = line.split(marker, 1)[1]
            return tail.split(None, 1)[0].strip() or None
        return None

    def start_dtn_routers(self) -> None:
        info(f"*** Starting MeshPay DTN routing: {self.routing}\n")
        info(f"*** DTN neighbour discovery mode: {self.medium}\n")

        peer_table = self.peer_table()

        for node in self.nodes:
            store = self.store_for(node.name)
            log_file = self.dtn_log_for(node.name)

            self.node_cmd(node, f"rm -rf {shlex.quote(str(store))}")
            self.node_cmd(node, f"mkdir -p {shlex.quote(str(store))}")

            wireless_iface = self.wireless_iface_for(node)
            peer_args = self.peer_args_for(node, peer_table)

            env_prefix = self.shell_env_prefix(self.dtn_env())

            cmd = (
                f"{env_prefix} "
                f"python3 {shlex.quote(str(self.router_file))} "
                f"--node {shlex.quote(node.name)} "
                f"--store {shlex.quote(str(store))} "
                f"--control-socket {shlex.quote(str(self.control_socket_for(node.name)))} "
                f"--delivery-socket {shlex.quote(str(self.delivery_socket))} "
                f"--discovery-mode {shlex.quote(self.medium)} "
                f"--wireless-iface {shlex.quote(wireless_iface)} "
                f"{peer_args} "
                f"--discovery-interval {self.discovery_interval} "
                f"--connect-timeout {dtn_config.DEFAULT_CONNECT_TIMEOUT} "
                f"--socket-timeout {dtn_config.DEFAULT_SOCKET_TIMEOUT} "
                f"--max-backoff {dtn_config.DEFAULT_MAX_BACKOFF} "
                f"--max-parallel-exchanges {dtn_config.DEFAULT_MAX_PARALLEL_EXCHANGES} "
                f"--contact-miss-log-interval {dtn_config.DEFAULT_CONTACT_MISS_LOG_INTERVAL} "
                f"--success-cooldown {dtn_config.DEFAULT_SUCCESS_COOLDOWN} "
                f"> {shlex.quote(str(log_file))} 2>&1 &"
            )

            proc = node.popen(cmd, shell=True)
            self.processes.append((node, proc))

    def stop_dtn_routers(self) -> None:
        info("*** Stopping MeshPay DTN daemons\n")

        for _node, proc in self.processes:
            try:
                proc.terminate()
            except Exception:
                pass

        router_patterns = [
            "dtn/epidemic.py",
            "epidemic.py",
            "dtn/spray_and_wait.py",
            "spray_and_wait.py",
            "dtn/prophet.py",
            "prophet.py",
        ]

        for node in self.nodes:
            for pattern in router_patterns:
                self.node_cmd(node, f"pkill -f {shlex.quote(pattern)} || true")

        self.processes = []

    def start_delivery_listener(self) -> None:
        """Start runtime delivery event listener.

        Routers connect to this Unix-domain socket whenever a bundle is
        delivered locally.  This replaces delivered.log polling in the payment
        hot path.
        """
        if self.delivery_thread is not None:
            return

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.prepare_ipc_sockets()

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.delivery_socket))
        server.listen(512)
        server.settimeout(1.0)
        self._delivery_server = server

        self.delivery_thread = threading.Thread(
            target=self._delivery_listener_loop,
            daemon=True,
        )
        self.delivery_thread.start()
        info(f"*** MeshPay IPC socket dir: {self.socket_dir}\n")
        info(f"*** MeshPay delivery socket listening: {self.delivery_socket}\n")

    def stop_delivery_listener(self) -> None:
        if self._delivery_server is not None:
            try:
                self._delivery_server.close()
            except Exception:
                pass
            self._delivery_server = None

        # Wake accept() if it is blocked.
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                s.connect(str(self.delivery_socket))
        except Exception:
            pass

        if self.delivery_thread is not None:
            self.delivery_thread.join(timeout=2.0)
            self.delivery_thread = None

        try:
            self.delivery_socket.unlink()
        except Exception:
            pass

    def start_payment_loop(self) -> None:
        """Backward-compatible alias; delivery now arrives through IPC."""
        self.start_delivery_listener()

    def stop_payment_loop(self) -> None:
        self.stop_delivery_listener()

    def _delivery_listener_loop(self) -> None:
        while self.running:
            server = self._delivery_server
            if server is None:
                return
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            except Exception as exc:
                self.record_event({"event": "delivery_listener_error", "error": f"{type(exc).__name__}: {exc!r}"})
                continue
            threading.Thread(target=self._handle_delivery_conn, args=(conn,), daemon=True).start()

    def _handle_delivery_conn(self, conn: socket.socket) -> None:
        try:
            with conn:
                f = conn.makefile("r")
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") != "delivered_bundle":
                        continue
                    node_name = str(event.get("node", ""))
                    node = self._node_by_name.get(node_name)
                    payload = event.get("payload")
                    if node is None or not isinstance(payload, dict):
                        continue
                    if payload.get("app") != "meshpay.offline":
                        continue
                    self.handle_payment_payload(node, payload)
        except Exception as exc:
            self.record_event({"event": "delivery_handler_error", "error": f"{type(exc).__name__}: {exc!r}"})

    def pay(self, src_name: str, dst_name: str, amount: int) -> None:
        """Interactive payment between physical station accounts.

        Example:
            pay sta1 sta3 10
        """

        self.pay_account(
            sender_account=src_name,
            recipient_account=dst_name,
            amount=amount,
        )

    def pay_account(
        self,
        sender_account: str,
        recipient_account: str,
        amount: int,
    ) -> None:
        """Create a payment between logical accounts.

        Example:
            sender_account    = "sta1/u00042"
            recipient_account = "sta7/u00088"

        Physical transport:
            source station      = sta1
            recipient station   = sta7
        """

        src_name = account_host(sender_account)
        recipient_host = account_host(recipient_account)

        src = self.net.get(src_name)

        if src not in self.clients:
            raise ValueError(f"{src_name} is not a MeshPay client station")

        order = src.pay(
            recipient=recipient_account,
            amount=amount,
            sender_account=sender_account,
        )

        payload = DTNAdapter.to_payload(order)

        self.record_event(
            {
                "event": "payment_created",
                "sender": sender_account,
                "recipient": recipient_account,
                "sender_host": src_name,
                "recipient_host": recipient_host,
                "amount": amount,
                "order_id": str(order.order_id),
                "sequence_number": order.sequence_number,
            }
        )

        for authority in self.authorities:
            self.inject_payload(
                src_name=src_name,
                dst_name=authority.name,
                payload=payload,
            )

    def inject_payload(self, src_name: str, dst_name: str, payload: dict) -> None:
        """Inject one MeshPay payload into the running source DTN daemon.

        This uses a Unix-domain control socket in the node's store directory.
        It avoids per-bundle node.cmd(), avoids one Python subprocess per
        bundle, and avoids inbox.jsonl file polling.
        """

        from dtn.bundle import Bundle

        src_node = self.net.get(src_name)
        payload = self.add_routing_hints(payload)
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        payload_size_bytes = len(payload_json.encode("utf-8"))

        bundle = Bundle.create(
            src=src_name,
            dst=dst_name,
            payload=payload,
            ttl=self.bundle_ttl,
        )
        bundle_id = bundle.bundle_id

        response = self._send_control_message(
            src_name,
            {"type": "inject", "bundle": bundle.to_dict()},
        )
        if not response or response.get("type") != "inject_ack":
            raise RuntimeError(f"invalid DTN inject response from {src_name}: {response!r}")
        if not response.get("stored") and response.get("error"):
            raise RuntimeError(f"DTN inject failed on {src_name}: {response.get('error')}")

        sender = None
        recipient = None
        amount = None
        try:
            obj = DTNAdapter.from_payload(
                payload,
                order_lookup=self.order_lookup_for_node(src_node),
            )
            if isinstance(obj, TransferOrder):
                sender = obj.sender
                recipient = obj.recipient
                amount = obj.amount
            elif hasattr(obj, "transfer_order"):
                sender = obj.transfer_order.sender
                recipient = obj.transfer_order.recipient
                amount = obj.transfer_order.amount
        except Exception:
            pass

        self.record_event(
            {
                "event": "payload_injected",
                "src": src_name,
                "dst": dst_name,
                "bundle_id": bundle_id,
                "payload_type": payload.get("type"),
                "payload_size_bytes": payload_size_bytes,
                "sender": sender,
                "recipient": recipient,
                "amount": amount,
                "injection_mode": "unix_control_socket",
            }
        )

    def _send_control_message(self, node_name: str, message: dict, retries: int = 5) -> dict:
        socket_path = self.control_socket_for(node_name)
        line = json.dumps(message, separators=(",", ":"), sort_keys=True) + "\n"
        last_error: Optional[Exception] = None

        for attempt in range(retries):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                    conn.settimeout(0.5)
                    conn.connect(str(socket_path))
                    conn.sendall(line.encode("utf-8"))
                    data = b""
                    while not data.endswith(b"\n"):
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                if not data:
                    return {}
                return json.loads(data.decode("utf-8"))
            except (FileNotFoundError, ConnectionRefusedError) as exc:
                last_error = exc
                time.sleep(min(0.05 * (attempt + 1), 0.5))
            except Exception as exc:
                last_error = exc
                break

        raise RuntimeError(
            f"could not inject into DTN daemon for {node_name} via {socket_path}: "
            f"{type(last_error).__name__}: {last_error!r}"
        )

    def add_routing_hints(self, payload: dict) -> dict:
        payload = dict(payload)
        if payload.get("app") != "meshpay.offline":
            return payload

        hints = dict(payload.get("_meshpay_route", {}))
        data = payload.get("data", {})
        if not isinstance(data, dict):
            data = {}

        ptype = payload.get("type")
        if ptype == "transfer_order":
            hints.setdefault("sender_host", account_host(data.get("sender") or data.get("s")))
            hints.setdefault("recipient_host", account_host(data.get("recipient") or data.get("r")))
            hints.setdefault("authority_targets", [node.name for node in self.authorities])
        elif ptype == "signed_transfer_order":
            raw_order_id = str(data.get("order_id") or data.get("i") or "")
            order_id = raw_order_id
            if raw_order_id:
                try:
                    order_id = str(UUID(raw_order_id))
                except Exception:
                    order_id = raw_order_id

            order = None
            if order_id:
                for node in self.nodes:
                    order = self.lookup_order(node, order_id)
                    if order is not None:
                        break
            if order is not None:
                hints.setdefault("sender_host", account_host(order.sender))
                hints.setdefault("recipient_host", account_host(order.recipient))
        elif ptype == "confirmation_order":
            hints.setdefault("sender_host", account_host(data.get("sender") or data.get("s")))
            hints.setdefault("recipient_host", account_host(data.get("recipient") or data.get("r")))
            hints.setdefault("authority_targets", [node.name for node in self.authorities])

        payload["_meshpay_route"] = {key: value for key, value in hints.items() if value}
        return payload

    def store_for(self, node_name: str) -> Path:
        return self.log_dir / "stores" / self.routing / node_name

    def control_socket_for(self, node_name: str) -> Path:
        return self.socket_dir / f"{node_name}.sock"

    def dtn_log_for(self, node_name: str) -> Path:
        return self.log_dir / f"{node_name}-{self.routing}.log"

    def delivered_log_for(self, node_name: str) -> Path:
        return self.store_for(node_name) / "delivered.log"

    def _payment_loop(self) -> None:
        while self.running:
            for node in self.nodes:
                try:
                    self.process_delivered_for_node(node)
                except Exception as exc:
                    self.record_event(
                        {
                            "event": "payment_loop_error",
                            "node": node.name,
                            "error": f"{type(exc).__name__}: {exc!r}",
                        }
                    )

            time.sleep(self.payment_poll_interval)

    def process_delivered_for_node(self, node) -> None:
        """Process only newly appended delivered.log lines.

        This avoids repeatedly reading the entire file, which becomes expensive
        when thousands of transactions are delivered.
        """

        delivered_log = self.delivered_log_for(node.name)

        if not delivered_log.exists():
            return

        offset_key = f"{node.name}:delivered"
        offset = self.file_offsets.get(offset_key, 0)

        with delivered_log.open("r", encoding="utf-8") as f:
            f.seek(offset)
            new_lines = f.readlines()
            self.file_offsets[offset_key] = f.tell()

        for line in new_lines:
            line = line.strip()

            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            payload = record.get("payload")

            if not isinstance(payload, dict):
                continue

            if payload.get("app") != "meshpay.offline":
                continue

            self.handle_payment_payload(node, payload)
        
    def order_lookup_for_node(self, node):
        return lambda order_id: self.lookup_order(node, order_id)

    def lookup_order(self, node, order_id: str):
        order_id = str(order_id)

        pending_transfers = getattr(node, "pending_transfers", None)
        if isinstance(pending_transfers, dict):
            order = pending_transfers.get(order_id)
            if order is not None:
                return order

        confirmation_orders = getattr(node, "confirmation_orders", None)
        if isinstance(confirmation_orders, dict):
            confirmation = confirmation_orders.get(order_id)
            if confirmation is not None:
                return confirmation.transfer_order

        signed_transfer_orders = getattr(node, "signed_transfer_orders", None)
        if isinstance(signed_transfer_orders, dict):
            signatures_for_order = signed_transfer_orders.get(order_id)
            if isinstance(signatures_for_order, dict):
                for signed in signatures_for_order.values():
                    return signed.transfer_order

        state = getattr(node, "state", None)
        accounts = getattr(state, "accounts", None)
        if isinstance(accounts, dict):
            for account in accounts.values():
                pending = getattr(account, "pending_confirmation", None)
                if pending is not None and str(pending.order_id) == order_id:
                    return pending.transfer_order

                confirmed = getattr(account, "confirmed_transfers", None)
                if isinstance(confirmed, dict):
                    confirmation = confirmed.get(order_id)
                    if confirmation is not None:
                        return confirmation.transfer_order

        return None

    def handle_payment_payload(self, node, payload: dict) -> None:
        try:
            obj = DTNAdapter.from_payload(
                payload,
                order_lookup=self.order_lookup_for_node(node),
            )
        except ValueError:
            return

        payload_size_bytes = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
        order_id = self.object_order_id(obj)

        sender = None
        recipient = None
        amount = None
        if isinstance(obj, TransferOrder):
            sender = obj.sender
            recipient = obj.recipient
            amount = obj.amount
        elif hasattr(obj, "transfer_order"):
            sender = obj.transfer_order.sender
            recipient = obj.transfer_order.recipient
            amount = obj.transfer_order.amount

        self.record_event(
            {
                "event": "payment_payload_delivered",
                "node": node.name,
                "payload_type": payload.get("type"),
                "order_id": order_id,
                "payload_size_bytes": payload_size_bytes,
                "sender": sender,
                "recipient": recipient,
                "amount": amount,
            }
        )

        accepted_before = False

        if isinstance(obj, ConfirmationOrder):
            accepted_before = order_id in getattr(node, "confirmation_orders", {})

        outgoing_objects = node.on_payment_object(obj)

        if isinstance(obj, ConfirmationOrder):
            accepted_after = order_id in getattr(node, "confirmation_orders", {})

            if (
                obj.transfer_order.recipient in getattr(node, "accounts", {})
                and not accepted_before
                and accepted_after
            ):
                self.record_event(
                    {
                        "event": "payment_accepted",
                        "node": node.name,
                        "recipient_account": obj.transfer_order.recipient,
                        "order_id": order_id,
                        "sender": obj.transfer_order.sender,
                        "recipient": obj.transfer_order.recipient,
                        "amount": obj.transfer_order.amount,
                    }
                )

        if outgoing_objects:
            threading.Thread(
                target=self._route_outgoing_objects,
                args=(node, outgoing_objects),
                daemon=True,
            ).start()

    def _route_outgoing_objects(self, src_node, objects) -> None:
        for out_obj in objects:
            try:
                self.route_outgoing_object(src_node=src_node, obj=out_obj)
            except Exception as e:
                self.record_event(
                    {
                        "event": "async_routing_error",
                        "node": src_node.name,
                        "error": str(e),
                    }
                )

    def route_outgoing_object(self, src_node, obj) -> None:
        payload = DTNAdapter.to_payload(obj)

        if isinstance(obj, SignedTransferOrder):
            order = obj.transfer_order
            dst = account_host(order.sender)

            self.record_event(
                {
                    "event": "authority_signed_transfer",
                    "authority": src_node.name,
                    "sender": order.sender,
                    "recipient": order.recipient,
                    "amount": order.amount,
                    "sender_host": dst,
                    "order_id": str(obj.order_id),
                }
            )

            self.inject_payload(
                src_name=src_node.name,
                dst_name=dst,
                payload=payload,
            )
            return

        if isinstance(obj, ConfirmationOrder):
            order = obj.transfer_order
            recipient_host = account_host(order.recipient)

            self.record_event(
                {
                    "event": "confirmation_created",
                    "sender": order.sender,
                    "recipient": order.recipient,
                    "amount": order.amount,
                    "sender_host": account_host(order.sender),
                    "recipient_host": recipient_host,
                    "order_id": str(order.order_id),
                    "signatures": len(obj.authority_signatures),
                }
            )

            # Collect unique destinations, skipping the source node
            # (it already has the confirmation in its store).
            destinations: set[str] = set()

            # Recipient's physical host needs the confirmation for acceptance.
            destinations.add(recipient_host)

            # Authorities need confirmations to update account state.
            for authority in self.authorities:
                destinations.add(authority.name)

            # Don't inject back to the node that created the confirmation —
            # it already has the bundle in its store.
            destinations.discard(src_node.name)

            for dst_name in destinations:
                self.inject_payload(
                    src_name=src_node.name,
                    dst_name=dst_name,
                    payload=payload,
                )

            return

    def ensure_payment_log(self) -> None:
        """Create the compatibility payment.log file if it does not exist.

        The socket-IPC implementation keeps payment events in memory and flushes
        them explicitly.  Some scripts and interactive users nevertheless expect
        the file path to exist as soon as the runtime starts.
        """
        self.payment_log.parent.mkdir(parents=True, exist_ok=True)
        self.payment_log.touch(exist_ok=True)

    def record_event(self, event: dict) -> None:
        """Record a payment event in memory.

        This keeps payment.log writes out of the per-payment hot path. Existing
        metrics tooling is preserved by flushing before metrics/paymentlog and at
        benchmark finalization. Set MESHPAY_PAYMENT_LOG_FLUSH_EVENTS=N to flush
        every N buffered events during long interactive/debug runs.
        """
        event = dict(event)
        event.setdefault("time", time.time())

        with self._log_lock:
            self._payment_events.append(event)
            if (
                self._payment_log_flush_events > 0
                and len(self._payment_events) - self._payment_log_flushed
                >= self._payment_log_flush_events
            ):
                self._flush_payment_log_locked()

    def _flush_payment_log_locked(self) -> None:
        pending = self._payment_events[self._payment_log_flushed :]
        if not pending:
            self.ensure_payment_log()
            return

        self.payment_log.parent.mkdir(parents=True, exist_ok=True)
        with self.payment_log.open("a", encoding="utf-8") as f:
            for event in pending:
                f.write(json.dumps(event, sort_keys=True) + "\n")
        self._payment_log_flushed = len(self._payment_events)

    def flush_payment_log(self) -> None:
        with self._log_lock:
            self._flush_payment_log_locked()

    @staticmethod
    def object_order_id(obj) -> Optional[str]:
        if hasattr(obj, "order_id"):
            return str(obj.order_id)

        if hasattr(obj, "transfer_order"):
            return str(obj.transfer_order.order_id)

        return None


class MeshPayCLI(CLI):
    """Interactive CLI for MeshPay offline payment demos.

    Supported commands:
        pay sta1 sta3 10
        sta1 pay sta3 10

        balance
        balance sta1

        payments
        payments sta1

        metrics

        paymentlog
        paymentlog sta1

        dtnlog
        dtnlog sta1

        delivered
        delivered sta3
    """

    def __init__(self, mininet, runtime: MeshPayRuntime, *args, **kwargs):
        self.runtime = runtime
        super().__init__(mininet, *args, **kwargs)

    def default(self, line: str):
        try:
            args = shlex.split(line)
        except ValueError as exc:
            error(f"*** Parse error: {exc}\n")
            return

        # Support:
        #   sta1 pay sta3 10
        if len(args) == 4 and args[1] == "pay":
            return self._pay(args[0], args[2], args[3])

        return super().default(line)
    def do_vpay(self, line: str) -> None:
        """Create an offline payment between virtual accounts.

        Usage:
            vpay sta1/u00001 sta3/u00001 10
        """

        try:
            args = shlex.split(line)
        except ValueError as exc:
            error(f"*** Parse error: {exc}\n")
            return

        if len(args) != 3:
            error("*** Usage: vpay <sender_account> <recipient_account> <amount>\n")
            error("*** Example: vpay sta1/u00001 sta3/u00001 10\n")
            return

        sender_account = args[0]
        recipient_account = args[1]

        try:
            amount = int(args[2])
        except ValueError:
            error("*** amount must be an integer\n")
            return

        try:
            self.runtime.pay_account(
                sender_account=sender_account,
                recipient_account=recipient_account,
                amount=amount,
            )
        except Exception as exc:
            error(f"*** Virtual payment failed: {type(exc).__name__}: {exc!r}\n")

    def do_pay(self, line: str) -> None:
        """Create an offline payment.

        Usage:
            pay sta1 sta3 10
        """

        try:
            args = shlex.split(line)
        except ValueError as exc:
            error(f"*** Parse error: {exc}\n")
            return

        if len(args) != 3:
            error("*** Usage: pay <sender> <recipient> <amount>\n")
            return

        self._pay(args[0], args[1], args[2])

    def _pay(self, src: str, dst: str, amount_text: str) -> None:
        try:
            amount = int(amount_text)
        except ValueError:
            error("*** amount must be an integer\n")
            return

        try:
            self.runtime.pay(src, dst, amount)
        except Exception as exc:
            error(f"*** Payment failed: {type(exc).__name__}: {exc!r}\n")

    def do_accounts(self, line: str) -> None:
        """Show virtual accounts hosted by client stations.

        Usage:
            accounts
            accounts sta1
        """

        try:
            args = shlex.split(line)
        except ValueError as exc:
            error(f"*** Parse error: {exc}\n")
            return

        if args:
            node_names = args
        else:
            node_names = [client.name for client in self.runtime.clients]

        for node_name in node_names:
            if node_name not in self.mn:
                error(f"*** Unknown node: {node_name}\n")
                continue

            node = self.mn.get(node_name)

            if not hasattr(node, "hosted_accounts"):
                error(f"*** Node {node_name} does not expose hosted_accounts()\n")
                continue

            info(f"\n===== accounts hosted by {node_name} =====\n")

            accounts = node.hosted_accounts(virtual_only=False)

            for account_id in accounts[:50]:
                balance = node.account_balance(account_id)
                info(f"{account_id}: balance={balance}\n")

            if len(accounts) > 50:
                info(f"... {len(accounts) - 50} more accounts hidden\n")

    def do_balance(self, line: str) -> None:
        """Show client balance and authority views.

        Usage:
            balance
            balance sta1
        """

        args = shlex.split(line)

        if args:
            node_names = args
        else:
            node_names = [node.name for node in self.runtime.clients]

        for node_name in node_names:
            if node_name not in self.mn:
                error(f"*** Unknown node: {node_name}\n")
                continue

            node = self.mn.get(node_name)

            info(f"\n===== balance {node_name} =====\n")

            if hasattr(node, "balance"):
                info(f"client_local_balance={node.balance}\n")

            for authority in self.runtime.authorities:
                if hasattr(authority, "balance_of"):
                    info(
                        f"{authority.name}_view="
                        f"{authority.balance_of(node_name)}\n"
                    )

    def do_payments(self, line: str) -> None:
        """Show confirmation orders known by clients.

        Usage:
            payments
            payments sta1
        """

        args = shlex.split(line)

        if args:
            nodes = [self.mn.get(name) for name in args if name in self.mn]
        else:
            nodes = self.runtime.clients

        for node in nodes:
            info(f"\n===== payments {node.name} =====\n")

            confirmations = getattr(node, "confirmation_orders", {})

            if not confirmations:
                info("No confirmation orders\n")
                continue

            for order_id, confirmation in confirmations.items():
                order = confirmation.transfer_order
                info(
                    f"order_id={order_id} "
                    f"sender={order.sender} "
                    f"recipient={order.recipient} "
                    f"amount={order.amount} "
                    f"status={confirmation.status}\n"
                )

    def do_paymentlog(self, line: str) -> None:
        """Show MeshPay payment log.

        Usage:
            paymentlog
            paymentlog 50
        """

        args = shlex.split(line)

        lines = 50
        if args:
            try:
                lines = int(args[0])
            except ValueError:
                error("*** Usage: paymentlog [lines]\n")
                return

        # Payment events are buffered in memory to keep the payment hot path
        # fast.  The interactive command must flush before reading the
        # compatibility payment.log file, otherwise it shows a stale snapshot.
        self.runtime.flush_payment_log()

        path = self.runtime.payment_log

        info(f"\n===== payment log: {path} =====\n")

        if not path.exists():
            info("No payment log\n")
            return

        content = path.read_text(encoding="utf-8").splitlines()

        for line in content[-lines:]:
            info(line + "\n")

    def do_metrics(self, _line: str) -> None:
        """Show MeshPay payment metrics including time to quorum.

        Usage:
            metrics
        """

        # Payment events are buffered in memory during normal operation.
        # Flush before using the existing file-based metrics collector so the
        # interactive metrics command reflects all events already processed by
        # the runtime and delivery socket.
        self.runtime.flush_payment_log()

        started_at = self.runtime.started_at or time.time()
        report = collect_payment_metrics(
            log_dir=self.runtime.log_dir,
            started_at=started_at,
            ended_at=time.time(),
        )

        summary = report["summary"]
        quorum = report["latency_ms"]["time_to_quorum"]
        accepted = report["latency_ms"]["time_to_acceptance"]

        info(f"\n===== MeshPay metrics: {self.runtime.payment_log} =====\n")
        info(f"payments_created:              {summary['payments_created']}\n")
        info(f"payments_confirmed:            {summary['payments_confirmed']}\n")
        info(f"payments_unconfirmed:          {summary['payments_unconfirmed']}\n")
        info(f"payments_accepted:             {summary['payments_accepted']}\n")
        info(f"payments_unaccepted:           {summary['payments_unaccepted']}\n")
        info(
            f"payment_confirmation_rate_pct: {summary['payment_confirmation_rate_percent']:.2f}\n"
        )
        info(
            f"payment_acceptance_rate_pct:   {summary['payment_acceptance_rate_percent']:.2f}\n"
        )

        if quorum["avg"] is None:
            info("time_to_quorum_ms: None\n")
        else:
            info(f"avg_time_to_quorum_ms: {quorum['avg']:.4f}\n")
            info(f"p50_time_to_quorum_ms: {quorum['p50']:.4f}\n")
            info(f"p95_time_to_quorum_ms: {quorum['p95']:.4f}\n")
            info(f"min_time_to_quorum_ms: {quorum['min']:.4f}\n")
            info(f"max_time_to_quorum_ms: {quorum['max']:.4f}\n")

        if accepted["avg"] is None:
            info("time_to_acceptance_ms: None\n")
        else:
            info(f"avg_time_to_acceptance_ms: {accepted['avg']:.4f}\n")
            info(f"p50_time_to_acceptance_ms: {accepted['p50']:.4f}\n")
            info(f"p95_time_to_acceptance_ms: {accepted['p95']:.4f}\n")

    def do_dtnlog(self, line: str) -> None:
        """Show DTN daemon logs.

        Usage:
            dtnlog
            dtnlog sta1
        """

        args = shlex.split(line)

        if args:
            node_names = args
        else:
            node_names = [node.name for node in self.runtime.nodes]

        for node_name in node_names:
            if node_name not in self.mn:
                error(f"*** Unknown node: {node_name}\n")
                continue

            log_file = self.runtime.dtn_log_for(node_name)

            info(f"\n===== {node_name} DTN log =====\n")

            output = self.runtime.node_cmd(
                self.mn.get(node_name),
                f"test -f {shlex.quote(str(log_file))} "
                f"&& tail -n 40 {shlex.quote(str(log_file))} "
                f"|| true"
            )

            if output.strip():
                info(output)
            else:
                info("No daemon log\n")

    def do_delivered(self, line: str) -> None:
        """Show delivered DTN bundles.

        Usage:
            delivered
            delivered sta3
        """

        args = shlex.split(line)

        if args:
            node_names = args
        else:
            node_names = [node.name for node in self.runtime.nodes]

        for node_name in node_names:
            if node_name not in self.mn:
                error(f"*** Unknown node: {node_name}\n")
                continue

            delivered_log = self.runtime.delivered_log_for(node_name)

            info(f"\n===== {node_name} delivered.log =====\n")

            output = self.runtime.node_cmd(
                self.mn.get(node_name),
                f"test -f {shlex.quote(str(delivered_log))} "
                f"&& tail -n 40 {shlex.quote(str(delivered_log))} "
                f"|| true"
            )

            if output.strip():
                info(output)
            else:
                info("No delivered bundles\n")

    def do_meshpay(self, _line: str) -> None:
        """Show MeshPay demo commands."""

        info("\nMeshPay commands:\n")
        info("  pay sta1 sta3 10\n")
        info("  sta1 pay sta3 10\n")
        info("  balance\n")
        info("  balance sta1\n")
        info("  payments\n")
        info("  payments sta1\n")
        info("  metrics\n")
        info("  paymentlog\n")
        info("  dtnlog\n")
        info("  dtnlog sta1\n")
        info("  delivered\n")
        info("  delivered sta3\n\n")