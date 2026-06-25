#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) in sys.path:
    sys.path.remove(str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR))

examples_dir = str(ROOT_DIR / "examples")
if examples_dir in sys.path:
    sys.path.remove(examples_dir)


from mininet.log import info, setLogLevel
from mn_wifi.link import adhoc, mesh, wmediumd
from mn_wifi.net import Mininet_wifi
from mn_wifi.wmediumdConnector import interference

from attacks.controller import BenchmarkAttack
from meshpay.benchmark.payment_metrics import collect_payment_metrics
from meshpay.benchmark.report import write_reports
from meshpay.cli.meshpay_cli import MeshPayRuntime
from meshpay.offline.nodes.authority import Authority
from meshpay.offline.nodes.client import Client
from meshpay.offline.virtual_accounts import make_account_id
from dtn import config as dtn_config

DEFAULT_LOG_DIR = ROOT_DIR / "logs" / "benchmarks" / "meshpay_offline"

ROUTER_FILES = {
    "epidemic": ROOT_DIR / "dtn" / "epidemic.py",
    "spray-and-wait": ROOT_DIR / "dtn" / "spray_and_wait.py",
    "prophet": ROOT_DIR / "dtn" / "prophet.py",
}

@dataclass(frozen=True)
class MeshPayBenchmarkConfig:
    routing: str
    medium: str

    clients: int
    authorities: int
    accounts_per_station: int
    
    payment_rate: float
    amount: int
    initial_balance: int

    duration: float
    warmup: float
    settle_time: float
    max_submit_workers: int

    seed: int
    log_dir: Path
    clean: bool

    node_range: float
    area_width: float
    area_height: float
    min_velocity: float
    max_velocity: float
    mobility_start: float
    no_mobility: bool

    plot: bool

    attack: str
    attack_loss_probability: float
    attack_tpre: float
    attack_tatk: float
    attack_tpost: float
    attack_target_count: str
    attack_load_rate: float
    keep_debug_logs: bool

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["log_dir"] = str(self.log_dir)
        return data

    def validate(self) -> None:
        if self.routing not in ROUTER_FILES:
            raise ValueError(f"unsupported routing protocol: {self.routing}")

        if self.medium not in {"adhoc", "mesh"}:
            raise ValueError("--medium must be one of: adhoc, mesh")

        if self.clients < 1:
            raise ValueError("--clients must be at least 2")

        if self.authorities < 1:
            raise ValueError("--authorities must be at least 1")

        if self.accounts_per_station < 1:
            raise ValueError("--accounts-per-station must be at least 1")

        if self.payment_rate <= 0:
            raise ValueError("--payment-rate must be greater than 0")

        if self.amount <= 0:
            raise ValueError("--amount must be greater than 0")

        if self.initial_balance < 0:
            raise ValueError("--initial-balance must be >= 0")

        if self.duration <= 0:
            raise ValueError("--duration must be greater than 0")

        if self.warmup < 0:
            raise ValueError("--warmup must be >= 0")

        if self.settle_time < 0:
            raise ValueError("--settle-time must be >= 0")

        if self.max_submit_workers < 1:
            raise ValueError("--max-submit-workers must be >= 1")

        if self.node_range <= 0:
            raise ValueError("--node-range must be greater than 0")

        if self.area_width <= 0:
            raise ValueError("--area-width must be greater than 0")

        if self.area_height <= 0:
            raise ValueError("--area-height must be greater than 0")

        if self.min_velocity <= 0:
            raise ValueError("--min-velocity must be greater than 0")

        if self.max_velocity < self.min_velocity:
            raise ValueError("--max-velocity must be >= --min-velocity")

        if self.mobility_start < 0:
            raise ValueError("--mobility-start must be >= 0")

        if self.attack not in {"none", "packetloss", "load", "packetloss-load"}:
            raise ValueError("--attack must be one of: none, packetloss, load, packetloss-load")

        if not 0.0 <= self.attack_loss_probability <= 1.0:
            raise ValueError("--attack-loss-probability must be between 0.0 and 1.0")

        if self.attack_tpre < 0:
            raise ValueError("--attack-tpre must be >= 0")

        if self.attack_tatk < 0:
            raise ValueError("--attack-tatk must be >= 0")

        if self.attack_tpost < 0:
            raise ValueError("--attack-tpost must be >= 0")

        if self.attack != "none" and self.attack_tatk <= 0:
            raise ValueError("--attack-tatk must be greater than 0 when attack is enabled")

        if self.attack != "none" and (
            self.attack_tpre + self.attack_tatk + self.attack_tpost
        ) > self.duration:
            raise ValueError(
                "--duration must be at least --attack-tpre + --attack-tatk + --attack-tpost"
            )

        if self.attack_load_rate < 0:
            raise ValueError("--attack-load-rate must be >= 0")

        if self.attack_target_count != "auto":
            try:
                target_count = int(self.attack_target_count)
            except ValueError as exc:
                raise ValueError("--attack-target-count must be auto or a non-negative integer") from exc

            if target_count < 0:
                raise ValueError("--attack-target-count must be auto or a non-negative integer")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automatic MeshPay offline payment benchmark"
    )

    parser.add_argument(
        "--routing",
        required=True,
        choices=sorted(ROUTER_FILES),
        help="DTN routing protocol.",
    )

    parser.add_argument(
        "--medium",
        default="adhoc",
        choices=["adhoc", "mesh"],
        help="Wireless medium.",
    )

    parser.add_argument(
        "--clients",
        type=int,
        default=5,
        help="Number of client nodes.",
    )

    parser.add_argument(
        "--authorities",
        type=int,
        default=4,
        help="Number of authority nodes.",
    )

    parser.add_argument(
        "--payment-rate",
        type=float,
        default=1.0,
        help="Maximum payment submission rate in payments per second.",
    )

    parser.add_argument(
        "--accounts-per-station",
        type=int,
        default=None,
        help="Virtual accounts per physical client. Default: enough accounts for the requested traffic.",
    )

    parser.add_argument(
        "--amount",
        type=int,
        default=1,
        help="Amount per payment.",
    )

    parser.add_argument(
        "--initial-balance",
        type=int,
        default=10000,
        help="Initial balance for every virtual account.",
    )

    parser.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="Total measurement duration in seconds.",
    )

    parser.add_argument(
        "--warmup",
        type=float,
        default=5.0,
        help="Seconds to wait after starting DTN daemons before submitting payments.",
    )

    parser.add_argument(
        "--settle-time",
        type=float,
        default=60.0,
        help="Drain-only seconds after traffic stops before metrics are collected.",
    )

    parser.add_argument(
        "--max-submit-workers",
        type=int,
        default=0,
        help=(
            "Maximum concurrent payment-submission workers. "
            "0 means min(--clients, 8)."
        ),
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=20,
        help="Random seed.",
    )

    parser.add_argument(
        "--log-dir",
        default=str(DEFAULT_LOG_DIR),
        help="Benchmark log directory.",
    )

    parser.add_argument(
        "--keep-debug-logs",
        action="store_true",
        help="Keep daemon logs and all bundle/delivered logs (default: False/clean up to save storage).",
    )

    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not delete the previous log directory before this run.",
    )

    parser.add_argument(
        "--node-range",
        type=float,
        default=40.0,
        help="Wireless range for each node.",
    )

    parser.add_argument(
        "--no-mobility",
        action="store_true",
        help="Disable mobility.",
    )

    parser.add_argument(
        "--plot",
        action="store_true",
        help="Show Mininet-WiFi graph.",
    )

    parser.add_argument(
        "--attack",
        default="none",
        choices=["none", "packetloss", "load", "packetloss-load"],
        help="Attack mode to run during the benchmark.",
    )

    parser.add_argument(
        "--attack-loss-probability",
        type=float,
        default=0.1,
        help="Random packet-loss probability for packetloss attacks.",
    )

    parser.add_argument(
        "--attack-tpre",
        type=float,
        default=60.0,
        help="Seconds before attack starts, relative to payment traffic start.",
    )

    parser.add_argument(
        "--attack-tatk",
        type=float,
        default=180.0,
        help="Seconds to keep the attack active.",
    )

    parser.add_argument(
        "--attack-tpost",
        type=float,
        default=60.0,
        help="Seconds to observe after attack stops.",
    )

    parser.add_argument(
        "--attack-target-count",
        default="auto",
        help="Number of random nodes to attack, capped at floor(total_nodes / 3), or auto.",
    )

    parser.add_argument(
        "--attack-load-rate",
        type=float,
        default=0.0,
        help="Synthetic DTN load bundles per second during load attacks. 0 means use payment rate.",
    )

    return parser.parse_args()


