#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running this file directly:
#   sudo python3 examples/meshpay_offline.py ...
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mininet.log import info, setLogLevel
from mn_wifi.link import adhoc, mesh, wmediumd
from mn_wifi.net import Mininet_wifi
from mn_wifi.wmediumdConnector import interference

from meshpay.cli.meshpay_cli import MeshPayCLI, MeshPayRuntime
from meshpay.offline.nodes.authority import Authority
from meshpay.offline.nodes.client import Client
from meshpay.offline.virtual_accounts import make_account_id

EXAMPLES_DIR = Path(__file__).resolve().parent
DTN_DIR = ROOT_DIR / "dtn"
DEFAULT_LOG_DIR = ROOT_DIR / "logs" / "examples" / "meshpay_offline"

ROUTER_FILES = {
    "epidemic": DTN_DIR / "epidemic.py",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MeshPay offline payment interactive demo"
    )

    parser.add_argument(
        "--routing",
        required=True,
        choices=["epidemic"],
        help="DTN routing protocol to start automatically on every node.",
    )

    parser.add_argument(
        "--medium",
        default="adhoc",
        choices=["adhoc", "mesh"],
        help="Wireless medium to use.",
    )

    parser.add_argument(
        "--clients",
        type=int,
        default=3,
        help="Number of client stations to create.",
    )

    parser.add_argument(
        "--authorities",
        type=int,
        default=4,
        help="Number of authority stations to create.",
    )

    parser.add_argument(
        "--initial-balance",
        type=int,
        default=100,
        help="Initial balance for each client.",
    )

    parser.add_argument(
        "--log-dir",
        default=str(DEFAULT_LOG_DIR),
        help="Directory for daemon logs, DTN stores, and payment logs.",
    )

    parser.add_argument(
        "--plot",
        action="store_true",
        help="Show Mininet-WiFi graph.",
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
        "--mobility-start",
        type=float,
        default=1.0,
        help="Time at which RandomDirection mobility starts.",
    )

    parser.add_argument(
        "--min-velocity",
        type=float,
        default=0.5,
        help="Minimum velocity for RandomDirection mobility.",
    )

    parser.add_argument(
        "--max-velocity",
        type=float,
        default=2.0,
        help="Maximum velocity for RandomDirection mobility.",
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=7,
        help="Random seed for RandomDirection mobility.",
    )

    parser.add_argument(
        "--node-range",
        type=float,
        default=25.0,
        help="Wireless range for each node.",
    )

    parser.add_argument(
        "--station-spacing",
        type=float,
        default=35.0,
        help="Initial spacing between nodes in static mode.",
    )

    parser.add_argument(
        "--no-mobility",
        action="store_true",
        help="Disable RandomDirection mobility.",
    )

    parser.add_argument(
        "--accounts-per-station",
        type=int,
        default=100,
        help="Number of virtual logical accounts hosted by each client station.",
    )
    
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.clients < 2:
        raise SystemExit("--clients must be at least 2.")

    if args.authorities < 1:
        raise SystemExit("--authorities must be at least 1.")

    if args.accounts_per_station < 1:
        raise SystemExit("--accounts-per-station must be at least 1.")
    
    if args.initial_balance < 0:
        raise SystemExit("--initial-balance must be >= 0.")

    if args.area_width <= 0:
        raise SystemExit("--area-width must be greater than 0.")

    if args.area_height <= 0:
        raise SystemExit("--area-height must be greater than 0.")

    if args.node_range <= 0:
        raise SystemExit("--node-range must be greater than 0.")

    if args.station_spacing <= 0:
        raise SystemExit("--station-spacing must be greater than 0.")

    if args.mobility_start < 0:
        raise SystemExit("--mobility-start must be >= 0.")

    if args.min_velocity <= 0:
        raise SystemExit("--min-velocity must be greater than 0.")

    if args.max_velocity < args.min_velocity:
        raise SystemExit("--max-velocity must be >= --min-velocity.")


def prepare_log_dir(path: str | Path) -> Path:
    log_dir = Path(path)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "stores").mkdir(parents=True, exist_ok=True)
    return log_dir


def router_file_for(routing: str) -> Path:
    router_file = ROUTER_FILES[routing]

    if not router_file.exists():
        raise FileNotFoundError(f"Router file not found: {router_file}")

    return router_file

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

