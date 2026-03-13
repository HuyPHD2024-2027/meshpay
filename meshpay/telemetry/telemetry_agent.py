import json
import threading
import time
import socket
import subprocess
import traceback
from typing import Any, Dict, Optional, List, TYPE_CHECKING

from meshpay.telemetry.telemetry_metrics import (
    TelemetryState, WirelessMetrics, MobilityMetrics, ResourceMetrics, AppMetrics
)

if TYPE_CHECKING:
    from mn_wifi.node import Node_wifi

class TelemetryDaemon:
    """Daemon that gathers metrics from a Mininet-WiFi node.
    
    Supports both independent UDP broadcast and 'piggybacking' where 
    routing protocols pull the latest state to include in their own packets.
    """

    def __init__(
        self,
        node: Any, # Node_wifi
        interval: float = 5.0,
        enable_broadcast: bool = True,
        aggregator_ip: str = "127.0.0.1"
    ) -> None:
        self.node = node
        self.interval = interval
        self.node_id = getattr(node, "name", "unknown")
        self.running = False
        self.enable_broadcast = enable_broadcast
        self.aggregator_ip = aggregator_ip
        self._latest_state: Optional[TelemetryState] = None
        self.thread: Optional[threading.Thread] = None
        self.udp_port = 5005

    def get_latest_state(self) -> Optional[TelemetryState]:
        """Returns the most recent metrics gathered by the daemon."""
        return self._latest_state

    def start(self) -> None:
        """Start the telemetry gathering loop."""
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """Stop the telemetry gathering loop."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)

    def _gather_metrics(self) -> TelemetryState:
        """Gather all physical, logical, and application metrics from the node."""
        return TelemetryState(
            node_id=self.node_id,
            timestamp=time.time(),
            wireless=self._gather_wireless(),
            mobility=self._gather_mobility(),
            resources=self._gather_resources(),
            app=self._gather_app()
        )

    def _gather_wireless(self) -> WirelessMetrics:
        """Gather link-layer and physical wireless metrics."""
        stats = {}
        if hasattr(self.node, "get_link_stats"):
            stats = self.node.get_link_stats()
            
        return WirelessMetrics(
            rssi_dbm=stats.get("signal"),
            tx_bytes=stats.get("tx_bytes", 0),
            rx_bytes=stats.get("rx_bytes", 0),
            tx_retries=stats.get("tx_retries", 0),
            tx_failed=stats.get("tx_failed", 0),
            sinr=stats.get("sinr")
        )

    def _gather_mobility(self) -> MobilityMetrics:
        """Gather spatial and neighbor discovery metrics."""
        pos_raw = getattr(self.node, "position", (0.0, 0.0, 0.0))
        try:
            pos = (float(pos_raw[0]), float(pos_raw[1]), float(pos_raw[2]))
        except (IndexError, TypeError, ValueError):
            pos = (0.0, 0.0, 0.0)
            
        speed = getattr(self.node, "speed", getattr(self.node, "velocity", 0.0))
        active_neighbors = []
        if hasattr(self.node, "get_encounter_history"):
            active_neighbors = self.node.get_encounter_history()
            
        return MobilityMetrics(
            position=pos,
            speed=float(speed),
            heading=0.0,
            active_neighbors=active_neighbors
        )

    def _gather_resources(self) -> ResourceMetrics:
        """Gather hardware and device resource metrics."""
        buffer_occ = 0
        if hasattr(self.node, "get_buffer_occupancy"):
            buffer_occ = self.node.get_buffer_occupancy()
        
        battery = None
        if hasattr(self.node, "params") and "battery" in self.node.params:
            try:
                battery = float(self.node.params["battery"])
            except (ValueError, TypeError):
                pass
            
        return ResourceMetrics(
            battery_level=battery,
            buffer_occupancy=buffer_occ
        )

    def _gather_app(self) -> AppMetrics:
        """Gather MeshPay application-layer performance metrics."""
        p_metrics = getattr(self.node, "performance_metrics", None)
        if not p_metrics:
            return AppMetrics()
            
        stats = p_metrics.get_stats()
        
        # Calculate protocol overhead ratio from MeshMixin counters
        overhead_ratio = 0.0
        ctrl = getattr(self.node, "control_bytes_sent", 0)
        data = getattr(self.node, "data_bytes_sent", 0)
        if (ctrl + data) > 0:
            overhead_ratio = ctrl / (ctrl + data)

        return AppMetrics(
            transaction_count=stats.get("transaction_count", 0),
            successful_transaction_count=stats.get("successful_transaction_count", 0),
            error_count=stats.get("error_count", 0),
            validation_latency_ms=stats.get("validation_latency_ms", 0.0),
            average_e2e_latency_ms=stats.get("average_e2e_latency_ms", 0.0),
            tps=stats.get("tps", 0.0),
            protocol_overhead_ratio=overhead_ratio,
            reputation_score=stats.get("reputation_score", 1.0)
        )

    def _run(self) -> None:
        """Periodically gather and optionally publish metrics."""
        while self.running:
            try:
                state = self._gather_metrics()
                self._latest_state = state
                
                # Always report to the configured aggregator
                self._send_telemetry(state, self.aggregator_ip)

                if self.enable_broadcast:
                    self._send_telemetry(state, "10.255.255.255", broadcast=True)
                    
            except Exception as e:
                print(f"[TelemetryDaemon {self.node_id}] Run error: {e}\n{traceback.format_exc()}")
                
            time.sleep(self.interval)

    def _send_telemetry(self, state: TelemetryState, ip: str, broadcast: bool = False) -> None:
        """Send telemetry state via UDP (handles namespace isolation via popen)."""
        try:
            payload = json.dumps(state.to_dict())
            
            # Encapsulate UDP send in a shell command to ensure it runs in node namespace
            # but uses host NAT if needed (via the node's routing table)
            py_cmd = (
                "import sys, socket; "
                "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
                f"{'s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1); ' if broadcast else ''}"
                f"s.sendto(sys.stdin.read().encode(), ('{ip}', {self.udp_port}))"
            )
            
            if hasattr(self.node, "popen"):
                # Prefer popen for namespace-aware execution
                proc = self.node.popen(
                    ["python3", "-c", py_cmd], 
                    stdin=subprocess.PIPE, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE, 
                    text=True
                )
                _, err = proc.communicate(input=payload, timeout=1.5)
                if proc.returncode != 0 and err:
                    print(f"[{self.node_id}] Telemetry send to {ip} failed: {err.strip()}")
            else:
                # Direct socket as fallback
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    if broadcast:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.sendto(payload.encode(), (ip, self.udp_port))
        except Exception as e:
            print(f"[TelemetryDaemon {self.node_id}] Send error (target={ip}): {e}")