def build_config(args: argparse.Namespace) -> MeshPayBenchmarkConfig:
    if args.attack != "none":
        traffic_duration = min(args.duration, args.attack_tpre + args.attack_tatk + args.attack_tpost)
    else:
        traffic_duration = args.duration

    import math
    target_payments = math.ceil(args.payment_rate * traffic_duration)
    if args.accounts_per_station is None:
        accounts_per_station = max(100, math.ceil(target_payments / args.clients))
    else:
        accounts_per_station = int(args.accounts_per_station)

    config = MeshPayBenchmarkConfig(
        routing=args.routing,
        medium=args.medium,
        clients=args.clients,
        authorities=args.authorities,
        accounts_per_station=accounts_per_station,
        payment_rate=args.payment_rate,
        amount=args.amount,
        initial_balance=args.initial_balance,
        duration=args.duration,
        warmup=args.warmup,
        settle_time=args.settle_time,
        max_submit_workers=(
            int(args.max_submit_workers)
            if int(args.max_submit_workers) > 0
            else min(args.clients, 8)
        ),
        seed=args.seed,
        log_dir=Path(args.log_dir),
        clean=not args.no_clean,
        node_range=args.node_range,
        area_width=200.0,
        area_height=200.0,
        min_velocity=0.5,
        max_velocity=2.0,
        mobility_start=1.0,
        no_mobility=args.no_mobility,
        plot=args.plot,
        attack=args.attack,
        attack_loss_probability=args.attack_loss_probability,
        attack_tpre=args.attack_tpre,
        attack_tatk=args.attack_tatk,
        attack_tpost=args.attack_tpost,
        attack_target_count=args.attack_target_count,
        attack_load_rate=args.attack_load_rate,
        keep_debug_logs=args.keep_debug_logs,
    )

    config.validate()
    return config



