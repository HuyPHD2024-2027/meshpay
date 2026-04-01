import csv
import os
import time

class PerformanceLogger:
    """Utility to log mesh network performance metrics to CSV."""
    def __init__(self, filename="performance_metrics.csv"):
        self.filename = filename
        self.fieldnames = [
            'timestamp', 'node', 'pdr', 'ber', 'etx', 'ett', 
            'buffer_occupancy', 'jitter', 'latency', 'flow_stats', 'overhead'
        ]
        self._init_file()

    def _init_file(self):
        if not os.path.exists(self.filename):
            with open(self.filename, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                writer.writeheader()

    def log(self, node_id: str, stats: dict):
        """Append a new row of statistics for a given node."""
        with open(self.filename, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=self.fieldnames)
            row = {'node': node_id, 'timestamp': round(time.time(), 3)}
            # Update row with provided stats, ensuring only valid fields are used
            for field in self.fieldnames:
                if field in stats:
                    row[field] = stats[field]
            writer.writerow(row)

# Example:
# logger = PerformanceLogger()
# logger.log('sta1', {'pdr': 0.99, 'ber': 1e-7, 'latency': 12.5})
