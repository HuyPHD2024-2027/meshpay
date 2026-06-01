#!/usr/bin/env python3
"""MeshPay Emulation Resilience Study - Premium Interactive CLI Tool."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from mininet.log import setLogLevel, info

from meshpay.examples.emulation.config import parse_args, EmulationConfig
from meshpay.examples.emulation.runner import (
    run_single,
    run_comparison,
    format_comparison_report,
    write_json_output,
)
from meshpay.examples.emulation.campaign import generate_plots

def print_header() -> None:
    """Print a highly styled premium CLI header."""
    print("=" * 80)
    print("🛡️   MESHPAY SD-DTN EMULATION RESILIENCE BENCHMARKING STUDY")
    print("=" * 80)

def print_config(config: EmulationConfig) -> None:
    """Print configuration profile in a highly readable format."""
    print("\n📊 CONFIGURATION PROFILE:")
    print(f"  • Scenario Label:     \033[94m{config.scenario_name}\033[0m")
    print(f"  • Routing Protocol:   \033[92m{config.routing.upper()}\033[0m")
    print(f"  • WiFi Authorities:   {config.authorities} nodes ({config.authority_layout} layout)")
    print(f"  • WiFi Clients:       {config.clients} stations ({config.client_layout} layout)")
    print(f"  • Wireless Range:     {config.wireless_range} meters")
    print(f"  • Emulation Duration: {config.duration} seconds")
    print(f"  • Random Seed:        {config.random_seed}")

    print("\n💥 RESILIENCE ATTACK PARAMETERS:")
    if config.attack_type != "none":
        print(f"  • Attack Type:        \033[91m{config.attack_type.upper()}\033[0m")
        print(f"  • Attack Intensity:   \033[93m{config.attack_intensity * 100:.1f}%\033[0m")
        print(f"  • Target Node:        \033[95m{config.attack_target}\033[0m")
    else:
        print("  • Attack Type:        \033[90mNONE (Baseline Run)\033[0m")
    print("-" * 80)

def print_telemetry_files(config: EmulationConfig) -> None:
    """Print the paths and descriptions of generated log files."""
    workspace_root = Path("/home/huydq/PHD2024-2027/meshpay")
    log_dir = workspace_root / "tmp" / "logs"
    results_dir = workspace_root / "results"

    print("\n📝 SYSTEM LOG TELEMETRY GENERATED:")
    attack_log = log_dir / "attack.log"
    if attack_log.exists():
        print(f"  • 🛑 Attack Log:             \033[96m{attack_log.relative_to(workspace_root)}\033[0m")
    
    print("  • 📨 Client Station Logs:")
    for i in range(1, config.clients + 1):
        client_log = log_dir / f"user{i}_client.log"
        if client_log.exists():
            print(f"    - user{i}:                 {client_log.relative_to(workspace_root)}")
            
    print("  • 🛡️  Authority Validator Logs:")
    for i in range(1, config.authorities + 1):
        auth_log = log_dir / f"auth{i}_authority.log"
        if auth_log.exists():
            print(f"    - auth{i}:                 {auth_log.relative_to(workspace_root)}")

    if config.plot:
        print("\n🎨 TOPOLOGICAL VISUALIZATION GENERATED:")
        if os.getuid() != 0:
            plot_path = results_dir / "fallback_topology.png"
            if plot_path.exists():
                print(f"  • 📍 Network Topology Plot:   \033[92m{plot_path.relative_to(workspace_root)}\033[0m")
        else:
            print("  • 📍 Network Topology Plot:   Generated in Mininet-WiFi interactive window.")
    print("=" * 80)

def main() -> None:
    """Run the interactive resilience benchmark study."""
    setLogLevel("info")
    config = parse_args()

    print_header()
    print_config(config)

    if config.routing in ("both", "all"):
        print("\n🚀 Executing routing comparison benchmark campaign...")
        result = run_comparison(config)
        
        print("\n" + "=" * 80)
        print("📊 COMPARATIVE BENCHMARK REPORT:")
        print("=" * 80)
        print(format_comparison_report(result))
        
        plot_output = config.plot_output or "results/comparison_plot.png"
        print(f"\n🎨 Generating comparative evaluation plots to: {plot_output}")
        generate_plots(
            result.epidemic_stats,
            result.sdn_stats,
            plot_output,
            all_stats=result.all_stats,
        )
        if config.output_file:
            write_json_output(config.output_file, result.to_dict())
            print(f"💾 Saved comparison telemetry stats to: {config.output_file}")
            
    else:
        print(f"\n🚀 Executing single-protocol benchmark run for \033[92m{config.routing.upper()}\033[0m...")
        stats = run_single(config)
        
        print("\n" + "=" * 80)
        print(f"📊 SINGLE RUN Telemetry Summary ({config.routing.upper()}):")
        print("=" * 80)
        print(f"  • Packet Delivery Finality: \033[92m{stats.finality_rate:.2f}%\033[0m")
        print(f"  • Average Latency:           \033[93m{stats.avg_latency_ms:.2f} ms\033[0m")
        print(f"  • Control Plane Overhead:    {stats.control_bytes} bytes")
        print(f"  • Data Plane Traffic:        {stats.data_bytes} bytes")
        print(f"  • Average Node Buffer size:  {stats.avg_buffer_size:.2f} messages")
        print(f"  • Total Transactions:        {stats.total_tx} submitted / {stats.successful_tx} completed")
        print(f"  • Peer Discovery Contacts:   {stats.peer_discovery_events} events")
        print(f"  • Simulation Backend Mode:   {stats.network_mode.upper()}")
        print("-" * 80)
        
        if config.output_file:
            write_json_output(config.output_file, stats.to_dict())
            print(f"💾 Saved {config.routing.upper()} telemetry stats to: {config.output_file}")

    print("\n✅ Resilience study run completed successfully!")
    print_telemetry_files(config)

if __name__ == "__main__":
    main()
