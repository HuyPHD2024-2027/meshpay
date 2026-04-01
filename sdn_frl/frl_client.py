import flwr as fl
import numpy as np
import time
import random
import argparse
from typing import Dict, List, Tuple

class RLAgent:
    """RL Agent for optimizing the Infection Rate beta(t)."""
    def __init__(self, state_dim=9, action_dim=5):
        self.state_dim = state_dim
        self.action_dim = action_dim
        # Simplified linear weights for FRL aggregation
        self.weights = np.random.randn(state_dim, action_dim)
        self.learning_rate = 0.01

    def get_action(self, state: np.ndarray) -> int:
        """Select action beta(t) index using e-greedy policy."""
        q_values = np.dot(state, self.weights)
        return np.argmax(q_values)

    def update(self, state: np.ndarray, action: int, reward: float, next_state: np.ndarray):
        """Update weights based on experience."""
        # Policy gradient or Q-update simplified for aggregation
        target = reward + 0.9 * np.max(np.dot(next_state, self.weights))
        prediction = np.dot(state, self.weights)[action]
        error = target - prediction
        self.weights[:, action] += self.learning_rate * error * state

class MeshFRLClient(fl.client.NumPyClient):
    """Flower Client that trains the local RL agent using telemetry states."""
    def __init__(self, node_id: str, server_addr: str):
        self.node_id = node_id
        self.agent = RLAgent()
        self.server_addr = server_addr
        self.current_state = np.zeros(9)
        self.omega1 = 0.5 # Latency weight
        self.omega2 = 0.5 # Overhead weight

    def get_parameters(self, config):
        return [self.agent.weights]

    def fit(self, parameters, config):
        # Update local weights with aggregated global weights
        self.agent.weights = parameters[0]
        
        # Simulate local training round
        for _ in range(10): # 10 steps of mini-batch
            state = self._get_local_telemetry()
            action = self.agent.get_action(state)
            # Send action beta(t) to dissemination engine
            self._apply_infection_rate(action)
            
            # Wait for feedback (Reward)
            time.sleep(0.5)
            latency, overhead = self._get_performance_feedback()
            reward = -(self.omega1 * latency + self.omega2 * overhead)
            
            next_state = self._get_local_telemetry()
            self.agent.update(state, action, reward, next_state)
            
        return self.agent.weights, 10, {} # weights, num_samples, metrics

    def filter_metrics(self, parameters, config):
        return self.agent.weights, 1.0, {}

    def _get_local_telemetry(self) -> np.ndarray:
        """Poll telemetry from local node's perspective."""
        # PDR, BER, buffer, node_density, ETX, ETT, jitter, latency, flow_stats
        # For simulation, we generate based on real node logs or placeholders
        return np.random.rand(9)

    def _apply_infection_rate(self, action_idx: int):
        """Adjust dissemination rate in routing protocol."""
        beta_values = [0.1, 0.3, 0.5, 0.7, 0.9]
        beta = beta_values[action_idx]
        print(f"[{self.node_id}] Applying Infection Rate beta={beta}")

    def _get_performance_feedback(self) -> Tuple[float, float]:
        """Feedback from latency L and overhead Omega."""
        # Simulated performance
        return random.uniform(10, 50), random.uniform(1, 5)

def main():
    parser = argparse.ArgumentParser(description="Mesh FRL Client")
    parser.add_argument("--node", type=str, required=True, help="Node ID")
    parser.add_argument("--server", type=str, default="127.0.0.1:8080", help="Flower server addr")
    args = parser.parse_args()

    client = MeshFRLClient(args.node, args.server)
    print(f"Starting FRL Client for node {args.node}...")
    fl.client.start_numpy_client(server_address=args.server, client=client)

if __name__ == "__main__":
    main()
