import time
import json
import logging
import csv
import os
import subprocess
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

# Local project imports (will be created in the same folder)
# from performance_logger import PerformanceLogger

class SDQoSController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SDQoSController, self).__init__(*args, **kwargs)
        self.polling_interval = 5.0 # Seconds
        self.nodes = ['sta1', 'sta2', 'sta3', 'sta4']
        self.network_state = {} # Stores telemetry for each node/link
        self.logger_enabled = True
        self.start_time = time.time()
        
        # Start the telemetry polling hub
        self.monitor_thread = hub.spawn(self._monitor_loop)
        self.logger.info("SD-QoS Telemetry Module Started")

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Install table-miss flow entry
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst)
        datapath.send_msg(mod)

    def _monitor_loop(self):
        """Main loop for polling telemetry every T seconds."""
        while True:
            self._poll_telemetry()
            hub.sleep(self.polling_interval)

    def _poll_telemetry(self):
        """Extract multi-dimensional telemetry from mesh nodes."""
        for node in self.nodes:
            stats = {
                'timestamp': time.time() - self.start_time,
                'pdr': self._get_pdr(node),
                'ber': self._get_ber(node),
                'etx': self._get_etx(node),
                'ett': self._get_ett(node),
                'buffer_occupancy': self._get_buffer_occupancy(node),
                'jitter': self._get_jitter(node),
                'latency': self._get_latency(node),
                'flow_stats': self._get_flow_stats(node)
            }
            self.network_state[node] = stats
            # self.logger.info(f"Node {node} Telemetry: {stats}")
            # Log to CSV (via performance_logger helper)
            self._log_performance(node, stats)

    def _get_pdr(self, node):
        # Simulated or extracted from node counters
        return 0.98 # Placeholder

    def _get_ber(self, node):
        # Extracted from physical layer sim
        return 1e-6 # Placeholder

    def _get_etx(self, node):
        # ETX = 1 / (df * dr)
        return 1.1 # Placeholder

    def _get_ett(self, node):
        # ETT = ETX * (S / B)
        return 0.5 # Placeholder

    def _get_buffer_occupancy(self, node):
        """Extract buffer occupancy using 'tc' commands."""
        # Note: In real simulation, we'd use subprocess to run inside node namespace
        # cmd = f"ip netns exec {node} tc -s qdisc show dev {node}-wlan0"
        return 10 # Multi-level placeholder

    def _get_jitter(self, node):
        """Extract jitter via probes."""
        return 2.5 # Placeholder ms

    def _get_latency(self, node):
        """Extract E2E latency via ping."""
        return 15.0 # Placeholder ms

    def _get_flow_stats(self, node):
        """Extract flow statistics from the OpenFlow table."""
        return 100 # Packets per second placeholder

    def _log_performance(self, node, stats):
        csv_file = 'performance_metrics.csv'
        fieldnames = ['timestamp', 'node', 'pdr', 'ber', 'etx', 'ett', 'buffer_occupancy', 'jitter', 'latency', 'flow_stats']
        file_exists = os.path.isfile(csv_file)
        with open(csv_file, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            row = {'node': node}
            row.update(stats)
            writer.writerow(row)

    def calculate_disjoint_multipaths(self, src, dst):
        """Compute disjoint paths based on current telemetry (ETT weights)."""
        # Logic for path calculation goes here
        return []

    def adaptive_ttl_mechanism(self, node_density):
        """Reduce message lifetime if density >= critical threshold."""
        lambda_c = 1.5 # Critical threshold example
        if node_density >= lambda_c:
            return 2 # Reduced TTL
        return 5 # Default TTL
