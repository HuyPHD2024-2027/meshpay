import time
import random
import uuid
import logging

class Transaction:
    def __init__(self, tx_id=None):
        self.tx_id = tx_id or str(uuid.uuid4())
        self.start_time = time.time()
        self.quorum_time = None
        self.votes = {} # node_id: weight
        self.total_weight = 0

class ConsensusSimulator:
    """Simulates asynchronous BFT consensus with weighted voting."""
    def __init__(self, authority_nodes: dict):
        self.authorities = authority_nodes # {node_id: weight}
        self.total_authority_weight = sum(authority_nodes.values())
        self.quorum_threshold = 0.67 * self.total_authority_weight
        self.active_transactions = {}

    def broadcast_transaction(self, tx_id):
        """Simulate the start of a transaction broadcast."""
        tx = Transaction(tx_id)
        self.active_transactions[tx_id] = tx
        return tx

    def collect_vote(self, tx_id, node_id):
        """Simulate an authority node voting on a transaction."""
        if tx_id not in self.active_transactions:
            return None
        
        tx = self.active_transactions[tx_id]
        if node_id in self.authorities and node_id not in tx.votes:
            weight = self.authorities[node_id]
            tx.votes[node_id] = weight
            tx.total_weight += weight
            
            # Check for Quorum
            if tx.total_weight >= self.quorum_threshold and not tx.quorum_time:
                tx.quorum_time = time.time()
                latency = tx.quorum_time - tx.start_time
                print(f"Transaction {tx_id} reached quorum. Latency L = {latency:.3f}s")
                return latency
        return None

    def get_finality_latency(self, tx_id):
        tx = self.active_transactions.get(tx_id)
        if tx and tx.quorum_time:
            return tx.quorum_time - tx.start_time
        return None

# Example usage for integration:
# auth_weights = {'auth1': 10, 'auth2': 5, 'auth3': 5}
# sim = ConsensusSimulator(auth_weights)
# tx = sim.broadcast_transaction('tx-123')
# sim.collect_vote('tx-123', 'auth1')
# sim.collect_vote('tx-123', 'auth2') # Quorum reached if > 13.4