def create_meshpay_nodes(net: Mininet_wifi, args: argparse.Namespace):
    clients = []
    authorities = []

    authority_names = [
        f"auth{i}"
        for i in range(1, args.authorities + 1)
    ]

    client_names = [
        f"sta{i}"
        for i in range(1, args.clients + 1)
    ]

    initial_balances = build_initial_balances(
        client_names=client_names,
        accounts_per_station=args.accounts_per_station,
        initial_balance=args.initial_balance,
    )

    node_index = 1

    for client_name in client_names:
        ip = f"10.0.0.{node_index}/24"

        params = station_params(
            node_index=node_index,
            ip=ip,
            args=args,
        )

        client = net.addStation(
            client_name,
            cls=Client,
            committee=authority_names,
            initial_balance=args.initial_balance,
            accounts_per_station=args.accounts_per_station,
            **params,
        )

        clients.append(client)
        node_index += 1

    for authority_name in authority_names:
        ip = f"10.0.0.{node_index}/24"

        params = station_params(
            node_index=node_index,
            ip=ip,
            args=args,
        )

        authority = net.addStation(
            authority_name,
            cls=Authority,
            committee=authority_names,
            initial_balances=initial_balances,
            port=8000 + node_index,
            **params,
        )

        authorities.append(authority)
        node_index += 1

    return clients, authorities


def station_params(
    node_index: int,
    ip: str,
    args: argparse.Namespace,
) -> dict:
    x = 10 + ((node_index - 1) * args.station_spacing)
    y = 10
    fixed_position = f"{x:.2f},{y:.2f},0"

    params = {
        "ip": ip,
        "range": args.node_range,
    }

    if args.no_mobility:
        params["position"] = fixed_position
    else:
        params.update(
            {
                "min_x": 0,
                "max_x": args.area_width,
                "min_y": 0,
                "max_y": args.area_height,
                "min_v": args.min_velocity,
                "max_v": args.max_velocity,
            }
        )

    return params


def add_wireless_links(net: Mininet_wifi, nodes, medium: str) -> None:
    for node in nodes:
        intf = f"{node.name}-wlan0"

        if medium == "adhoc":
            net.addLink(
                node,
                cls=adhoc,
                intf=intf,
                ssid="meshpayOffline",
                mode="g",
                channel=5,
                ht_cap="HT40+",
            )

        elif medium == "mesh":
            net.addLink(
                node,
                cls=mesh,
                intf=intf,
                ssid="meshpayOfflineMesh",
                channel=5,
                ht_cap="HT40+",
            )


def configure_mobility(net: Mininet_wifi, args: argparse.Namespace) -> None:
    if args.no_mobility:
        info("*** Mobility disabled\n")
        return

    info("*** Configuring mobility model: RandomDirection\n")

    net.setMobilityModel(
        time=args.mobility_start,
        model="RandomDirection",
        max_x=args.area_width,
        max_y=args.area_height,
        seed=args.seed,
    )


def topology(args: argparse.Namespace) -> None:
    router_file = router_file_for(args.routing)
    log_dir = prepare_log_dir(args.log_dir)

    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    info("*** Creating MeshPay clients and authorities\n")
    clients, authorities = create_meshpay_nodes(net, args)
    nodes = clients + authorities

    info("*** Configuring propagation model\n")
    net.setPropagationModel(model="logDistance", exp=4)

    info("*** Configuring nodes\n")
    net.configureNodes()

    info(f"*** Configuring wireless medium={args.medium}\n")
    add_wireless_links(net, nodes, args.medium)

    if args.plot:
        net.plotGraph(
            max_x=int(args.area_width + 20),
            max_y=int(args.area_height + 20),
        )

    configure_mobility(net, args)

    info("*** Building network\n")
    net.build()

    runtime = MeshPayRuntime(
        net=net,
        clients=clients,
        authorities=authorities,
        routing=args.routing,
        router_file=router_file,
        log_dir=log_dir,
        root_dir=ROOT_DIR,
        discovery_interval=2.0,
        payment_poll_interval=0.5,
    )

    try:
        runtime.start()

        info("\n*** MeshPay offline interactive demo is ready\n")
        info("*** Physical payment:      pay sta1 sta3 10\n")
        info("*** Alternative command:   sta1 pay sta3 10\n")
        info("*** Virtual payment:       vpay sta1/u00001 sta3/u00001 10\n")
        info("*** Show accounts:         accounts\n")
        info("*** Show node accounts:    accounts sta1\n")
        info("*** Show balances:         balance\n")
        info("*** Show one balance:      balance sta1\n")
        info("*** Show payments:         payments\n")
        info("*** Show payment log:      paymentlog\n")
        info("*** Show DTN logs:         dtnlog\n")
        info("*** Show delivered:        delivered\n")
        info(f"*** Logs directory:        {log_dir}\n\n")

        MeshPayCLI(
            net,
            runtime=runtime,
        )

    finally:
        runtime.stop()
        info("*** Stopping network\n")
        net.stop()


def main() -> None:
    setLogLevel("info")
    args = parse_args()
    validate_args(args)

    try:
        topology(args)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()