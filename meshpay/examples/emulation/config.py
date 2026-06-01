"""Configuration and telemetry dataclasses for MeshPay emulation benchmarks."""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Sequence, Tuple

from meshpay.routing.registry import normalize_routing_name, supported_routing_algorithms
from meshpay.oppnet.interfaces import supported_wireless_interfaces


@dataclass(frozen=True)
class WorkloadItem:
    """Single offline payment submitted by the benchmark workload."""

    sender: str
    recipient: str
    amount: int


DEFAULT_WORKLOAD: Tuple[WorkloadItem, ...] = (
    WorkloadItem("user1", "user2", 10),
    WorkloadItem("user2", "user3", 15),
    WorkloadItem("user3", "user1", 5),
    WorkloadItem("user1", "user3", 20),
    WorkloadItem("user2", "user1", 12),
    WorkloadItem("user3", "user2", 8),
)


def default_workload() -> Tuple[WorkloadItem, ...]:
    """Return the historical benchmark workload."""

    return DEFAULT_WORKLOAD


def generate_deterministic_workload(clients: int, size: int, seed: int) -> Tuple[WorkloadItem, ...]:
    """Generate a reproducible valid transfer workload across clients."""

    if clients < 2 or size <= 0:
        return tuple()

    rng = random.Random(seed)
    names = [f"user{i}" for i in range(1, clients + 1)]
    workload = []
    for index in range(size):
        sender = names[index % len(names)]
        recipients = [name for name in names if name != sender]
        recipient = rng.choice(recipients)
        amount = rng.randint(1, 25)
        workload.append(WorkloadItem(sender, recipient, amount))
    return tuple(workload)


@dataclass(frozen=True)
class EmulationConfig:
    """Complete runtime configuration for one benchmark invocation."""

    authorities: int = 5
    clients: int = 3
    duration: int = 300
    wireless_range: int = 20
    plot: bool = False
    network_mode: str = "oppnet"
    wireless_interface: str = "mesh_80211s"
    routing: str = "both"
    policy_file: str = ""
    output_file: str = ""
    plot_output: str = ""
    random_seed: int = 42
    mobility_min_x: int = 0
    mobility_max_x: int = 200
    mobility_min_y: int = 0
    mobility_max_y: int = 150
    mobility_min_v: int = 1
    mobility_max_v: int = 3
    mobility_velocity_mean: float = 2.0
    mobility_alpha: float = 0.5
    mobility_variance: float = 0.5
    mobility_model: str = "GaussMarkov"
    peer_discovery_timeout: float = 30.0
    pending_wait_timeout: float = 60.0
    scenario_name: str = "single"
    workload_size: int = 0
    workload_seed: int = 42
    workload_interval: float = 1.5
    authority_layout: str = "uniform"
    client_layout: str = "uniform"
    experiment_id: str = ""
    campaign: str = ""
    seeds: str = "1,2,3,4,5"
    results_dir: str = "results/campaign"
    figure_format: str = "png,pdf"
    
    # Attack injection parameters
    attack_type: str = "none"
    attack_intensity: float = 0.0
    attack_target: str = "auth1"

    # Wireless channel propagation parameters
    propagation_model: str = "logNormalShadowing"
    propagation_exp: float = 3.5
    propagation_sL: float = 6.0
    
    workload: Tuple[WorkloadItem, ...] = field(default_factory=default_workload)

    def with_routing(self, routing: str) -> "EmulationConfig":
        """Return a copy with normalized routing, preserving comparison mode."""

        normalized = routing if routing in ("both", "all") else normalize_routing_name(routing)
        return replace(self, routing=normalized)