def prepare_log_dir(config: MeshPayBenchmarkConfig) -> Path:
    if config.clean and config.log_dir.exists():
        shutil.rmtree(config.log_dir)

    config.log_dir.mkdir(parents=True, exist_ok=True)
    (config.log_dir / "stores").mkdir(parents=True, exist_ok=True)

    return config.log_dir


def router_file_for(routing: str) -> Path:
    router_file = ROUTER_FILES[routing]

    if not router_file.exists():
        raise FileNotFoundError(f"router file not found: {router_file}")

    return router_file


def write_metadata(config: MeshPayBenchmarkConfig) -> None:
    metadata_path = config.log_dir / "benchmark_config.json"

    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2, sort_keys=True)
        f.write("\n")


def station_params(
    index: int,
    ip: str,
    config: MeshPayBenchmarkConfig,
) -> dict:
    params = {
        "ip": ip,
        "range": config.node_range,
    }

    if config.no_mobility:
        # Simple deterministic layout.
        x = 10 + ((index - 1) * 30)
        y = 10
        params["position"] = f"{x:.2f},{y:.2f},0"
    else:
        params.update(
            {
                "min_x": 0,
                "max_x": config.area_width,
                "min_y": 0,
                "max_y": config.area_height,
                "min_v": config.min_velocity,
                "max_v": config.max_velocity,
            }
        )

    return params

def build_initial_balances(
    client_names: list[str],
    accounts_per_station: int,
    initial_balance: int,
    include_physical_accounts: bool = True,
) -> dict[str, int]:
    balances: dict[str, int] = {}

    for client_name in client_names:
        if include_physical_accounts:
            balances[client_name] = initial_balance

        for index in range(1, accounts_per_station + 1):
            account_id = make_account_id(client_name, index)
            balances[account_id] = initial_balance

    return balances
    
