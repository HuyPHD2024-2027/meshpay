from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Any

@dataclass
class WirelessMetrics:
    """Wireless Link & Physical Layer Metrics."""
    rssi_dbm: Optional[float] = None
    tx_bytes: int = 0
    rx_bytes: int = 0
    tx_retries: int = 0
    tx_failed: int = 0
    sinr: Optional[float] = None

@dataclass
class MobilityMetrics:
    """Node Context & Mobility Metrics."""
    position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    speed: float = 0.0
    heading: float = 0.0
    active_neighbors: List[str] = field(default_factory=list)

@dataclass
class ResourceMetrics:
    """Device Resource Metrics."""
    battery_level: Optional[float] = None
    buffer_occupancy: int = 0

@dataclass
class AppMetrics:
    """MeshPay / Application-Specific Metrics."""
    transaction_count: int = 0
    error_count: int = 0
    validation_latency_ms: float = 0.0
    reputation_score: float = 1.0

@dataclass
class TelemetryState:
    """Aggregated full node state for FRL inputs."""
    node_id: str
    timestamp: float
    wireless: WirelessMetrics
    mobility: MobilityMetrics
    resources: ResourceMetrics
    app: AppMetrics

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
        
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TelemetryState':
        return cls(
            node_id=data.get("node_id", "unknown"),
            timestamp=data.get("timestamp", 0.0),
            wireless=WirelessMetrics(**data.get("wireless", {})),
            mobility=MobilityMetrics(**data.get("mobility", {})),
            resources=ResourceMetrics(**data.get("resources", {})),
            app=AppMetrics(**data.get("app", {}))
        )
