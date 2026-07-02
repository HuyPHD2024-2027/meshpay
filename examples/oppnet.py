#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shlex
import sys
import time
from pathlib import Path

# Allow running this file directly:
#   sudo python3 examples/oppnet.py ...
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

from meshpay.cli.oppnet_cli import OppNetCLI


EXAMPLES_DIR = Path(__file__).resolve().parent
DTN_DIR = ROOT_DIR / "dtn"
DEFAULT_LOG_DIR = ROOT_DIR / "logs" / "examples" / "oppnet"

ROUTER_FILES = {
    "epidemic": DTN_DIR / "epidemic.py",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MeshPay opportunistic-network interactive demo"
    )

    parser.add_argument(
        "--routing",
        required=True,
        choices=["epidemic"],
        help="DTN routing protocol to start automatically on every station.",
    )

    parser.add_argument(
        "--medium",
        default="adhoc",
        choices=["adhoc", "mesh"],
        help="Wireless medium to use.",
    )

    parser.add_argument(
        "--stations",
        type=int,
        default=3,
        help="Number of stations to create.",
    )

    parser.add_argument(
        "--log-dir",
        default=str(DEFAULT_LOG_DIR),
        help="Directory for daemon logs and bundle stores.",
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
        default=20.0,
        help="Wireless range for each station.",
    )

    parser.add_argument(
        "--station-spacing",
        type=float,
        default=35.0,
        help="Initial spacing between stations.",
    )

    parser.add_argument(
        "--no-mobility",
        action="store_true",
        help="Disable RandomDirection mobility.",
    )

    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.stations < 2:
        raise SystemExit("--stations must be at least 2.")

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


def create_stations(net: Mininet_wifi, args: argparse.Namespace):
    stations = []

    for index in range(1, args.stations + 1):
        name = f"sta{index}"
        ip = f"10.0.0.{index}/24"

        x = 10 + ((index - 1) * args.station_spacing)
        y = 10
        fixed_position = f"{x:.2f},{y:.2f},0"

        station_kwargs = {
            "ip": ip,
            "range": args.node_range,
        }

        if args.no_mobility:
            # Static mode: fixed linear placement.
            station_kwargs["position"] = fixed_position
        else:
            # Mobility mode: let Mininet-WiFi mobility model control positions.
            station_kwargs.update(
                {
                    "min_x": 0,
                    "max_x": args.area_width,
                    "min_y": 0,
                    "max_y": args.area_height,
                    "min_v": args.min_velocity,
                    "max_v": args.max_velocity,
                }
            )

        sta = net.addStation(name, **station_kwargs)
        stations.append(sta)

    return stations

def add_wireless_links(net: Mininet_wifi, stations, medium: str) -> None:
    for sta in stations:
        intf = f"{sta.name}-wlan0"

        if medium == "adhoc":
            net.addLink(
                sta,
                cls=adhoc,
                intf=intf,
                ssid="meshpayOppNet",
                mode="g",
                channel=5,
                ht_cap="HT40+",
            )

        elif medium == "mesh":
            net.addLink(
                sta,
                cls=mesh,
                intf=intf,
                ssid="meshpayOppNetMesh",
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


def start_dtn_routers(
    stations,
    routing: str,
    router_file: Path,
    log_dir: Path,
):
    info(f"*** Starting DTN routing: {routing}\n")
    info("*** Every station will run the selected DTN daemon automatically\n")

    processes = []

    for sta in stations:
        store = log_dir / "stores" / routing / sta.name
        log_file = log_dir / f"{sta.name}-{routing}.log"

        sta.cmd(f"rm -rf {shlex.quote(str(store))}")
        sta.cmd(f"mkdir -p {shlex.quote(str(store))}")

        cmd = (
            f"PYTHONPATH={shlex.quote(str(ROOT_DIR))} "
            f"python3 {shlex.quote(str(router_file))} "
            f"--node {shlex.quote(sta.name)} "
            f"--store {shlex.quote(str(store))} "
            f"--discovery-interval 0.5 "
            f"> {shlex.quote(str(log_file))} 2>&1"
        )

        proc = sta.popen(cmd, shell=True)
        processes.append((sta, proc))

    time.sleep(2)

    return processes


def stop_dtn_routers(stations, processes) -> None:
    info("*** Stopping DTN daemons\n")

    for _sta, proc in processes:
        try:
            proc.terminate()
        except Exception:
            pass

    for sta in stations:
        sta.cmd("pkill -f 'dtn/epidemic.py' || true")
        sta.cmd("pkill -f 'epidemic.py' || true")


def topology(args: argparse.Namespace) -> None:
    router_file = router_file_for(args.routing)
    log_dir = prepare_log_dir(args.log_dir)

    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    info("*** Creating stations\n")
    stations = create_stations(net, args)

    info("*** Configuring propagation model\n")
    net.setPropagationModel(model="logNormalShadowing", exp=3.2, variance=2.0)

    info("*** Configuring nodes\n")
    net.configureNodes()

    info(f"*** Configuring wireless medium={args.medium}\n")
    add_wireless_links(net, stations, args.medium)

    if args.plot:
        net.plotGraph(
            max_x=int(args.area_width + 20),
            max_y=int(args.area_height + 20),
        )

    configure_mobility(net, args)

    info("*** Building network\n")
    net.build()

    processes = []

    try:
        processes = start_dtn_routers(
            stations=stations,
            routing=args.routing,
            router_file=router_file,
            log_dir=log_dir,
        )

        info("\n*** MeshPay OppNet interactive demo is ready\n")
        info('*** Send message:       sta1 send sta3 "Hello World"\n')
        info("*** Show deliveries:    delivered\n")
        info("*** Show one node:      delivered sta3\n")
        info("*** Show router logs:   dtnlog\n")
        info("*** Show one log:       dtnlog sta1\n")
        info(f"*** Logs directory:     {log_dir}\n\n")

        OppNetCLI(
            net,
            routing=args.routing,
            router_file=router_file,
            log_dir=log_dir,
        )

    finally:
        stop_dtn_routers(stations, processes)
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