def create_meshpay_nodes(net: Mininet_wifi, config: MeshPayBenchmarkConfig):
    clients = []
    authorities = []

    client_names = [
        f"sta{i}"
        for i in range(1, config.clients + 1)
    ]

    authority_names = [
        f"auth{i}"
        for i in range(1, config.authorities + 1)
    ]

    initial_balances = build_initial_balances(
        client_names=client_names,
        accounts_per_station=config.accounts_per_station,
        initial_balance=config.initial_balance,
    )

    index = 1

    for client_name in client_names:
        ip = f"10.0.0.{index}/24"
        params = station_params(index, ip, config)

        client = net.addStation(
            client_name,
            cls=Client,
            committee=authority_names,
            initial_balance=config.initial_balance,
            accounts_per_station=config.accounts_per_station,
            **params,
        )

        clients.append(client)
        index += 1

    for authority_name in authority_names:
        ip = f"10.0.0.{index}/24"
        params = station_params(index, ip, config)

        authority = net.addStation(
            authority_name,
            cls=Authority,
            committee=authority_names,
            initial_balances=initial_balances,
            port=8000 + index,
            **params,
        )

        authorities.append(authority)
        index += 1

    return clients, authorities


def add_wireless_links(net: Mininet_wifi, nodes, medium: str) -> None:
    for node in nodes:
        intf = f"{node.name}-wlan0"

        if medium == "adhoc":
            net.addLink(
                node,
                cls=adhoc,
                intf=intf,
                ssid="meshpayOfflineBench",
                mode="g",
                channel=5,
                ht_cap="HT40+",
            )

        elif medium == "mesh":
            net.addLink(
                node,
                cls=mesh,
                intf=intf,
                ssid="meshpayOfflineBenchMesh",
                channel=5,
                ht_cap="HT40+",
            )


def configure_mobility(
    net: Mininet_wifi,
    config: MeshPayBenchmarkConfig,
) -> None:
    if config.no_mobility:
        info("*** Mobility disabled\n")
        return

    info("*** Configuring Mininet-WiFi mobility model: RandomDirection\n")

    net.setMobilityModel(
        time=config.mobility_start,
        model="RandomDirection",
        max_x=config.area_width,
        max_y=config.area_height,
        seed=config.seed,
    )

