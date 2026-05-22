"""CLI argument handling for MeshPay emulation benchmarks."""

from __future__ import annotations

import argparse
from typing import Sequence

from meshpay.examples.emulation.config import EmulationConfig
from meshpay.examples.emulation.workload import generate_deterministic_workload
from meshpay.oppnet.interfaces import supported_wireless_interfaces
from meshpay.routing.registry import normalize_routing_name, supported_routing_algorithms


def build_parser() -> argparse.ArgumentParser:
    """Create the backward-compatible benchmark argument parser."""

    parser = argparse.ArgumentParser(description="MeshPay SDN-DTN vs Epidemic Benchmark.")
    parser.add_argument("--authorities", type=int, default=5, help="Number of authority nodes")
    parser.add_argument("--clients", type=int, default=3, help="Number of client nodes")
    parser.add_argument("--duration", type=int, default=300, help="Simulation duration per run (seconds)")
    parser.add_argument("--wireless-range", type=int, default=15, help="Wireless range for each station")
    parser.add_argument("--plot", action="store_true", help="Enable Mininet-WiFi graphical topology plotting")
    parser.add_argument("--network-mode", type=str, choices=["oppnet"], default="oppnet", help="Network abstraction profile")
    parser.add_argument(
        "--wireless-interface",
        type=str,
        choices=sorted(supported_wireless_interfaces()),
        default="mesh_80211s",
        help="Wireless interface profile",
    )
    parser.add_argument(
        "--routing-mode",
        type=str,
        choices=["epidemic", "sdn", "sdn_dtn", "spray_and_wait", "prophet", "both"],
        default=None,
        help="Routing protocol run mode; kept for backward compatibility",
    )
    parser.add_argument(
        "--routing",
        type=str,
        choices=sorted(supported_routing_algorithms()),
        default=None,
        help="Routing algorithm for a single run",
    )
    parser.add_argument("--policy-file", type=str, default="", help="Signed SDN policy template JSON/YAML path")
    parser.add_argument("--output-file", type=str, default="", help="Save statistics to this JSON path")
    parser.add_argument("--plot-output", type=str, default="", help="Save comparison plot to this path")
    parser.add_argument("--random-seed", type=int, default=42, help="Random seed for reproducible mobility")
    parser.add_argument("--mobility-min-x", type=int, default=0, help="Minimum client mobility X coordinate")
    parser.add_argument("--mobility-max-x", type=int, default=200, help="Maximum client mobility X coordinate")
    parser.add_argument("--mobility-min-y", type=int, default=0, help="Minimum client mobility Y coordinate")
    parser.add_argument("--mobility-max-y", type=int, default=150, help="Maximum client mobility Y coordinate")
    parser.add_argument("--mobility-min-v", type=int, default=1, help="Minimum client mobility velocity")
    parser.add_argument("--mobility-max-v", type=int, default=3, help="Maximum client mobility velocity")
    parser.add_argument("--peer-discovery-timeout", type=float, default=30.0, help="Peer discovery wait timeout")
    parser.add_argument("--scenario-name", type=str, default="single", help="Scenario label stored in telemetry")
    parser.add_argument("--workload-size", type=int, default=0, help="Generated workload size; 0 keeps the legacy workload")
    parser.add_argument("--workload-seed", type=int, default=42, help="Seed for deterministic workload generation")
    parser.add_argument("--workload-interval", type=float, default=1.5, help="Seconds between workload submissions")
    parser.add_argument("--authority-layout", type=str, choices=["uniform", "clustered", "corridor", "edge_authorities"], default="uniform")
    parser.add_argument("--client-layout", type=str, choices=["uniform", "clustered", "corridor", "edge_authorities"], default="uniform")
    parser.add_argument("--experiment-id", type=str, default="", help="Experiment identifier stored in telemetry")
    parser.add_argument("--campaign", type=str, choices=["", "disruption", "scalability", "placement", "all"], default="", help="Run a research campaign instead of one benchmark")
    parser.add_argument("--seeds", type=str, default="1,2,3,4,5", help="Comma-separated campaign seeds")
    parser.add_argument("--results-dir", type=str, default="results/campaign", help="Campaign output directory")
    parser.add_argument("--figure-format", type=str, default="png,pdf", help="Comma-separated figure formats for campaign runs")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> EmulationConfig:
    """Parse CLI arguments and normalize legacy routing names."""

    args = build_parser().parse_args(argv)
    routing = args.routing or args.routing_mode or "both"
    if routing != "both":
        routing = normalize_routing_name(routing)

    workload = generate_deterministic_workload(args.clients, args.workload_size, args.workload_seed) if args.workload_size else None

    return EmulationConfig(
        authorities=args.authorities,
        clients=args.clients,
        duration=args.duration,
        wireless_range=args.wireless_range,
        plot=args.plot,
        network_mode=args.network_mode,
        wireless_interface=args.wireless_interface,
        routing=routing,
        policy_file=args.policy_file,
        output_file=args.output_file,
        plot_output=args.plot_output,
        random_seed=args.random_seed,
        mobility_min_x=args.mobility_min_x,
        mobility_max_x=args.mobility_max_x,
        mobility_min_y=args.mobility_min_y,
        mobility_max_y=args.mobility_max_y,
        mobility_min_v=args.mobility_min_v,
        mobility_max_v=args.mobility_max_v,
        peer_discovery_timeout=args.peer_discovery_timeout,
        scenario_name=args.scenario_name,
        workload_size=args.workload_size,
        workload_seed=args.workload_seed,
        workload_interval=args.workload_interval,
        authority_layout=args.authority_layout,
        client_layout=args.client_layout,
        experiment_id=args.experiment_id,
        campaign=args.campaign,
        seeds=args.seeds,
        results_dir=args.results_dir,
        figure_format=args.figure_format,
        **({"workload": workload} if workload is not None else {}),
    )

