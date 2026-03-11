import json
import threading
import time
from typing import Any, Dict


class TelemetryDaemon:
    """Daemon that gathers metrics from a Mininet-WiFi node and publishes via MQTT."""

    def __init__(
        self,
        node: Any,
        udp_port: int = 5005,
        interval: float = 2.0,
    ) -> None:
        self.node = node
        self.udp_port = udp_port
        self.interval = interval
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the telemetry reporting loop."""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the telemetry reporting loop."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _gather_metrics(self) -> Dict[str, Any]:
        """Gather all physical, logical, and application metrics from the node."""
        from meshpay.telemetry.telemetry_metrics import (
            TelemetryState, WirelessMetrics, MobilityMetrics, ResourceMetrics, AppMetrics
        )
        
        node_id = getattr(self.node, "name", "unknown")
        
        # 1. Wireless Link & Physical Layer Metrics
        w_stats = {}
        if hasattr(self.node, "get_link_stats"):
            w_stats = self.node.get_link_stats()
            
        wireless = WirelessMetrics(
            rssi_dbm=w_stats.get("signal"),
            tx_bytes=w_stats.get("tx_bytes", 0),
            rx_bytes=w_stats.get("rx_bytes", 0),
            tx_retries=w_stats.get("tx_retries", 0),
            tx_failed=w_stats.get("tx_failed", 0),
            sinr=w_stats.get("sinr")
        )

        # 2. Node Context & Mobility Metrics
        pos_raw = getattr(self.node, "position", (0.0, 0.0, 0.0))
        try:
            pos = (float(pos_raw[0]), float(pos_raw[1]), float(pos_raw[2]))
        except (IndexError, TypeError, ValueError):
            pos = (0.0, 0.0, 0.0)
            
        speed = getattr(self.node, "speed", getattr(self.node, "velocity", 0.0))
        active_neighbors = []
        if hasattr(self.node, "get_encounter_history"):
            active_neighbors = self.node.get_encounter_history()
            
        mobility = MobilityMetrics(
            position=pos,
            speed=float(speed),
            heading=0.0,
            active_neighbors=active_neighbors
        )

        # 3. Device Resource Metrics
        buffer_occupancy = 0
        if hasattr(self.node, "get_buffer_occupancy"):
            buffer_occupancy = self.node.get_buffer_occupancy()
        
        battery = None
        if hasattr(self.node, "params") and "battery" in self.node.params:
            try:
                battery = float(self.node.params["battery"])
            except (ValueError, TypeError):
                pass
            
        resources = ResourceMetrics(
            battery_level=battery,
            buffer_occupancy=buffer_occupancy
        )

        # 4. MeshPay / Application-Specific Metrics
        app = AppMetrics()
        if hasattr(self.node, "performance_metrics"):
            p_stats = self.node.performance_metrics.get_stats()
            app = AppMetrics(
                transaction_count=p_stats.get("transaction_count", 0),
                error_count=p_stats.get("error_count", 0),
                validation_latency_ms=p_stats.get("validation_latency_ms", 0.0),
                reputation_score=p_stats.get("reputation_score", 1.0)
            )

        state = TelemetryState(
            node_id=node_id,
            timestamp=time.time(),
            wireless=wireless,
            mobility=mobility,
            resources=resources,
            app=app
        )
            
        return state.to_dict()

    def _run_loop(self) -> None:
        """Periodically gather and publish metrics."""
        while self._running:
            try:
                metrics = self._gather_metrics()
                payload = json.dumps(metrics)
                # Use Python inline script to broadcast UDP datagram natively from the node's namespace
                import subprocess
                cmd = [
                    "python3", "-c",
                    "import sys, socket; "
                    "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
                    "s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1); "
                    f"s.sendto(sys.stdin.read().encode(), ('10.255.255.255', {self.udp_port}))"
                ]
                if hasattr(self.node, "popen"):
                    proc = self.node.popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    try:
                        out, err = proc.communicate(input=payload, timeout=1.0)
                        if proc.returncode != 0:
                            print(f"[{getattr(self.node, 'name', 'unknown')}] UDP broadcast failed: {err.strip()}")
                    except subprocess.TimeoutExpired:
                        proc.kill()
                else:
                    print(f"[{getattr(self.node, 'name', 'unknown')}] Warning: Cannot publish telemetry, missing popen method.")
                    
            except Exception as e:
                import traceback
                print(f"[{getattr(self.node, 'name', 'unknown')}] Telemetry gathering failed: {e}\n{traceback.format_exc()}")
                
            time.sleep(self.interval)