def run_payment_traffic(
    runtime: MeshPayRuntime,
    clients,
    config: MeshPayBenchmarkConfig,
    attack_controller: BenchmarkAttack | None = None,
) -> tuple[float, float]:
    """Generate payments between virtual accounts based on time and rate."""

    rng = random.Random(config.seed)

    info("*** Starting virtual-account payment traffic\n")

    client_by_name = {
        client.name: client
        for client in clients
    }

    all_accounts: list[str] = []

    for client in clients:
        for index in range(1, config.accounts_per_station + 1):
            account_id = make_account_id(client.name, index)
            all_accounts.append(account_id)

    if len(all_accounts) < 2:
        raise ValueError("Need at least two virtual accounts")

    # Spread the first wave of submissions across physical stations.
    # Without this, all_accounts starts as sta1/u00001, sta1/u00002, ...
    # and the benchmark hammers one Mininet node shell with concurrent injects.
    rng.shuffle(all_accounts)

    # Set traffic to run for the full benchmark duration (no tail)
    if config.attack != "none":
        traffic_duration = min(config.duration, config.attack_tpre + config.attack_tatk + config.attack_tpost)
    else:
        traffic_duration = config.duration

    info(f"*** Physical client stations: {len(clients)}\n")
    info(f"*** Accounts per station:    {config.accounts_per_station}\n")
    info(f"*** Total virtual accounts:  {len(all_accounts)}\n")
    info(f"*** Payment rate:            {config.payment_rate} tx/s\n")
    info(f"*** Traffic duration:        {traffic_duration:.2f}s\n")

    started_at = time.time()
    traffic_deadline = started_at + traffic_duration

    if attack_controller is not None:
        attack_controller.start(started_at)

    traffic_lock = threading.Lock()
    submit_locks = {client.name: threading.Lock() for client in clients}
    currently_submitting = set()
    available_senders = deque(all_accounts)
    submitted_success = 0
    submitted_total = 0
    last_backpressure_log = 0.0
    next_submit_at = started_at
    submit_interval = 1.0 / config.payment_rate

    def worker_task(sender, recipient):
        nonlocal submitted_success, submitted_total
        host = sender.split("/", 1)[0]
        try:
            # Serialise payment submission per physical source node.  A single
            # Mininet node shell cannot safely run multiple node.cmd() calls at
            # once, and each payment injection uses that shell to contact the
            # in-memory DTN daemon.
            with submit_locks[host]:
                runtime.pay_account(
                    sender_account=sender,
                    recipient_account=recipient,
                    amount=config.amount,
                )
            with traffic_lock:
                submitted_success += 1
        except Exception as exc:
            runtime.record_event(
                {
                    "event": "payment_submit_failed",
                    "sender": sender,
                    "recipient": recipient,
                    "amount": config.amount,
                    "error": f"{type(exc).__name__}: {exc!r}",
                }
            )
        finally:
            client = client_by_name[host]
            with client._lock:
                can_reuse = client.can_pay_from(sender, config.amount)
            with traffic_lock:
                currently_submitting.discard(sender)
                if can_reuse:
                    available_senders.append(sender)

    max_workers = max(1, int(config.max_submit_workers))
    info(f"*** Using ThreadPoolExecutor with {max_workers} workers\n")

    target_payments = int(config.payment_rate * traffic_duration)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while True:
            now = time.time()
            
            # Check if traffic generation phase is over
            if submitted_total >= target_payments or now >= traffic_deadline:
                break

            # Enforce the target payment rate
            if now < next_submit_at:
                time.sleep(min(next_submit_at - now, 0.05))
                continue

            sender_account = None
            while True:
                with traffic_lock:
                    candidate = available_senders.popleft() if available_senders else None

                if candidate is None:
                    break

                host = candidate.split("/", 1)[0]
                client = client_by_name[host]
                with client._lock:
                    can_pay = client.can_pay_from(candidate, config.amount)

                if can_pay:
                    sender_account = candidate
                    break

            if sender_account is None:
                now = time.time()
                with traffic_lock:
                    if now - last_backpressure_log > 0.5:
                        runtime.record_event(
                            {
                                "event": "payment_backpressure",
                                "submitted": submitted_success,
                                "reason": "no virtual account is currently available",
                            }
                        )
                        last_backpressure_log = now
                time.sleep(0.01)
                continue

            recipient_account = rng.choice(all_accounts)
            while recipient_account == sender_account:
                recipient_account = rng.choice(all_accounts)

            with traffic_lock:
                currently_submitting.add(sender_account)
                submitted_total += 1

                next_submit_at += submit_interval

                # If we somehow fell massively behind (e.g. > 1 second), prevent infinite blast:
                if next_submit_at < time.time() - 1.0:
                    next_submit_at = time.time()

            executor.submit(worker_task, sender_account, recipient_account)

    submission_finished_at = time.time()

    runtime.record_event(
        {
            "event": "payment_submission_finished",
            "submitted": submitted_success,
            "submission_duration_s": submission_finished_at - started_at,
            "physical_client_stations": len(clients),
            "accounts_per_station": config.accounts_per_station,
            "total_virtual_accounts": len(all_accounts),
        }
    )

    ended_at = time.time()

    return started_at, ended_at

