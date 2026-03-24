#!/usr/bin/env python3
"""Flash-Mesh Automated Emulation Benchmark.

This script runs an automated load test on the MeshPay architecture.
It spins up the mesh network, creates background threads to generate
transaction load at a specified TPS, and then collects and saves KPIs
(Finality, Vote RTT, Security Drops, BCB Queue stats) to a CSV file.

Run with root privileges:
    sudo python3 -m meshpay.examples.emulation_benchmark --authorities 3 --clients 3 --flashmesh --tps 2 --duration 30
"""

import sys
import os
# Ensure the project root is in the path so we can import 'meshpay'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import argparse
import csv
import logging
import random
import threading
import time
from typing import List, Optional

from mininet.log import info, setLogLevel
from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client1 import Client
from meshpay.cli_fastpay import MeshPayCLI
from meshpay.examples.meshpay_demo import create_mesh_network, setup_test_accounts, configure_internet_access
from meshpay.controller import QoSManager, LinkStatsCollector, FallbackProfile
from mn_wifi.examples.demoCommon import (
    open_xterms as _open_xterms,
    close_xterms as _close_xterms,
)

def parse_benchmark_args():
    parser = argparse.ArgumentParser(description="MeshPay Emulation Benchmark")
    parser.add_argument("-a", "--authorities", type=int, default=3, help="number of authorities")
    parser.add_argument("-c", "--clients", type=int, default=3, help="number of client stations")
    parser.add_argument("--internet", action="store_true", help="enable internet gateway bridge")
    parser.add_argument("--gateway-port", type=int, default=8080)
    parser.add_argument("--mesh-id", type=str, default="fastpay-mesh")
    parser.add_argument("--mobility", action="store_true", help="enable advanced mobility models")
    parser.add_argument("--no-security", action="store_true", help="disable mesh security")
    parser.add_argument("--flashmesh", action="store_true", help="enable Flash-Mesh D-SDN controller")
    parser.add_argument("--duration", type=int, default=30, help="Duration of the benchmark in seconds")
    parser.add_argument("--csv", type=str, default="benchmark_results.csv", help="Output CSV file path")
    parser.add_argument("-p", "--plot", action="store_true", help="enable plot")
    parser.add_argument("--cli", action="store_true", help="Drop into interactive CLI for debugging")
    parser.add_argument("-l", "--logs", action="store_true", help="Open xterm windows for all nodes")
    parser.add_argument("-r", "--range", type=int, default=50, help="Wireless range of nodes (meters)")
    return parser.parse_args()


def load_generator(client: Client, clients: List[Client], duration: int, stop_event: threading.Event):
    """Generate transactions sequentially: send, wait for confirmation, repeat."""
    
    end_time = time.time() + duration
    
    # Wait for the network to fully settle before blasting packets
    time.sleep(2.0)
    
    while time.time() < end_time and not stop_event.is_set():
        # Pick a random recipient
        possible_recipients = [c for c in clients if c != client]
        if not possible_recipients:
            break
            
        recipient = random.choice(possible_recipients)
        try:
            # Initiate transfer
            # client1.py signature: transfer(recipient: str, token_address: str, amount: int)
            status = client.transfer(recipient.name, "XTZ", 1)
            
            if hasattr(status, 'value') and status.value != "buffered":
                info(f"[{client.name}] Transfer not buffered: {status}\n")
                # Wait a bit if it failed to buffer (throttle)
                time.sleep(1.0)
                continue

            # Wait for confirmation (finalization)
  
            while client.state.pending_transfer is not None and not stop_event.is_set():
                # Smaller poll interval for responsiveness
                time.sleep(0.1)
                
        except Exception as e:
            info(f"[{client.name}] Transfer error: {e}\n")
            time.sleep(1.0)


def gather_and_save_metrics(authorities: List[WiFiAuthority], clients: List[Client], link_stats: Optional[LinkStatsCollector], csv_path: str):
    """Collect KPIs and dump to CSV and stdout."""
    results = []
    
    info(f"\n{'='*60}\n")
    info(f"📊 BENCHMARK RESULTS\n")
    info(f"{'='*60}\n")
    
    for auth in authorities:
        stats = auth.performance_metrics.get_stats()
        row = {
            "node_type": "authority",
            "node_name": auth.name,
            "tx_count": stats.get("transaction_count", 0),
            "success_count": stats.get("successful_transaction_count", 0),
            "error_count": stats.get("error_count", 0),
            "finality_latency_ms": stats.get("average_e2e_latency_ms", 0),
            "vote_rtt_avg_ms": "",
            "cert_assembly_success_rate": "",
            "handoff_interruption_ms_avg": "",
            "replay_drops": stats.get("replay_drop_count", 0),
            "duplicate_nonce_drops": stats.get("duplicate_nonce_drop_count", 0),
            "bcb_dropped": 0,
            "bcb_overlimits": 0
        }
        if link_stats:
            ls = link_stats.get(auth.name)
            if ls:
                row["bcb_dropped"] = ls.bcb_dropped
                row["bcb_overlimits"] = ls.bcb_overlimits
        results.append(row)
        
        info(f"[{auth.name}] Finality Latency (avg): {row['finality_latency_ms']:.2f}ms | Drops (Replay: {row['replay_drops']}, Dup: {row['duplicate_nonce_drops']}) | BCB Drops: {row['bcb_dropped']}\n")

    for client in clients:
        stats = client.performance_metrics.get_stats()
        row = {
            "node_type": "client",
            "node_name": client.name,
            "tx_count": stats.get("transaction_count", 0),
            "success_count": stats.get("successful_transaction_count", 0),
            "error_count": stats.get("error_count", 0),
            "finality_latency_ms": "",
            "vote_rtt_avg_ms": stats.get("vote_rtt_avg_ms", 0),
            "cert_assembly_success_rate": stats.get("cert_assembly_success_rate", 0),
            "handoff_interruption_ms_avg": stats.get("handoff_interruption_ms_avg", 0),
            "replay_drops": "",
            "duplicate_nonce_drops": "",
            "bcb_dropped": 0,
            "bcb_overlimits": 0
        }
        if link_stats:
            ls = link_stats.get(client.name)
            if ls:
                row["bcb_dropped"] = ls.bcb_dropped
                row["bcb_overlimits"] = ls.bcb_overlimits
        results.append(row)
        
        info(f"[{client.name}] Vote RTT (avg): {row['vote_rtt_avg_ms']:.2f}ms | Cert Success: {row['cert_assembly_success_rate']:.2%} | Handoff Interrupt (avg): {row['handoff_interruption_ms_avg']:.2f}ms\n")

    info(f"\n💾 Saving detailed metrics to {csv_path}...\n")
    try:
        keys = results[0].keys()
        with open(csv_path, 'w', newline='') as output_file:
            dict_writer = csv.DictWriter(output_file, keys)
            dict_writer.writeheader()
            dict_writer.writerows(results)
    except Exception as e:
        info(f"❌ Failed to save CSV: {e}\n")


