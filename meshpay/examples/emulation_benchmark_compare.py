#!/usr/bin/env python3
"""SDN vs Epidemic Routing Emulation Benchmark Runner.

This script runs the IEEE 802.11s mesh offline payment network simulation twice
in separate subprocesses to prevent Mininet-WiFi state pollution:
1. Baseline Run: Epidemic routing protocol.
2. SDN-DTN Run: SDN-guided routing with priority queues, epoch limits, and active buffer pruning.

It collects, compares, and prints key metrics: Finality Rate, average End-to-End Latency,
control overhead, data forwarded, and remaining buffer sizes. It also generates a
publication-quality comparison graph.
"""

from __future__ import annotations

import os
import sys
import json
import time
import random
import argparse
import subprocess
from typing import Dict, List, Any, Tuple

from mininet.log import info, setLogLevel
from mn_wifi.link import wmediumd, mesh
from mn_wifi.wmediumdConnector import interference
from mn_wifi.net import Mininet_wifi

from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client import Client
from meshpay.transport import TransportKind
from mn_wifi.services.core.config import SUPPORTED_TOKENS
from meshpay.examples.meshpay_demo import setup_test_accounts


def cleanup_environment() -> None:
    """Kill lingering node processes, wmediumd, and clean mininet interfaces."""
    info("\n🧹 Cleaning up Mininet and wmediumd environment...\n")
    # Kill any leftover fastpay services or background processes
    subprocess.run("pkill -9 -f 'python3 -m meshpay'", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run("pkill -9 wmediumd", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Perform clean mininet reset
    subprocess.run("mn -c", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)




def _neighbor_kind(address: Any) -> str:
    node_type = getattr(address, "node_type", "")
    value = getattr(node_type, "value", node_type)
    return str(value).lower()


def wait_for_peer_discovery(clients: List[Client], authorities: List[WiFiAuthority], timeout: float = 10.0) -> None:
    """Wait until nodes have enough discovered peers for the benchmark path."""
    deadline = time.time() + timeout
    quorum = int(len(authorities) * 2 / 3) + 1 if authorities else 1

    while time.time() < deadline:
        clients_ready = all(
            sum(1 for addr in c.state.neighbors.values() if _neighbor_kind(addr) == "authority") >= quorum
            for c in clients
        )
        authorities_ready = all(
            any(_neighbor_kind(addr) == "client" for addr in a.state.neighbors.values())
            for a in authorities
        )
        if clients_ready and authorities_ready:
            return
        time.sleep(0.25)

    info("*** Peer discovery timeout reached; continuing with current neighbor tables\n")

def run_benchmark(
    routing_mode: str,
    num_authorities: int,
    num_clients: int,
    duration: int,
    wireless_range: int = 15,
    enable_plot: bool = False,
) -> Dict[str, Any]:
    """Configure, boot, and run the mesh payment network under a specific routing protocol."""
    info(f"\n🚀 Booting simulation with {routing_mode.upper()} routing protocol...\n")
    
    # Set seed for reproducible GaussMarkov mobility trajectory
    random.seed(42)
    
    # Initialize Mininet-WiFi
    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)
    
    authorities: List[WiFiAuthority] = []
    committee = {f"auth{i}" for i in range(1, num_authorities + 1)}
    
    # Add authorities (Embedded SDN Controllers if routing_mode is 'sdn')
    for i in range(1, num_authorities + 1):
        name = f"auth{i}"
        auth = net.addStation(
            name,
            cls=WiFiAuthority,
            committee_members=committee - {name},
            ip=f"10.0.0.{10 + i}/8",
            port=8000 + i,
            position=f'{40 + (i * 15)},100,0',
            range=wireless_range,
            txpower=10,
            routing_protocol_name=routing_mode
        )
        authorities.append(auth)
        
    # Add mobile clients (SDN Agents if routing_mode is 'sdn')
    clients: List[Client] = []
    for i in range(1, num_clients + 1):
        name = f"user{i}"
        client = net.addStation(
            name,
            cls=Client,
            ip=f"10.0.0.{20 + i}/8",
            port=9000 + i,
            min_x=0, max_x=200, min_y=0, max_y=150, min_v=1, max_v=3,
            range=wireless_range,
            txpower=10,
            routing_protocol_name=routing_mode
        )
        clients.append(client)
        
    info("*** Setting up wireless propagation model\n")
    net.setPropagationModel(model="logNormalShadowing", exp=3.5, sL=6.0)
    
    net.configureNodes()
    
    info("*** Creating mesh links\n")
    for i in range(1, num_authorities + 1):
        net.addLink(authorities[i-1], cls=mesh, ssid='meshNet',
                    intf=f'auth{i}-wlan0', channel=5, ht_cap='HT40+')
    for i in range(1, num_clients + 1):
        net.addLink(clients[i-1], cls=mesh, ssid='meshNet',
                    intf=f'user{i}-wlan0', channel=5, ht_cap='HT40+')
        
    info("*** Assigning mobility model (GaussMarkov)\n")
    net.setMobilityModel(
        time=0,
        model='GaussMarkov',
        velocity_mean=1,
        alpha=0.5,
        variance=0.1,
        seed=42
    )
    
    for client in clients:
        client.state.committee = authorities
        
    # Enable plotting if requested
    if enable_plot:
        info("*** Plotting mesh network\n")
        net.plotGraph(max_x=200, max_y=150)
        
    info("*** Starting network and initializing nodes...\n")
    net.build()
    
    # Start node services
    for auth in authorities:
        auth.start_fastpay_services(enable_internet=False)
        
    setup_test_accounts(authorities, clients)
    
    for client in clients:
        client.start_fastpay_services()
        
    # Wait for mesh interfaces and peer discovery to bootstrap
    info("*** Waiting for mesh network to stabilize and discover peers...\n")
    time.sleep(2)
    wait_for_peer_discovery(clients, authorities)
    
    # Initiate sample transfers among mobile clients
    info("*** Injecting offline payment workload...\n")
    workload = [
        ("user1", "user2", 10),
        ("user2", "user3", 15),
        ("user3", "user1", 5),
        ("user1", "user3", 20),
        ("user2", "user1", 12),
        ("user3", "user2", 8),
    ]
    
    client_map = {c.name: c for c in clients}
    xtz_token = SUPPORTED_TOKENS.get('XTZ', {}).get('address', '')
    
    # Staggered transfer submission. A client has one pending transfer slot,
    # so do not overwrite it with the same sender's next payment before quorum
    # handling clears the current order.
    per_sender_wait_timeout = max(5.0, duration / 2.0)
    submitted_orders = 0
    for sender_name, recipient_name, amount in workload:
        sender = client_map.get(sender_name)
        if sender:
            wait_start = time.time()
            while sender.state.pending_transfer is not None and time.time() - wait_start < per_sender_wait_timeout:
                time.sleep(0.2)

            if sender.state.pending_transfer is not None:
                pending_id = sender.state.pending_transfer.order_id
                info(
                    f"⚠️  [{sender_name}] Skipping transfer to {recipient_name}: "
                    f"pending order {pending_id} did not clear within {per_sender_wait_timeout:.1f}s\n"
                )
                continue

            info(f"📤 [{sender_name}] Submitting transfer: {amount} XTZ to {recipient_name}\n")
            sender.transfer(recipient_name, xtz_token, amount)
            submitted_orders += 1
            time.sleep(1.5)
            
    # Monitor finality and progress during the simulation run
    info(f"*** Emulating offline payments for {duration} seconds...\n")
    start_time = time.time()
    while time.time() - start_time < duration:
        elapsed = int(time.time() - start_time)
        finalized_ids = set()
        raw_completed = 0
        for client in clients:
            stats = client.performance_metrics.get_stats()
            finalized_ids.update(stats.get("successful_transaction_ids", []))
            raw_completed += stats.get("successful_transaction_count", 0)
        completed = len(finalized_ids) if finalized_ids else raw_completed
        denominator = submitted_orders or len(workload)
        completed = min(completed, denominator)
        info(f"⏱️  Time: {elapsed}/{duration}s | Finalized Payments: {completed}/{denominator}\n")
        time.sleep(5)
        
    # Stop node services gracefully
    info("\n*** Stopping node services gracefully...\n")
    for client in clients:
        client.stop_fastpay_services()
    for auth in authorities:
        auth.stop_fastpay_services()
        
    # Collect statistics
    info("*** Compiling evaluation metrics...\n")
    total_tx = 0
    successful_tx = 0
    total_latency_sum = 0.0
    latency_count = 0
    total_data_bytes = 0
    total_control_bytes = 0
    total_remaining_buffer_items = 0
    successful_ids = set()
    raw_successful_events = 0
    
    for client in clients:
        stats = client.performance_metrics.get_stats()
        total_tx += stats.get("transaction_count", 0)
        successful_ids.update(stats.get("successful_transaction_ids", []))
        raw_successful_events += stats.get("successful_transaction_count", 0)
        
        avg_lat = stats.get("average_e2e_latency_ms", 0.0)
        if avg_lat and avg_lat > 0:
            total_latency_sum += avg_lat
            latency_count += 1
            
        total_data_bytes += getattr(client, "data_bytes_sent", 0)
        total_control_bytes += getattr(client, "control_bytes_sent", 0)
        total_remaining_buffer_items += len(client.message_buffer)
        
    for auth in authorities:
        total_data_bytes += getattr(auth, "data_bytes_sent", 0)
        total_control_bytes += getattr(auth, "control_bytes_sent", 0)
        
    avg_latency = total_latency_sum / latency_count if latency_count > 0 else 0.0
    successful_tx = len(successful_ids) if successful_ids else raw_successful_events
    successful_tx = min(successful_tx, total_tx)
    finality_rate = (successful_tx / total_tx * 100.0) if total_tx > 0 else 0.0
    avg_buffer_size = total_remaining_buffer_items / len(clients) if clients else 0.0
    
    net.stop()
    
    return {
        "finality_rate": finality_rate,
        "avg_latency_ms": avg_latency,
        "control_bytes": total_control_bytes,
        "data_bytes": total_data_bytes,
        "avg_buffer_size": avg_buffer_size,
        "total_tx": total_tx,
        "successful_tx": successful_tx,
        "successful_transaction_ids": sorted(successful_ids),
        "raw_successful_events": raw_successful_events
    }


def generate_plots(epidemic_stats: Dict[str, Any], sdn_stats: Dict[str, Any]) -> None:
    """Generate high-quality Matplotlib figures comparing Epidemic vs SDN."""
    import matplotlib.pyplot as plt
    
    # Use clean academic style
    plt.rcParams.update({
        'font.size': 11,
        'axes.labelsize': 12,
        'axes.titlesize': 13,
        'xtick.labelsize': 10,
        'ytick.labelsize': 10,
        'figure.titlesize': 16,
        'grid.alpha': 0.3
    })
    
    fig, axs = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle("MeshPay Performance Evaluation: Epidemic vs. SDN-DTN", fontweight="bold")
    
    # Colors
    colors = ["#F25C54", "#3A86C8"]  # Epidemic (Coral Red) vs SDN-DTN (Teal Blue)
    labels = ["Epidemic Baseline", "SDN-DTN Routing"]
    
    # 1. Finality Rate
    axs[0, 0].bar(labels, [epidemic_stats["finality_rate"], sdn_stats["finality_rate"]], color=colors, width=0.5, edgecolor="black", alpha=0.9)
    axs[0, 0].set_title("Payment Transaction Finality Rate")
    axs[0, 0].set_ylabel("Finality Rate (%)")
    axs[0, 0].set_ylim(0, 105)
    axs[0, 0].grid(axis="y", linestyle="--")
    for i, val in enumerate([epidemic_stats["finality_rate"], sdn_stats["finality_rate"]]):
        axs[0, 0].text(i, val + 2, f"{val:.1f}%", ha="center", fontweight="bold")
        
    # 2. Latency
    axs[0, 1].bar(labels, [epidemic_stats["avg_latency_ms"], sdn_stats["avg_latency_ms"]], color=colors, width=0.5, edgecolor="black", alpha=0.9)
    axs[0, 1].set_title("Average End-to-End Latency")
    axs[0, 1].set_ylabel("Latency (ms)")
    axs[0, 1].grid(axis="y", linestyle="--")
    max_lat = max(epidemic_stats["avg_latency_ms"], sdn_stats["avg_latency_ms"])
    axs[0, 1].set_ylim(0, max_lat * 1.2 if max_lat > 0 else 100)
    for i, val in enumerate([epidemic_stats["avg_latency_ms"], sdn_stats["avg_latency_ms"]]):
        axs[0, 1].text(i, val + (max_lat * 0.03 if max_lat > 0 else 2), f"{val:.1f} ms", ha="center", fontweight="bold")
        
    # 3. Control Overhead
    axs[1, 0].bar(labels, [epidemic_stats["control_bytes"] / 1024.0, sdn_stats["control_bytes"] / 1024.0], color=colors, width=0.5, edgecolor="black", alpha=0.9)
    axs[1, 0].set_title("Routing & Control Overhead")
    axs[1, 0].set_ylabel("Overhead (KB)")
    axs[1, 0].grid(axis="y", linestyle="--")
    max_ctrl = max(epidemic_stats["control_bytes"], sdn_stats["control_bytes"]) / 1024.0
    axs[1, 0].set_ylim(0, max_ctrl * 1.2 if max_ctrl > 0 else 10)
    for i, val in enumerate([epidemic_stats["control_bytes"] / 1024.0, sdn_stats["control_bytes"] / 1024.0]):
        axs[1, 0].text(i, val + (max_ctrl * 0.03 if max_ctrl > 0 else 0.5), f"{val:.2f} KB", ha="center", fontweight="bold")
        
    # 4. Buffer Size
    axs[1, 1].bar(labels, [epidemic_stats["avg_buffer_size"], sdn_stats["avg_buffer_size"]], color=colors, width=0.5, edgecolor="black", alpha=0.9)
    axs[1, 1].set_title("Client Remaining Buffer Occupancy")
    axs[1, 1].set_ylabel("Average Items in Buffer")
    axs[1, 1].grid(axis="y", linestyle="--")
    max_buf = max(epidemic_stats["avg_buffer_size"], sdn_stats["avg_buffer_size"])
    axs[1, 1].set_ylim(0, max_buf * 1.2 if max_buf > 0 else 5)
    for i, val in enumerate([epidemic_stats["avg_buffer_size"], sdn_stats["avg_buffer_size"]]):
        axs[1, 1].text(i, val + (max_buf * 0.03 if max_buf > 0 else 0.2), f"{val:.1f} items", ha="center", fontweight="bold")
        
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "meshpay_routing_comparison.png")
    plt.savefig(plot_path, dpi=300)
    artifacts_path = "/home/huydq/.gemini/antigravity/brain/e6ec894d-6039-49b6-8b86-6a9690fc5967/artifacts/meshpay_routing_comparison.png"
    try:
        plt.savefig(artifacts_path, dpi=300)
        info(f"📊 Artifact comparative metrics graph saved to:\n   {artifacts_path}\n")
    except Exception:
        pass
    plt.close()
    info(f"\n📊 Gorgeous comparative metrics graph saved successfully to:\n   {plot_path}\n")


