"""Configuration and telemetry dataclasses for MeshPay emulation benchmarks."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Tuple

from meshpay.routing.registry import normalize_routing_name


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


@dataclass(frozen=True)
class EmulationConfig:
    """Complete runtime configuration for one benchmark invocation."""

    authorities: int = 5
    clients: int = 3
    duration: int = 300
    wireless_range: int = 15
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
    mobility_velocity_mean: int = 1
    mobility_alpha: float = 0.5
    mobility_variance: float = 0.1
    peer_discovery_timeout: float = 30.0
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
    workload: Tuple[WorkloadItem, ...] = field(default_factory=default_workload)

    def with_routing(self, routing: str) -> "EmulationConfig":
        """Return a copy with normalized routing, preserving comparison mode."""

        normalized = routing if routing == "both" else normalize_routing_name(routing)
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
    submitted_payments: int = 0
    certificate_assembly_success_rate: float = 0.0
    avg_vote_rtt_ms: float = 0.0
    avg_handoff_interruption_ms: float = 0.0
    tps: float = 0.0
    peer_discovery_events: int = 0
    contact_events: int = 0

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
        }
        payload.update(
            {
                "scenario_name": self.scenario_name,
                "experiment_id": self.experiment_id,
                "seed": self.seed,
                "wireless_range": self.wireless_range,
                "mobility_speed": self.mobility_speed,
                "submitted_payments": self.submitted_payments,
                "certificate_assembly_success_rate": self.certificate_assembly_success_rate,
                "avg_vote_rtt_ms": self.avg_vote_rtt_ms,
                "avg_handoff_interruption_ms": self.avg_handoff_interruption_ms,
                "tps": self.tps,
                "peer_discovery_events": self.peer_discovery_events,
                "contact_events": self.contact_events,
            }
        )
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
            submitted_payments=data.get("submitted_payments", data.get("total_tx", 0)),
            certificate_assembly_success_rate=data.get("certificate_assembly_success_rate", 0.0),
            avg_vote_rtt_ms=data.get("avg_vote_rtt_ms", 0.0),
            avg_handoff_interruption_ms=data.get("avg_handoff_interruption_ms", 0.0),
            tps=data.get("tps", 0.0),
            peer_discovery_events=data.get("peer_discovery_events", 0),
            contact_events=data.get("contact_events", 0),
        )


@dataclass(frozen=True)
class ComparisonResult:
    """Telemetry produced by an isolated epidemic vs SDN-DTN comparison."""

    epidemic_stats: BenchmarkStats
    sdn_stats: BenchmarkStats
    epidemic_json: str
    sdn_json: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize both comparison arms without changing per-run stat keys."""

        return {
            "epidemic": self.epidemic_stats.to_dict(),
            "sdn_dtn": self.sdn_stats.to_dict(),
            "epidemic_json": self.epidemic_json,
            "sdn_json": self.sdn_json,
        }

