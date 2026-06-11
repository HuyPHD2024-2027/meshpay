#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mininet.log import info, setLogLevel
from mn_wifi.link import adhoc, mesh, wmediumd
from mn_wifi.net import Mininet_wifi
from mn_wifi.wmediumdConnector import interference

from meshpay.benchmark.metrics import collect_metrics
from meshpay.benchmark.report import write_reports
from meshpay.benchmark.traffic import BenchmarkMessage, TrafficGenerator


DEFAULT_LOG_DIR = ROOT_DIR / "logs" / "benchmarks" / "oppnet"

ROUTER_FILES = {
    "epidemic": ROOT_DIR / "dtn" / "epidemic.py",
}


@dataclass(frozen=True)
class BenchmarkConfig:
    routing: str
    medium: str
    stations: int

    messages: int
    message_rate: float
    payload_size: int
    duration: float

    # If src/dst are None, source and destination are random per message.
    src: Optional[str]
    dst: Optional[str]

    seed: int
    log_dir: Path
    clean: bool

    # Topology / mobility area
    node_range: float
    station_spacing: float
    area_width: float
    area_height: float

    # Only supported mobility model
    mobility: str
    mobility_start: float
    min_velocity: float
    max_velocity: float

    plot: bool

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["log_dir"] = str(self.log_dir)
        return data

    @property
    def injection_duration(self) -> float:
        if self.messages <= 1:
            return 0.0
        return (self.messages - 1) / self.message_rate

    def validate(self) -> None:
        if self.routing not in ROUTER_FILES:
            raise ValueError(f"Unsupported routing protocol: {self.routing}")

        if self.medium not in {"adhoc", "mesh"}:
            raise ValueError("--medium must be one of: adhoc, mesh")

        if self.stations < 2:
            raise ValueError("--stations must be at least 2")

        if self.messages < 1:
            raise ValueError("--messages must be at least 1")

        if self.message_rate <= 0:
            raise ValueError("--message-rate must be greater than 0")

        if self.payload_size < 1:
            raise ValueError("--payload-size must be at least 1")

        if self.duration <= 0:
            raise ValueError("--duration must be greater than 0")

        if self.injection_duration > self.duration:
            raise ValueError(
                "Benchmark duration is too short for the requested traffic. "
                f"Need at least {self.injection_duration:.2f}s to inject "
                f"{self.messages} messages at {self.message_rate} msg/s."
            )

        if self.node_range <= 0:
            raise ValueError("--node-range must be greater than 0")

        if self.station_spacing <= 0:
            raise ValueError("--station-spacing must be greater than 0")

        if self.area_width <= 0:
            raise ValueError("--area-width must be greater than 0")

        if self.area_height <= 0:
            raise ValueError("--area-height must be greater than 0")

        if self.mobility != "random-direction":
            raise ValueError("--mobility must be random-direction")

        if self.mobility_start < 0:
            raise ValueError("--mobility-start must be >= 0")

        if self.mobility_start >= self.duration:
            raise ValueError("--mobility-start must be smaller than --duration")

        if self.min_velocity <= 0:
            raise ValueError("--min-velocity must be greater than 0")

        if self.max_velocity < self.min_velocity:
            raise ValueError("--max-velocity must be >= --min-velocity")

        valid_nodes = {f"sta{i}" for i in range(1, self.stations + 1)}

        if self.src is not None and self.src not in valid_nodes:
            raise ValueError(f"--src must be one of {sorted(valid_nodes)}")

        if self.dst is not None and self.dst not in valid_nodes:
            raise ValueError(f"--dst must be one of {sorted(valid_nodes)}")

        if self.src is not None and self.dst is not None and self.src == self.dst:
            raise ValueError("--src and --dst must be different")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automatic OppNet benchmark for MeshPay"
    )

    parser.add_argument(
        "--routing",
        required=True,
        choices=["epidemic"],
        help="DTN routing protocol to benchmark.",
    )

    parser.add_argument(
        "--medium",
        default="adhoc",
        choices=["adhoc", "mesh"],
        help="Wireless medium.",
    )

    parser.add_argument(
        "--stations",
        type=int,
        default=3,
        help="Number of stations.",
    )

    parser.add_argument(
        "--messages",
        type=int,
        default=20,
        help="Number of benchmark messages to generate.",
    )

    parser.add_argument(
        "--message-rate",
        type=float,
        default=1,
        help="Message injection rate in messages per second.",
    )

    parser.add_argument(
        "--payload-size",
        type=int,
        default=256,
        help="Payload size in bytes.",
    )

    parser.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="Total benchmark duration in seconds.",
    )

    parser.add_argument(
        "--src",
        default=None,
        help=(
            "Optional fixed source node, e.g. sta1. "
            "If omitted, source is random per message."
        ),
    )

    parser.add_argument(
        "--dst",
        default=None,
        help=(
            "Optional fixed destination node, e.g. sta3. "
            "If omitted, destination is random per message."
        ),
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=20,
        help="Random seed for traffic generation and RandomDirection mobility.",
    )

    parser.add_argument(
        "--log-dir",
        default=str(DEFAULT_LOG_DIR),
        help="Benchmark log directory.",
    )

    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Do not delete the old benchmark log directory before running.",
    )

    parser.add_argument(
        "--node-range",
        type=float,
        default=20.0,
        help="Wireless range for each station in meters.",
    )

    parser.add_argument(
        "--station-spacing",
        type=float,
        default=35.0,
        help="Initial spacing between stations in meters.",
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
        "--mobility",
        default="random-direction",
        choices=["random-direction"],
        help="Mobility model. Only RandomDirection is supported.",
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
        help="Minimum node velocity for RandomDirection mobility.",
    )

    parser.add_argument(
        "--max-velocity",
        type=float,
        default=2.0,
        help="Maximum node velocity for RandomDirection mobility.",
    )

    parser.add_argument(
        "--plot",
        action="store_true",
        help="Show Mininet-WiFi graph.",
    )

    return parser.parse_args()