def print_summary(report: dict) -> None:
    summary = report["summary"]
    quorum = report["latency_ms"]["time_to_quorum"]
    accepted = report["latency_ms"]["time_to_acceptance"]

    info("\n*** MeshPay payment benchmark summary\n")
    info(f"payments_created:                  {summary['payments_created']}\n")
    info(f"payments_confirmed:                {summary['payments_confirmed']}\n")
    info(f"payments_accepted:                 {summary['payments_accepted']}\n")
    info(f"payment_acceptance_rate_percent:   {summary['payment_acceptance_rate_percent']:.2f}%\n")

    info(f"created_tps:                       {summary['created_tps']:.4f}\n")
    info(f"confirmed_tps:                     {summary['confirmed_tps']:.4f}\n")
    info(f"accepted_tps:                      {summary['accepted_tps']:.4f}\n")

    info(f"tx_payloads_per_second:            {summary['tx_payloads_per_second']:.4f}\n")
    info(f"rx_payloads_per_second:            {summary['rx_payloads_per_second']:.4f}\n")
    info(f"tx_plus_rx_payloads_per_second:    {summary['tx_plus_rx_payloads_per_second']:.4f}\n")

    info(f"tx_bytes_per_second:               {summary['tx_bytes_per_second']:.4f}\n")
    info(f"rx_bytes_per_second:               {summary['rx_bytes_per_second']:.4f}\n")
    info(f"tx_plus_rx_bytes_per_second:       {summary['tx_plus_rx_bytes_per_second']:.4f}\n")

    if quorum["avg"] is not None:
        info(f"avg_time_to_quorum_ms:             {quorum['avg']:.4f}\n")
        info(f"p50_time_to_quorum_ms:             {quorum['p50']:.4f}\n")
        info(f"p95_time_to_quorum_ms:             {quorum['p95']:.4f}\n")
    else:
        info("avg_time_to_quorum_ms:             None\n")

    if accepted["avg"] is not None:
        info(f"avg_time_to_acceptance_ms:         {accepted['avg']:.4f}\n")
        info(f"p50_time_to_acceptance_ms:         {accepted['p50']:.4f}\n")
        info(f"p95_time_to_acceptance_ms:         {accepted['p95']:.4f}\n")
    else:
        info("avg_time_to_acceptance_ms:         None\n")