def main() -> None:
    args = parse_benchmark_args()
    setLogLevel("info")
    
    info(f"🚀 Flash-Mesh Automated Emulation Benchmark\n")
    info(f"   Authorities: {args.authorities}\n")
    info(f"   Clients: {args.clients}\n")
    info(f"   Duration: {args.duration}s\n")
    info(f"   Target Load: Sequential (send-wait-confirm)\n")
    info(f"   SDN Controller: {'Enabled' if args.flashmesh else 'Disabled'}\n")
    
    net = None
    bridge = None
    qos_mgr = None
    link_stats = None
    fallback = None
    stop_event = threading.Event()
    threads = []
    
    try:
        net, authorities, clients, gateway, bridge = create_mesh_network(
            num_authorities=args.authorities,
            num_clients=args.clients,
            mesh_id=args.mesh_id,
            enable_mobility=args.mobility,
            enable_plot=args.plot,
            enable_internet=args.internet,
            gateway_port=args.gateway_port,
            wireless_range=args.range
        )
        
        info("*** Building enhanced mesh network\n")
        net.build()
        
        if args.internet and bridge:
            configure_internet_access(net, authorities, gateway, bridge)
            
        info("*** Starting FastPay services on all nodes\n")
        for auth in authorities:
            auth.start_fastpay_services(args.internet)
        
        setup_test_accounts(authorities, clients)

        for client in clients:
            client.start_fastpay_services()
            
        if gateway:
            gateway.start_gateway_services()
            
        info("*** Waiting for mesh network to stabilize\n")
        time.sleep(5)

        if args.flashmesh:
            info("*** Enabling Flash-Mesh D-SDN controller\n")
            qos_mgr = QoSManager()
            all_nodes = list(authorities) + list(clients)
            for node in all_nodes:
                qos_mgr.install_priority(node)
                
            link_stats = LinkStatsCollector(all_nodes, interval_ms=500, qos_mgr=qos_mgr)
            link_stats.start()
            
            fallback = FallbackProfile()
            fallback.set_managed_nodes(all_nodes)
        
        if args.logs:
            _open_xterms(authorities, clients)
            
        # ==================== BENCHMARK STARTED ====================
        info(f"*** Starting automated load generation (Sequential per client for {args.duration}s)\n")
        for client in clients:
            t = threading.Thread(target=load_generator, args=(client, clients, args.duration, stop_event))
            t.start()
            threads.append(t)
            
        # If CLI is requested, drop into it now so user can interact DURING the benchmark
        if args.cli:
            info("*** Starting Interactive MeshPay CLI for debugging (benchmark running in background)\n")
            cli = MeshPayCLI(
                net, authorities, clients, gateway,
                link_stats=link_stats, qos_mgr=qos_mgr,
            )
            cli.cmdloop()
            info("*** CLI session closed. Waiting for background load generation to finish...\n")
        else:
            # Main thread simply sleeps for the duration
            time.sleep(args.duration)
        
        info(f"*** Load generation finished. Waiting 5s for pending transactions to clear...\n")
        stop_event.set()
        for t in threads:
            t.join(timeout=2.0)
        time.sleep(5.0)

        # Gather final metrics
        gather_and_save_metrics(authorities, clients, link_stats, args.csv)
        

    except KeyboardInterrupt:
        info("\n*** Interrupted by user\n")
        stop_event.set()
    except Exception as e:
        info(f"*** Error: {e}\n")
        import traceback
        traceback.print_exc()
    finally:
        if link_stats:
            link_stats.stop()
        if bridge:
            bridge.stop_bridge_server()
        if net is not None:
            info("*** Stopping node services gracefully\n")
            for node in authorities + clients:
                if hasattr(node, 'stop_fastpay_services'):
                    node.stop_fastpay_services()
            
            info("*** Stopping enhanced mesh network\n")
            net.stop()
            if args.logs:
                _close_xterms(authorities, clients)
            
        info("*** Benchmark completed\n")
        try:
            import matplotlib.pyplot as plt
            plt.close('all')
        except Exception:
            pass

if __name__ == "__main__":
    main()
