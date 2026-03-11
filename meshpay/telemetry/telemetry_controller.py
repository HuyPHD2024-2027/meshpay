import json
import threading
import subprocess
import time
from typing import Any, Dict, Optional

from meshpay.telemetry.telemetry_metrics import TelemetryState

class TelemetryAggregator:
    """Aggregates telemetry from all nodes via MQTT subscriptions.
    
    Can run on an Authority node or a standalone Controller.
    """

    def __init__(self, udp_port: int = 5005, node: Any = None) -> None:
        self.udp_port = udp_port
        self.node = node
        self.global_state: Dict[str, TelemetryState] = {}
        self._process: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        """Start listening to MQTT topics."""
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop listening."""
        self._running = False
        if self._process:
            self._process.terminate()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _listen_loop(self) -> None:
        """Listen to UDP telemetry broadcasts from the network."""
        # Note: We use exec(sys.stdin.read()) to allow a clean multi-line listener script
        listener_script = (
            "import socket, sys\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
            "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            f"s.bind(('', {self.udp_port}))\n"
            "while True:\n"
            "  data, addr = s.recvfrom(65535)\n"
            "  sys.stdout.write(data.decode() + '\\n')\n"
        )
        cmd = ["python3", "-u", "-c", "import sys; exec(sys.stdin.read())"]
        
        try:
            if self.node and hasattr(self.node, 'popen'):
                self._process = self.node.popen(
                    cmd, 
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE,
                    text=True
                )
                self._process.stdin.write(listener_script)
                self._process.stdin.close()
            else:
                self._process = subprocess.Popen(
                    cmd, 
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE,
                    text=True
                )
                self._process.stdin.write(listener_script)
                self._process.stdin.close()
            
            while self._running:
                # Check for errors in stderr
                if self._process.poll() is not None:
                    err = self._process.stderr.read()
                    if err:
                        print(f"TelemetryAggregator Listener crashed: {err.strip()}")
                    break

                line = self._process.stdout.readline()
                if line:
                    self._handle_message(line)
                else:
                    time.sleep(0.1)
        except Exception as e:
            print(f"TelemetryAggregator Error: {e}")

    def _handle_message(self, message: str) -> None:
        """Parse incoming JSON telemetry and update global state."""
        try:
            data = json.loads(message.strip())
            state = TelemetryState.from_dict(data)
            self.global_state[state.node_id] = state
        except Exception as e:
            # print(f"TelemetryAggregator: Failed to parse message: {e}")
            pass

    def get_network_state(self) -> Dict[str, TelemetryState]:
        """Return the aggregated state of the network."""
        return self.global_state