def build_config(args: argparse.Namespace) -> BenchmarkConfig:
    config = BenchmarkConfig(
        routing=args.routing,
        medium=args.medium,
        stations=args.stations,
        messages=args.messages,
        message_rate=args.message_rate,
        payload_size=args.payload_size,
        duration=args.duration,
        src=args.src,
        dst=args.dst,
        seed=args.seed,
        log_dir=Path(args.log_dir),
        clean=not args.no_clean,
        node_range=args.node_range,
        station_spacing=args.station_spacing,
        area_width=args.area_width,
        area_height=args.area_height,
        mobility=args.mobility,
        mobility_start=args.mobility_start,
        min_velocity=args.min_velocity,
        max_velocity=args.max_velocity,
        plot=args.plot,
    )

    config.validate()
    return config


def prepare_log_dir(config: BenchmarkConfig) -> Path:
    if config.clean and config.log_dir.exists():
        shutil.rmtree(config.log_dir)

    config.log_dir.mkdir(parents=True, exist_ok=True)
    (config.log_dir / "stores").mkdir(parents=True, exist_ok=True)

    return config.log_dir


def router_file_for(routing: str) -> Path:
    router_file = ROUTER_FILES[routing]

    if not router_file.exists():
        raise FileNotFoundError(f"Router file not found: {router_file}")

    return router_file


def write_metadata(config: BenchmarkConfig) -> None:
    metadata_path = config.log_dir / "benchmark_config.json"

    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2, sort_keys=True)
        f.write("\n")


def create_stations(net: Mininet_wifi, config: BenchmarkConfig):
    stations = []

    for index in range(1, config.stations + 1):
        name = f"sta{index}"
        ip = f"10.0.0.{index}/24"

        sta = net.addStation(
            name,
            ip=ip,
            range=config.node_range,
            min_x=0,
            max_x=config.area_width,
            min_y=0,
            max_y=config.area_height,
            min_v=config.min_velocity,
            max_v=config.max_velocity,
        )

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
                ssid="meshpayBenchAdhoc",
                mode="g",
                channel=5,
                ht_cap="HT40+",
            )

        elif medium == "mesh":
            net.addLink(
                sta,
                cls=mesh,
                intf=intf,
                ssid="meshpayBenchMesh",
                channel=5,
                ht_cap="HT40+",
            )