@dataclass(frozen=True)
class BenchmarkStats:
    """JSON-compatible benchmark telemetry."""

    finality_rate: float = 0.0
    avg_latency_ms: float = 0.0
    control_bytes: int = 0
    data_bytes: int = 0
    avg_buffer_size: float = 0.0
    total_tx: int = 0
    successful_tx: int = 0
    successful_transaction_ids: List[str] = field(default_factory=list)
    raw_successful_events: int = 0
    network_mode: str = "oppnet"
    wireless_interface: str = "mesh_80211s"
    routing: str = "epidemic"
    policy_file: str = ""
    scenario_name: str = ""
    experiment_id: str = ""
    seed: int = 0
    wireless_range: int = 0
    mobility_speed: str = ""
    mobility_model: str = "GaussMarkov"
    submitted_payments: int = 0
    certificate_assembly_success_rate: float = 0.0
    avg_vote_rtt_ms: float = 0.0
    avg_handoff_interruption_ms: float = 0.0
    tps: float = 0.0
    peer_discovery_events: int = 0
    contact_events: int = 0
    attack_type: str = "none"
    attack_intensity: float = 0.0
    attack_target: str = "none"
    propagation_model: str = "logNormalShadowing"
    propagation_exp: float = 3.5
    propagation_sL: float = 6.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize using the historical benchmark JSON keys."""

        payload = {
            "finality_rate": self.finality_rate,
            "avg_latency_ms": self.avg_latency_ms,
            "control_bytes": self.control_bytes,
            "data_bytes": self.data_bytes,
            "avg_buffer_size": self.avg_buffer_size,
            "total_tx": self.total_tx,
            "successful_tx": self.successful_tx,
            "successful_transaction_ids": list(self.successful_transaction_ids),
            "raw_successful_events": self.raw_successful_events,
            "network_mode": self.network_mode,
            "wireless_interface": self.wireless_interface,
            "routing": self.routing,
            "policy_file": self.policy_file,
            "scenario_name": self.scenario_name,
            "experiment_id": self.experiment_id,
            "seed": self.seed,
            "wireless_range": self.wireless_range,
            "mobility_speed": self.mobility_speed,
            "mobility_model": self.mobility_model,
            "submitted_payments": self.submitted_payments,
            "certificate_assembly_success_rate": self.certificate_assembly_success_rate,
            "avg_vote_rtt_ms": self.avg_vote_rtt_ms,
            "avg_handoff_interruption_ms": self.avg_handoff_interruption_ms,
            "tps": self.tps,
            "peer_discovery_events": self.peer_discovery_events,
            "contact_events": self.contact_events,
            "attack_type": self.attack_type,
            "attack_intensity": self.attack_intensity,
            "attack_target": self.attack_target,
            "propagation_model": self.propagation_model,
            "propagation_exp": self.propagation_exp,
            "propagation_sL": self.propagation_sL,
        }
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BenchmarkStats":
        """Build a stats object from JSON telemetry produced by older scripts."""

        return cls(
            finality_rate=data.get("finality_rate", 0.0),
            avg_latency_ms=data.get("avg_latency_ms", 0.0),
            control_bytes=data.get("control_bytes", 0),
            data_bytes=data.get("data_bytes", 0),
            avg_buffer_size=data.get("avg_buffer_size", 0.0),
            total_tx=data.get("total_tx", 0),
            successful_tx=data.get("successful_tx", 0),
            successful_transaction_ids=list(data.get("successful_transaction_ids", [])),
            raw_successful_events=data.get("raw_successful_events", 0),
            network_mode=data.get("network_mode", "oppnet"),
            wireless_interface=data.get("wireless_interface", "mesh_80211s"),
            routing=data.get("routing", "epidemic"),
            policy_file=data.get("policy_file", ""),
            scenario_name=data.get("scenario_name", ""),
            experiment_id=data.get("experiment_id", ""),
            seed=data.get("seed", 0),
            wireless_range=data.get("wireless_range", 0),
            mobility_speed=data.get("mobility_speed", ""),
            mobility_model=data.get("mobility_model", "GaussMarkov"),
            submitted_payments=data.get("submitted_payments", data.get("total_tx", 0)),
            certificate_assembly_success_rate=data.get("certificate_assembly_success_rate", 0.0),
            avg_vote_rtt_ms=data.get("avg_vote_rtt_ms", 0.0),
            avg_handoff_interruption_ms=data.get("avg_handoff_interruption_ms", 0.0),
            tps=data.get("tps", 0.0),
            peer_discovery_events=data.get("peer_discovery_events", 0),
            contact_events=data.get("contact_events", 0),
            attack_type=data.get("attack_type", "none"),
            attack_intensity=data.get("attack_intensity", 0.0),
            attack_target=data.get("attack_target", "none"),
            propagation_model=data.get("propagation_model", "logNormalShadowing"),
            propagation_exp=data.get("propagation_exp", 3.5),
            propagation_sL=data.get("propagation_sL", 6.0),
        )


@dataclass(frozen=True)
class ComparisonResult:
    """Telemetry produced by an isolated multiple-protocol comparison."""

    epidemic_stats: BenchmarkStats
    sdn_stats: BenchmarkStats
    epidemic_json: str
    sdn_json: str
    all_stats: Dict[str, BenchmarkStats] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize comparative results without changing per-run stat keys."""

        payload = {
            "epidemic": self.epidemic_stats.to_dict(),
            "sdn_dtn": self.sdn_stats.to_dict(),
            "epidemic_json": self.epidemic_json,
            "sdn_json": self.sdn_json,
        }
        if self.all_stats:
            payload["all_stats"] = {k: v.to_dict() for k, v in self.all_stats.items()}
        return payload


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
        choices=sorted(supported_routing_algorithms()) + ["both", "all"],
        default=None,
        help="Routing algorithm for a single run or comparison",
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
    parser.add_argument("--mobility-velocity-mean", type=float, default=2.0, help="Mean velocity for GaussMarkov and similar mobility models")
    parser.add_argument("--mobility-alpha", type=float, default=0.5, help="Alpha (memory) parameter for GaussMarkov model (0=random walk, 1=linear)")
    parser.add_argument("--mobility-variance", type=float, default=0.5, help="Variance parameter for GaussMarkov model")
    parser.add_argument(
        "--mobility-model",
        type=str,
        default="GaussMarkov",
        help="WiFi station mobility model name (e.g. GaussMarkov, RandomDirection, RandomWayPoint)",
    )
    parser.add_argument("--peer-discovery-timeout", type=float, default=30.0, help="Peer discovery wait timeout")
    parser.add_argument("--pending-wait-timeout", type=float, default=60.0, help="Pending transaction wait timeout")
    parser.add_argument("--scenario-name", type=str, default="single", help="Scenario label stored in telemetry")
    parser.add_argument("--workload-size", type=int, default=0, help="Generated workload size; 0 keeps the legacy workload")
    parser.add_argument("--workload-seed", type=int, default=42, help="Seed for deterministic workload generation")
    parser.add_argument("--workload-interval", type=float, default=1.5, help="Seconds between workload submissions")
    parser.add_argument("--authority-layout", type=str, choices=["uniform", "clustered", "corridor", "edge_authorities"], default="uniform")
    parser.add_argument("--client-layout", type=str, choices=["uniform", "clustered", "corridor", "edge_authorities"], default="uniform")
    parser.add_argument("--experiment-id", type=str, default="", help="Experiment identifier stored in telemetry")
    parser.add_argument("--campaign", type=str, choices=["", "disruption", "scalability", "placement", "resilience", "all"], default="", help="Run a research campaign instead of one benchmark")
    parser.add_argument("--seeds", type=str, default="1,2,3,4,5", help="Comma-separated campaign seeds")
    parser.add_argument("--results-dir", type=str, default="results/campaign", help="Campaign output directory")
    parser.add_argument("--figure-format", type=str, default="png,pdf", help="Comma-separated figure formats for campaign runs")
    
    # Attack parameters
    parser.add_argument(
        "--attack-type",
        type=str,
        choices=[
            "none",
            "jamming",   
            "grayhole",   
            "targeted_load",
            "leader_isolation",
            "transient_failure",
            "stopping",
        ],
        default="none",
        help=(
            "Type of resilience attack to inject. "
            "'jamming' floods the 802.11 channel with UDP noise (iperf3). "
            "'grayhole' selectively drops FastPay UDP certificates on target authorities (tc)."
        ),
    )
    parser.add_argument("--attack-intensity", type=float, default=0.0, help="Intensity of the resilience attack (0.0 to 1.0)")
    parser.add_argument("--attack-target", type=str, default="auth1", help="Target node identifier for the attack")

    # Wireless channel propagation parameters
    parser.add_argument(
        "--propagation-model",
        type=str,
        default="logNormalShadowing",
        help="Wireless propagation model name (e.g. logNormalShadowing, logDistance, friis, twoRayGround)",
    )
    parser.add_argument(
        "--propagation-exp",
        type=float,
        default=3.5,
        help="Path loss exponent for the propagation model",
    )
    parser.add_argument(
        "--propagation-sl",
        dest="propagation_sL",
        type=float,
        default=6.0,
        help="Systematic loss/Shadowing standard deviation for the propagation model",
    )

    return parser


def parse_args(argv: Sequence[str] | None = None) -> EmulationConfig:
    """Parse CLI arguments and normalize legacy routing names."""

    args = build_parser().parse_args(argv)
    routing = args.routing or args.routing_mode or "both"
    if routing not in ("both", "all"):
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
        mobility_velocity_mean=args.mobility_velocity_mean,
        mobility_alpha=args.mobility_alpha,
        mobility_variance=args.mobility_variance,
        mobility_model=args.mobility_model,
        peer_discovery_timeout=args.peer_discovery_timeout,
        pending_wait_timeout=args.pending_wait_timeout,
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
        attack_type=args.attack_type,
        attack_intensity=args.attack_intensity,
        attack_target=args.attack_target,
        propagation_model=args.propagation_model,
        propagation_exp=args.propagation_exp,
        propagation_sL=args.propagation_sL,
        **({"workload": workload} if workload is not None else {}),
    )
