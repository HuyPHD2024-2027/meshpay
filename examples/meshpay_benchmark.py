#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mininet.log import info, setLogLevel
from mn_wifi.link import adhoc, mesh, wmediumd
from mn_wifi.net import Mininet_wifi
from mn_wifi.wmediumdConnector import interference

from meshpay.benchmark.payment_metrics import collect_payment_metrics
from meshpay.benchmark.report import write_reports
from meshpay.cli.meshpay_cli import MeshPayRuntime
from meshpay.offline.nodes.authority import Authority
from meshpay.offline.nodes.client import Client
from meshpay.offline.virtual_accounts import make_account_id

DEFAULT_LOG_DIR = ROOT_DIR / "logs" / "benchmarks" / "meshpay_offline"

ROUTER_FILES = {
    "epidemic": ROOT_DIR / "dtn" / "epidemic.py",
}

@dataclass(frozen=True)
class MeshPayBenchmarkConfig:
    routing: str
    medium: str

    clients: int
    authorities: int
    accounts_per_station: int
    
    payments: int
    payment_rate: float
    amount: int
    initial_balance: int

    duration: float
    warmup: float

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

        if self.payments < 1:
            raise ValueError("--payments must be at least 1")

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automatic MeshPay offline payment benchmark"
    )

    parser.add_argument(
        "--routing",
        required=True,
        choices=["epidemic"],
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
        "--payments",
        type=int,
        default=20,
        help="Number of payments to submit.",
    )

    parser.add_argument(
        "--payment-rate",
        type=float,
        default=1.0,
        help="Maximum payment submission rate in payments per second.",
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
        default=100,
        help="Initial balance for every client.",
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
        help="Warm-up time before submitting payments.",
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
        "--no-clean",
        action="store_true",
        help="Do not delete old benchmark logs.",
    )

    parser.add_argument(
        "--node-range",
        type=float,
        default=40.0,
        help="Wireless range for each node.",
    )

    parser.add_argument(
        "--area-width",
        type=float,
        default=200.0,
        help="RandomDirection mobility area width.",
    )

    parser.add_argument(
        "--area-height",
        type=float,
        default=200.0,
        help="RandomDirection mobility area height.",
    )

    parser.add_argument(
        "--min-velocity",
        type=float,
        default=0.5,
        help="Minimum RandomDirection velocity.",
    )

    parser.add_argument(
        "--max-velocity",
        type=float,
        default=2.0,
        help="Maximum RandomDirection velocity.",
    )

    parser.add_argument(
        "--mobility-start",
        type=float,
        default=1.0,
        help="Time at which RandomDirection mobility starts.",
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
        "--accounts-per-station",
        type=int,
        default=100,
        help="Number of virtual logical accounts hosted by each client station.",
    )
    
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> MeshPayBenchmarkConfig:
    config = MeshPayBenchmarkConfig(
        routing=args.routing,
        medium=args.medium,
        clients=args.clients,
        authorities=args.authorities,
        accounts_per_station=args.accounts_per_station,
        payments=args.payments,
        payment_rate=args.payment_rate,
        amount=args.amount,
        initial_balance=args.initial_balance,
        duration=args.duration,
        warmup=args.warmup,
        seed=args.seed,
        log_dir=Path(args.log_dir),
        clean=not args.no_clean,
        node_range=args.node_range,
        area_width=args.area_width,
        area_height=args.area_height,
        min_velocity=args.min_velocity,
        max_velocity=args.max_velocity,
        mobility_start=args.mobility_start,
        no_mobility=args.no_mobility,
        plot=args.plot,
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
) -> tuple[float, float]:
    """Generate payments between virtual accounts.

    Physical stations are still sta1, sta2, ...
    Logical accounts are sta1/u00001, sta1/u00002, ...
    """

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

    info(f"*** Physical client stations: {len(clients)}\n")
    info(f"*** Accounts per station:    {config.accounts_per_station}\n")
    info(f"*** Total virtual accounts:  {len(all_accounts)}\n")
    info(f"*** Requested payments:      {config.payments}\n")

    started_at = time.time()
    deadline = started_at + config.duration

    traffic_lock = threading.Lock()
    currently_submitting = set()
    submitted_success = 0
    submitted_total = 0
    last_backpressure_log = 0.0
    next_submit_at = started_at
    submit_interval = 1.0 / config.payment_rate

    def worker_task(sender, recipient):
        nonlocal submitted_success, submitted_total
        try:
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
                    "error": str(exc),
                }
            )
        finally:
            with traffic_lock:
                currently_submitting.remove(sender)

    max_workers = min(100, config.payments)
    info(f"*** Using ThreadPoolExecutor with {max_workers} workers\n")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        while True:
            with traffic_lock:
                if submitted_success >= config.payments:
                    break
                if time.time() >= deadline:
                    break

                active_tasks = len(currently_submitting)
                if submitted_success + active_tasks >= config.payments:
                    should_wait = True
                else:
                    should_wait = False

            if should_wait:
                time.sleep(0.01)
                continue

            now = time.time()
            if now < next_submit_at:
                time.sleep(min(next_submit_at - now, 0.05))
                continue

            eligible_senders = []
            for account_id in all_accounts:
                with traffic_lock:
                    if account_id in currently_submitting:
                        continue
                host = account_id.split("/", 1)[0]
                client = client_by_name[host]
                with client._lock:
                    if client.can_pay_from(account_id, config.amount):
                        eligible_senders.append(account_id)

            if not eligible_senders:
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

            with traffic_lock:
                sender_account = rng.choice(eligible_senders)
                receiver_candidates = [
                    acc for acc in all_accounts if acc != sender_account
                ]
                recipient_account = rng.choice(receiver_candidates)

                currently_submitting.add(sender_account)
                submitted_total += 1
                next_submit_at = max(
                    next_submit_at + submit_interval,
                    time.time() + submit_interval,
                )

            executor.submit(worker_task, sender_account, recipient_account)

    submission_finished_at = time.time()

    runtime.record_event(
        {
            "event": "payment_submission_finished",
            "submitted": submitted_success,
            "requested": config.payments,
            "submission_duration_s": submission_finished_at - started_at,
            "physical_client_stations": len(clients),
            "accounts_per_station": config.accounts_per_station,
            "total_virtual_accounts": len(all_accounts),
        }
    )

    remaining = config.duration - (submission_finished_at - started_at)

    if remaining > 0:
        info(f"*** Waiting {remaining:.2f}s for final delivery\n")
        time.sleep(remaining)

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
        payment_poll_interval=0.5,
    )

    started_at = time.time()
    ended_at = started_at

    try:
        runtime.start()

        if config.warmup > 0:
            info(f"*** Warm-up: waiting {config.warmup:.2f}s\n")
            time.sleep(config.warmup)

        started_at, ended_at = run_payment_traffic(
            runtime=runtime,
            clients=clients,
            config=config,
        )

        report = collect_payment_metrics(
            log_dir=config.log_dir,
            started_at=started_at,
            ended_at=ended_at,
        )

        report["config"] = config.to_dict()
        report["timing"] = {
            "started_at": started_at,
            "ended_at": ended_at,
        }

        write_reports(report, config.log_dir)
        print_summary(report)

        info(f"\n*** Reports saved to: {config.log_dir}\n")
        info(f"*** JSON: {config.log_dir / 'benchmark.json'}\n")
        info(f"*** CSV:  {config.log_dir / 'benchmark.csv'}\n")

    finally:
        runtime.stop()
        info("*** Stopping network\n")
        net.stop()


def main() -> None:
    setLogLevel("info")

    args = parse_args()
    config = build_config(args)

    topology(config)


if __name__ == "__main__":
    main()