def main() -> None:
    setLogLevel("info")
    parser = argparse.ArgumentParser(description="MeshPay SDN-DTN vs Epidemic Benchmark.")
    parser.add_argument("--authorities", type=int, default=5, help="Number of authority nodes")
    parser.add_argument("--clients", type=int, default=3, help="Number of client nodes")
    parser.add_argument("--duration", type=int, default=40, help="Simulation duration per run (seconds)")
    parser.add_argument("--plot", action="store_true", help="Enable Mininet-WiFi graphical topology plotting")
    parser.add_argument("--routing-mode", type=str, choices=["epidemic", "sdn", "both"], default="both", help="Routing protocol run mode")
    parser.add_argument("--output-file", type=str, default="", help="Save statistics to this JSON path")
    args = parser.parse_args()
    
    script_path = os.path.abspath(__file__)
    workspace_dir = os.path.dirname(os.path.dirname(os.path.dirname(script_path)))
    
    if args.routing_mode == "both":
        print("=" * 75)
        print(f"🔬 MESHPAY ROUTING PERFORMANCE BENCHMARK COMPARATIVE STUDY")
        print(f"   Authorities: {args.authorities} | Clients: {args.clients} | Emulation Duration: {args.duration}s")
        print("=" * 75)
        
        # Subprocess Run 1: Epidemic
        epidemic_json = os.path.join(workspace_dir, "meshpay", "examples", "epidemic_stats.json")
        if os.path.exists(epidemic_json):
            os.remove(epidemic_json)
            
        print("\n--- Running Epidemic Baseline Emulation (Isolated Subprocess) ---")
        cleanup_environment()
        cmd1 = [
            sys.executable, script_path,
            "--routing-mode", "epidemic",
            "--authorities", str(args.authorities),
            "--clients", str(args.clients),
            "--duration", str(args.duration),
            "--output-file", epidemic_json
        ]
        subprocess.run(cmd1, check=True)
        
        # Subprocess Run 2: SDN
        sdn_json = os.path.join(workspace_dir, "meshpay", "examples", "sdn_stats.json")
        if os.path.exists(sdn_json):
            os.remove(sdn_json)
            
        print("\n--- Running SDN-DTN Emulation (Isolated Subprocess) ---")
        cleanup_environment()
        cmd2 = [
            sys.executable, script_path,
            "--routing-mode", "sdn",
            "--authorities", str(args.authorities),
            "--clients", str(args.clients),
            "--duration", str(args.duration),
            "--output-file", sdn_json
        ]
        subprocess.run(cmd2, check=True)
        
        # Final clean
        cleanup_environment()
        
        # Load stats
        if not os.path.exists(epidemic_json) or not os.path.exists(sdn_json):
            print("❌ Error: Subprocess benchmark telemetry files were not generated properly.")
            sys.exit(1)
            
        with open(epidemic_json, "r") as f:
            epidemic_stats = json.load(f)
        with open(sdn_json, "r") as f:
            sdn_stats = json.load(f)
            
        # Format and display a gorgeous comparative report
        print("\n" + "=" * 80)
        print("📊 MESHPAY BENCHMARK COMPARATIVE RESULTS MATRIX")
        print("=" * 80)
        print(f"{'Metric':<32} | {'Epidemic Baseline':<18} | {'SDN-DTN Routing':<18} | {'Improvement':<12}")
        print("-" * 88)
        
        # Finality rate
        ep_fin = epidemic_stats['finality_rate']
        sdn_fin = sdn_stats['finality_rate']
        fin_diff = sdn_fin - ep_fin
        print(f"{'Finality Rate (%)':<32} | {ep_fin:>17.1f}% | {sdn_fin:>17.1f}% | {fin_diff:>+11.1f}%")
        
        # Average Latency
        ep_lat = epidemic_stats['avg_latency_ms']
        sdn_lat = sdn_stats['avg_latency_ms']
        lat_diff = ((ep_lat - sdn_lat) / ep_lat * 100) if ep_lat > 0 else 0.0
        print(f"{'Avg End-to-End Latency (ms)':<32} | {ep_lat:>17.2f}  | {sdn_lat:>17.2f}  | {lat_diff:>+10.1f}% (reduced)")
        
        # Control Bytes Sent
        ep_ctrl = epidemic_stats['control_bytes']
        sdn_ctrl = sdn_stats['control_bytes']
        ctrl_diff = ((ep_ctrl - sdn_ctrl) / ep_ctrl * 100) if ep_ctrl > 0 else 0.0
        print(f"{'Total Control Overhead (Bytes)':<32} | {ep_ctrl:>18,d} | {sdn_ctrl:>18,d} | {ctrl_diff:>+10.1f}% (reduced)")
        
        # Data Bytes Sent
        ep_data = epidemic_stats['data_bytes']
        sdn_data = sdn_stats['data_bytes']
        data_diff = ((ep_data - sdn_data) / ep_data * 100) if ep_data > 0 else 0.0
        print(f"{'Total Forwarding Overhead (Bytes)':<32} | {ep_data:>18,d} | {sdn_data:>18,d} | {data_diff:>+10.1f}% (reduced)")
        
        # Average Remaining Buffer Items
        ep_buf = epidemic_stats['avg_buffer_size']
        sdn_buf = sdn_stats['avg_buffer_size']
        buf_diff = ((ep_buf - sdn_buf) / ep_buf * 100) if ep_buf > 0 else 0.0
        print(f"{'Remaining Buffer Size (items)':<32} | {ep_buf:>17.1f}  | {sdn_buf:>17.1f}  | {buf_diff:>+10.1f}% (purged)")
        
        print("=" * 88)
        print("\n🎨 Generating evaluation plots...")
        generate_plots(epidemic_stats, sdn_stats)
        print("\n✅ Benchmark study completed successfully!")
        
    else:
        # Run individual routing benchmark
        stats = run_benchmark(
            routing_mode=args.routing_mode,
            num_authorities=args.authorities,
            num_clients=args.clients,
            duration=args.duration,
            enable_plot=args.plot
        )
        
        if args.output_file:
            with open(args.output_file, "w") as f:
                json.dump(stats, f)
            print(f"💾 Saved {args.routing_mode.upper()} telemetry stats to: {args.output_file}")


if __name__ == "__main__":
    main()
