#!/usr/bin/env python3

from __future__ import annotations
from chardet import langbulgarianmodel
from IPython.core import payload

import json
import shlex
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
        - inject MeshPay payment payloads into DTN
        - poll delivered.log for payment payloads
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
        self._log_lock = threading.Lock()

    def start(self) -> None:
        self.started_at = time.time()
        self.start_dtn_routers()
        time.sleep(2)
        self.start_payment_loop()

    def stop(self) -> None:
        self.stop_payment_loop()
        self.stop_dtn_routers()

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
        mac = self.clean_mac(node.cmd(f"cat /sys/class/net/{shlex.quote(iface)}/address").strip())

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

    def start_dtn_routers(self) -> None:
        info(f"*** Starting MeshPay DTN routing: {self.routing}\n")
        info(f"*** DTN neighbour discovery mode: {self.medium}\n")

        peer_table = self.peer_table()

        for node in self.nodes:
            store = self.store_for(node.name)
            log_file = self.dtn_log_for(node.name)

            node.cmd(f"rm -rf {shlex.quote(str(store))}")
            node.cmd(f"mkdir -p {shlex.quote(str(store))}")

            wireless_iface = self.wireless_iface_for(node)
            peer_args = self.peer_args_for(node, peer_table)

            cmd = (
                f"PYTHONPATH={shlex.quote(str(self.root_dir))} "
                f"python3 {shlex.quote(str(self.router_file))} "
                f"--node {shlex.quote(node.name)} "
                f"--store {shlex.quote(str(store))} "
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

            info(f"*** {node.name}: {self.routing} daemon started\n")
            info(f"***     store={store}\n")
            info(f"***     log={log_file}\n")
            info(f"***     discovery={self.medium} iface={wireless_iface} peers={len(peer_table) - 1}\n")

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
                node.cmd(f"pkill -f {shlex.quote(pattern)} || true")

        self.processes = []

    def start_payment_loop(self) -> None:
        if self.payment_thread is not None:
            return

        self.running = True
        self.payment_thread = threading.Thread(
            target=self._payment_loop,
            daemon=True,
        )
        self.payment_thread.start()

        info("*** MeshPay payment loop started\n")

    def stop_payment_loop(self) -> None:
        self.running = False

        if self.payment_thread is not None:
            self.payment_thread.join(timeout=2.0)
            self.payment_thread = None

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
        """Inject one MeshPay payload directly into the source DTN store.

        Writes the bundle JSON file atomically (tmp + os.replace) directly
        into the store directory. The running router daemon discovers new
        files via filesystem mtime checks — no BundleStore instance needed.

        This avoids the overhead of instantiating a full BundleStore (cache
        rebuild, confirmed-order scan, etc.) on every injection call, which
        was a major bottleneck at high TPS.
        """

        import os as _os

        from dtn.bundle import Bundle

        store_path = self.store_for(src_name)
        store_path.mkdir(parents=True, exist_ok=True)

        payload = self.add_routing_hints(payload)

        bundle = Bundle.create(
            src=src_name,
            dst=dst_name,
            payload=payload,
            ttl=self.bundle_ttl,
        )

        # Write bundle JSON atomically — the router daemon will pick it up
        # via _refresh_cache_if_needed_unlocked() on the next store.all().
        bundle_data = bundle.to_dict()
        bundle_file = store_path / f"{bundle.bundle_id}.json"
        tmp_file = store_path / f"{bundle.bundle_id}.json.tmp"

        with tmp_file.open("w", encoding="utf-8") as f:
            json.dump(bundle_data, f, sort_keys=True)
            f.write("\n")

        _os.replace(tmp_file, bundle_file)

        payload_json = json.dumps(payload, sort_keys=True)
        payload_size_bytes = len(payload_json.encode("utf-8"))

        # Append creation event directly to the store's events.jsonl.
        events_log = store_path / "events.jsonl"
        event_record = json.dumps(
            {
                "event": "created",
                "node": src_name,
                "bundle_id": bundle.bundle_id,
                "src": src_name,
                "dst": dst_name,
                "size_bytes": bundle.size_bytes,
                "payload": payload,
                "time": time.time(),
            },
            sort_keys=True,
        )
        with events_log.open("a", encoding="utf-8") as f:
            f.write(event_record + "\n")

        sender = None
        recipient = None
        amount = None
        try:
            source_node = self.net.get(src_name)
            obj = DTNAdapter.from_payload(
                payload,
                order_lookup=self.order_lookup_for_node(source_node),
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
                "bundle_id": bundle.bundle_id,
                "payload_type": payload.get("type"),
                "payload_size_bytes": payload_size_bytes,
                "sender": sender,
                "recipient": recipient,
                "amount": amount,
            }
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
                            "error": str(exc),
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

        for out_obj in outgoing_objects:
            self.route_outgoing_object(src_node=node, obj=out_obj)
             
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

    def record_event(self, event: dict) -> None:
        event = dict(event)
        event.setdefault("time", time.time())

        with self._log_lock:
            self.payment_log.parent.mkdir(parents=True, exist_ok=True)
            with self.payment_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, sort_keys=True) + "\n")

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
            error(f"*** Virtual payment failed: {exc}\n")

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
            error(f"*** Payment failed: {exc}\n")

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
        info(f"payments_accepted:             {summary['payments_accepted']}\n")
        info(
            f"payment_confirmation_rate_pct: {summary['payment_confirmation_rate_percent']:.2f}\n"
        )
        info(
            f"payment_acceptance_rate_pct:   {summary['payment_acceptance_rate_percent']:.2f}\n"
        )

        if quorum["avg"] is None:
            info("time_to_quorum_ms:             None\n")
        else:
            info(f"avg_time_to_quorum_ms:         {quorum['avg']:.4f}\n")
            info(f"p50_time_to_quorum_ms:         {quorum['p50']:.4f}\n")
            info(f"p95_time_to_quorum_ms:         {quorum['p95']:.4f}\n")
            info(f"min_time_to_quorum_ms:         {quorum['min']:.4f}\n")
            info(f"max_time_to_quorum_ms:         {quorum['max']:.4f}\n")

        if accepted["avg"] is None:
            info("time_to_acceptance_ms:         None\n")
        else:
            info(f"avg_time_to_acceptance_ms:     {accepted['avg']:.4f}\n")
            info(f"p50_time_to_acceptance_ms:     {accepted['p50']:.4f}\n")
            info(f"p95_time_to_acceptance_ms:     {accepted['p95']:.4f}\n")

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

            output = self.mn.get(node_name).cmd(
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

            output = self.mn.get(node_name).cmd(
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