def _set_lightweight_dtn_metric_env() -> dict[str, str | None]:
    """Disable DTN hot-path debug files for socket-IPC benchmark mode.

    Payment metrics come from the runtime's buffered payment.log, flushed before
    collection.  Router injection and delivery use Unix-domain sockets, so
    events.jsonl and delivered.log are no longer needed during benchmark runs.
    """
    names = {
        "MESHPAY_DTN_EVENT_LOG": "0",
        "MESHPAY_DTN_DELIVERED_LOG": "0",
        "MESHPAY_DTN_EVENT_FILTER": "metrics",
        "MESHPAY_SKIP_DELIVERY_RECEIPTS": "1",
    }
    previous = {name: os.environ.get(name) for name in names}
    os.environ.update(names)
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def topology(config: MeshPayBenchmarkConfig) -> None:
    router_file = router_file_for(config.routing)
    prepare_log_dir(config)
    write_metadata(config)

    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    info("*** Creating MeshPay benchmark clients and authorities\n")
    clients, authorities = create_meshpay_nodes(net, config)
    nodes = clients + authorities

    info("*** Configuring propagation model\n")
    net.setPropagationModel(model="logNormalShadowing", exp=3.2, variance=2.0)

    info("*** Configuring nodes\n")
    net.configureNodes()

    info(f"*** Configuring wireless medium={config.medium}\n")
    add_wireless_links(net, nodes, config.medium)

    if config.plot:
        net.plotGraph(
            max_x=int(config.area_width + 20),
            max_y=int(config.area_height + 20),
        )

    configure_mobility(net, config)

    info("*** Building network\n")
    net.build()

    runtime = MeshPayRuntime(
        net=net,
        clients=clients,
        authorities=authorities,
        routing=config.routing,
        router_file=router_file,
        log_dir=config.log_dir,
        root_dir=ROOT_DIR,
        payment_poll_interval=dtn_config.DEFAULT_PAYMENT_POLL_INTERVAL,
        # Critical: keep the DTN daemon discovery mode aligned with the
        # Mininet-WiFi link type.  Without this, --medium adhoc accidentally
        # starts DTN routers in mesh mode, which can collapse delivery.
        medium=config.medium,
    )

    attack_controller = None

    # Treat packetloss with probability 0 as an explicit no-op baseline.
    # Starting the attack controller for loss=0 only adds node.cmd()/iptables
    # activity and can collide with payment injection threads.
    attack_is_noop = (
        config.attack == "packetloss"
        and float(config.attack_loss_probability) <= 0.0
    )

    if config.attack != "none" and not attack_is_noop:
        attack_load_rate = (
            config.attack_load_rate
            if config.attack_load_rate > 0
            else config.payment_rate
        )
        attack_controller = BenchmarkAttack(
            runtime=runtime,
            all_nodes=nodes,
            client_nodes=clients,
            log_dir=config.log_dir,
            attack_type=config.attack,
            loss_probability=config.attack_loss_probability,
            tpre=config.attack_tpre,
            tatk=config.attack_tatk,
            tpost=config.attack_tpost,
            target_count=config.attack_target_count,
            load_rate=attack_load_rate,
            seed=config.seed,
        )
    elif attack_is_noop:
        runtime.record_event(
            {
                "event": "attack_noop",
                "attack": config.attack,
                "reason": "packetloss_probability_zero",
                "loss_probability": config.attack_loss_probability,
            }
        )

    started_at = time.time()
    ended_at = started_at

    previous_env = _set_lightweight_dtn_metric_env()

    try:
        runtime.start()

        if config.warmup > 0:
            info(f"*** Warm-up: waiting {config.warmup:.2f}s\n")
            time.sleep(config.warmup)

        started_at, traffic_ended_at = run_payment_traffic(
            runtime=runtime,
            clients=clients,
            config=config,
            attack_controller=attack_controller,
        )

        if config.settle_time > 0:
            info(f"*** DTN settle/drain: waiting {config.settle_time:.2f}s after traffic stops\n")
            time.sleep(config.settle_time)

        ended_at = time.time()

        # Runtime records payment events in memory to avoid payment.log writes
        # on the hot path.  Flush once before the existing metrics collector
        # reads payment.log.
        runtime.flush_payment_log()

        report = collect_payment_metrics(
            log_dir=config.log_dir,
            started_at=started_at,
            ended_at=ended_at,
        )

        report["config"] = config.to_dict()
        report["timing"] = {
            "started_at": started_at,
            "traffic_ended_at": traffic_ended_at,
            "ended_at": ended_at,
            "settle_time_s": config.settle_time,
        }

        if attack_controller is not None:
            report["attack"] = attack_controller.metadata()

        write_reports(report, config.log_dir)
        print_summary(report)

        info(f"\n*** Reports saved to: {config.log_dir}\n")
        info(f"*** JSON: {config.log_dir / 'benchmark.json'}\n")
        info(f"*** CSV:  {config.log_dir / 'benchmark.csv'}\n")

    finally:
        _restore_env(previous_env)

        if attack_controller is not None:
            attack_controller.cleanup()

        runtime.stop()
        info("*** Stopping network\n")
        net.stop()
        cleanup_logs(config)


def cleanup_logs(config: MeshPayBenchmarkConfig) -> None:
    if config.keep_debug_logs:
        return

    info("*** Cleaning up debug logs to save storage\n")
    log_dir = config.log_dir

    # 1. Delete all daemon logs in log_dir (except payment.log)
    for p in log_dir.glob("*.log"):
        if p.name != "payment.log":
            try:
                p.unlink()
            except Exception as e:
                info(f"Failed to delete daemon log {p}: {e}\n")

    # 2. Delete lightweight store metric logs after reports are written.
    #    No bundle JSON files are expected in the refactored in-memory store.
    stores_dir = log_dir / "stores"
    if stores_dir.exists():
        for p in stores_dir.rglob("*"):
            if p.is_file():
                suffix = p.suffix.lower()
                if suffix == ".jsonl":
                    continue
                if suffix in (".json", ".txt", ".log"):
                    try:
                        p.unlink()
                    except Exception as e:
                        info(f"Failed to delete stores file {p}: {e}\n")


def main() -> None:
    setLogLevel("info")

    args = parse_args()
    config = build_config(args)

    topology(config)


if __name__ == "__main__":
    main()