def configure_random_direction_mobility(
    net: Mininet_wifi,
    config: BenchmarkConfig,
) -> None:
    """Configure Mininet-WiFi built-in RandomDirection mobility.

    This benchmark intentionally supports only one mobility model:
    RandomDirection.

    Per-station movement limits and velocity are configured in addStation().
    The global model is enabled here.
    """

    info("*** Configuring Mininet-WiFi mobility model: RandomDirection\n")

    net.setMobilityModel(
        time=config.mobility_start,
        model="RandomDirection",
        max_x=config.area_width,
        max_y=config.area_height,
        seed=config.seed,
    )


def peer_args_for_station(stations, current_station) -> str:
    peer_args = []

    for peer in stations:
        if peer.name == current_station.name:
            continue

        peer_args.append(
            f"--peer {shlex.quote(peer.name + '=' + peer.IP())}"
        )

    return " ".join(peer_args)


def start_dtn_routers(stations, config: BenchmarkConfig, router_file: Path):
    info(f"*** Starting DTN routers: {config.routing}\n")

    processes = []

    for sta in stations:
        store = config.log_dir / "stores" / config.routing / sta.name
        log_file = config.log_dir / f"{sta.name}-{config.routing}.log"

        sta.cmd(f"rm -rf {shlex.quote(str(store))}")
        sta.cmd(f"mkdir -p {shlex.quote(str(store))}")

        peer_args = peer_args_for_station(stations, sta)

        cmd = (
            f"PYTHONPATH={shlex.quote(str(ROOT_DIR))} "
            f"python3 {shlex.quote(str(router_file))} "
            f"--node {shlex.quote(sta.name)} "
            f"--store {shlex.quote(str(store))} "
            f"{peer_args} "
            f"> {shlex.quote(str(log_file))} 2>&1"
        )

        proc = sta.popen(cmd, shell=True)
        processes.append((sta, proc))

        info(f"*** {sta.name}: router started\n")

    time.sleep(2.0)

    return processes


def stop_dtn_routers(stations, processes) -> None:
    info("*** Stopping DTN routers\n")

    for _sta, proc in processes:
        try:
            proc.terminate()
        except Exception:
            pass

    for sta in stations:
        sta.cmd("pkill -f 'dtn/epidemic.py' || true")
        sta.cmd("pkill -f 'epidemic.py' || true")


def inject_message(
    net: Mininet_wifi,
    config: BenchmarkConfig,
    router_file: Path,
    message: BenchmarkMessage,
) -> None:
    src_node = net.get(message.src)
    store = config.log_dir / "stores" / config.routing / message.src

    cmd = (
        f"PYTHONPATH={shlex.quote(str(ROOT_DIR))} "
        f"python3 {shlex.quote(str(router_file))} "
        f"--inject "
        f"--node {shlex.quote(message.src)} "
        f"--dst {shlex.quote(message.dst)} "
        f"--payload {shlex.quote(message.payload)} "
        f"--store {shlex.quote(str(store))}"
    )

    output = src_node.cmd(cmd)

    injection_log = config.log_dir / "injections.log"

    with injection_log.open("a", encoding="utf-8") as f:
        f.write(
            f"time={time.time():.6f} "
            f"seq={message.seq} "
            f"src={message.src} "
            f"dst={message.dst} "
            f"payload_size={len(message.payload.encode('utf-8'))}\n"
        )

    if output.strip():
        info(output)


def run_traffic(
    net: Mininet_wifi,
    config: BenchmarkConfig,
    router_file: Path,
) -> tuple[float, float]:
    generator = TrafficGenerator(
        stations=config.stations,
        messages=config.messages,
        message_rate=config.message_rate,
        payload_size=config.payload_size,
        seed=config.seed,
        src=config.src,
        dst=config.dst,
    )

    messages = list(generator.generate())

    info("*** Starting traffic generation\n")

    if config.src is None and config.dst is None:
        info("*** Traffic mode: random source and random destination per message\n")
    elif config.src is not None and config.dst is not None:
        info(f"*** Traffic mode: fixed {config.src} -> {config.dst}\n")
    elif config.src is not None:
        info(f"*** Traffic mode: fixed source {config.src}, random destination\n")
    elif config.dst is not None:
        info(f"*** Traffic mode: random source, fixed destination {config.dst}\n")

    started_at = time.time()

    for message in messages:
        target_time = started_at + message.scheduled_at
        now = time.time()

        if target_time > now:
            time.sleep(target_time - now)

        inject_message(
            net=net,
            config=config,
            router_file=router_file,
            message=message,
        )

    injection_finished_at = time.time()
    remaining = config.duration - (injection_finished_at - started_at)

    if remaining > 0:
        info(f"*** Waiting {remaining:.2f}s for delivery\n")
        time.sleep(remaining)

    ended_at = time.time()

    return started_at, ended_at


def print_summary(report: dict) -> None:
    summary = report["summary"]
    latency = report["latency_ms"]

    info("\n*** Benchmark summary\n")
    info(f"generated_messages:            {summary['generated_messages']}\n")
    info(f"delivered_messages:            {summary['delivered_messages']}\n")
    info(f"lost_messages:                 {summary['lost_messages']}\n")
    info(f"delivery_rate_percent:         {summary['delivery_rate_percent']:.2f}%\n")
    info(f"finality_rate_percent:         {summary['finality_rate_percent']:.2f}%\n")
    info(f"delivered_throughput_msg_s:    {summary['delivered_throughput_msg_s']:.4f}\n")
    info(f"delivered_throughput_bytes_s:  {summary['delivered_throughput_bytes_s']:.4f}\n")
    info(f"overhead_ratio:                {summary['overhead_ratio']:.4f}\n")

    if latency["avg"] is not None:
        info(f"avg_latency_ms:                {latency['avg']:.4f}\n")
        info(f"p50_latency_ms:                {latency['p50']:.4f}\n")
        info(f"p95_latency_ms:                {latency['p95']:.4f}\n")
    else:
        info("avg_latency_ms:                None\n")
        info("p50_latency_ms:                None\n")
        info("p95_latency_ms:                None\n")


def topology(config: BenchmarkConfig) -> None:
    router_file = router_file_for(config.routing)
    prepare_log_dir(config)
    write_metadata(config)

    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    info("*** Creating benchmark stations\n")
    stations = create_stations(net, config)

    info("*** Configuring propagation model\n")
    net.setPropagationModel(model="logNormalShadowing", exp=3.2, variance=2.0)

    info("*** Configuring nodes\n")
    net.configureNodes()

    info(f"*** Configuring wireless medium={config.medium}\n")
    add_wireless_links(net, stations, config.medium)

    if config.plot:
        net.plotGraph(
            max_x=int(config.area_width + 20),
            max_y=int(config.area_height + 20),
        )

    configure_random_direction_mobility(net, config)

    info("*** Building network\n")
    net.build()

    processes = []
    started_at = time.time()
    ended_at = started_at

    try:
        processes = start_dtn_routers(
            stations=stations,
            config=config,
            router_file=router_file,
        )

        started_at, ended_at = run_traffic(
            net=net,
            config=config,
            router_file=router_file,
        )

        report = collect_metrics(
            log_dir=config.log_dir,
            routing=config.routing,
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
        stop_dtn_routers(stations, processes)

        info("*** Stopping network\n")
        net.stop()


def main() -> None:
    setLogLevel("info")

    args = parse_args()
    config = build_config(args)

    topology(config)


if __name__ == "__main__":
    main()