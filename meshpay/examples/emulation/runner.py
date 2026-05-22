"""Benchmark runners for MeshPay emulation experiments."""

from __future__ import annotations

import json
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import List

from mininet.log import info

from meshpay.examples.emulation.config import BenchmarkStats, ComparisonResult, EmulationConfig
from meshpay.examples.emulation.environment import cleanup_environment
from meshpay.routing.registry import normalize_routing_name


def workspace_root() -> Path:
    """Return the repository root for default benchmark artifacts."""

    return Path(__file__).resolve().parents[3]


def benchmark_script_path() -> Path:
    """Return the stable CLI wrapper path used for isolated subprocesses."""

    return Path(__file__).resolve().parent.parent / "emulation_benchmark_compare.py"


def run_single(config: EmulationConfig) -> BenchmarkStats:
    """Run one routing mode in the current process."""

    return run_emulation(config)


def run_emulation(config: EmulationConfig) -> BenchmarkStats:
    """Configure, boot, and run the mesh payment network for one routing profile."""

    if config.routing == "both":
        raise ValueError("run_emulation requires a single routing mode, not 'both'")

    config = config.with_routing(config.routing)
    random.seed(config.random_seed)

    from meshpay.examples.meshpay_demo import setup_test_accounts
    from meshpay.examples.emulation.metrics import (
        collect_benchmark_stats,
        monitor_progress,
        wait_for_peer_discovery,
    )
    from meshpay.examples.emulation.topology import create_emulation_context
    from meshpay.examples.emulation.workload import submit_workload

    context = create_emulation_context(config)
    stopped = False
    try:
        info("*** Starting network and initializing nodes...\n")
        context.net.build()

        for auth in context.authorities:
            auth.start_fastpay_services(enable_internet=False)

        setup_test_accounts(context.authorities, context.clients)

        for client in context.clients:
            client.start_fastpay_services()

        info("*** Waiting for mesh network to stabilize and discover peers...\n")
        time.sleep(2)
        wait_for_peer_discovery(context.clients, context.authorities, config.peer_discovery_timeout)

        info("*** Injecting offline payment workload...\n")
        submitted_orders = submit_workload(
            context.clients,
            config.workload,
            config.duration,
            interval=config.workload_interval,
            pending_wait_timeout=max(30.0, config.duration / 4.0),
        )

        monitor_progress(context.clients, config.duration, submitted_orders, len(config.workload))

        info("\n*** Stopping node services gracefully...\n")
        for client in context.clients:
            client.stop_fastpay_services()
        for auth in context.authorities:
            auth.stop_fastpay_services()

        stats = collect_benchmark_stats(
            context.clients,
            context.authorities,
            network_mode=config.network_mode,
            wireless_interface=context.interface_profile.name,
            routing=config.routing,
            policy_file=config.policy_file,
            submitted_payments=submitted_orders,
            duration=config.duration,
        )
        stats = BenchmarkStats.from_dict(
            {
                **stats.to_dict(),
                "scenario_name": config.scenario_name,
                "experiment_id": config.experiment_id,
                "seed": config.random_seed,
                "wireless_range": config.wireless_range,
                "mobility_speed": f"{config.mobility_min_v}-{config.mobility_max_v}",
            }
        )

        context.net.stop()
        stopped = True
        return stats
    finally:
        if not stopped:
            context.net.stop()


def build_subprocess_command(
    config: EmulationConfig,
    routing: str,
    output_file: str | Path,
    script_path: str | Path | None = None,
) -> List[str]:
    """Build the isolated subprocess command for one comparison arm."""

    normalized_routing = normalize_routing_name(routing)
    script = Path(script_path) if script_path else benchmark_script_path()
    cmd = [sys.executable, str(script)]

    if normalized_routing == "epidemic":
        cmd.extend(["--routing-mode", "epidemic"])
    else:
        cmd.extend(["--routing", normalized_routing])

    cmd.extend(
        [
            "--authorities",
            str(config.authorities),
            "--clients",
            str(config.clients),
            "--duration",
            str(config.duration),
            "--wireless-range",
            str(config.wireless_range),
            "--network-mode",
            config.network_mode,
            "--wireless-interface",
            config.wireless_interface,
            "--output-file",
            str(output_file),
            "--random-seed",
            str(config.random_seed),
            "--mobility-min-x",
            str(config.mobility_min_x),
            "--mobility-max-x",
            str(config.mobility_max_x),
            "--mobility-min-y",
            str(config.mobility_min_y),
            "--mobility-max-y",
            str(config.mobility_max_y),
            "--mobility-min-v",
            str(config.mobility_min_v),
            "--mobility-max-v",
            str(config.mobility_max_v),
            "--peer-discovery-timeout",
            str(config.peer_discovery_timeout),
            "--scenario-name",
            config.scenario_name,
            "--workload-size",
            str(config.workload_size or len(config.workload)),
            "--workload-seed",
            str(config.workload_seed),
            "--workload-interval",
            str(config.workload_interval),
            "--authority-layout",
            config.authority_layout,
            "--client-layout",
            config.client_layout,
            "--experiment-id",
            config.experiment_id,
        ]
    )
    if config.policy_file:
        cmd.extend(["--policy-file", config.policy_file])
    if config.plot:
        cmd.append("--plot")
    return cmd


def run_comparison(config: EmulationConfig) -> ComparisonResult:
    """Run epidemic and SDN-DTN benchmarks in isolated subprocesses."""

    root = workspace_root()
    epidemic_json = root / "meshpay" / "examples" / "epidemic_stats.json"
    sdn_json = root / "meshpay" / "examples" / "sdn_stats.json"

    print("=" * 75)
    print("🔬 MESHPAY ROUTING PERFORMANCE BENCHMARK COMPARATIVE STUDY")
    print(f"   Authorities: {config.authorities} | Clients: {config.clients} | Emulation Duration: {config.duration}s")
    print(f"   Network: {config.network_mode} | Interface: {config.wireless_interface}")
    print("=" * 75)

    try:
        if epidemic_json.exists():
            epidemic_json.unlink()

        print("\n--- Running Epidemic Baseline Emulation (Isolated Subprocess) ---")
        cleanup_environment()
        subprocess.run(build_subprocess_command(config, "epidemic", epidemic_json), check=True)

        if sdn_json.exists():
            sdn_json.unlink()

        print("\n--- Running SDN-DTN Emulation (Isolated Subprocess) ---")
        cleanup_environment()
        subprocess.run(build_subprocess_command(config, "sdn_dtn", sdn_json), check=True)
    finally:
        cleanup_environment()

    if not epidemic_json.exists() or not sdn_json.exists():
        raise RuntimeError("Subprocess benchmark telemetry files were not generated properly.")

    with epidemic_json.open("r", encoding="utf-8") as f:
        epidemic_stats = BenchmarkStats.from_dict(json.load(f))
    with sdn_json.open("r", encoding="utf-8") as f:
        sdn_stats = BenchmarkStats.from_dict(json.load(f))

    return ComparisonResult(
        epidemic_stats=epidemic_stats,
        sdn_stats=sdn_stats,
        epidemic_json=str(epidemic_json),
        sdn_json=str(sdn_json),
    )


def format_comparison_report(result: ComparisonResult) -> str:
    """Format the historical comparison results matrix."""

    epidemic_stats = result.epidemic_stats.to_dict()
    sdn_stats = result.sdn_stats.to_dict()
    lines: List[str] = []
    lines.append("")
    lines.append("=" * 80)
    lines.append("📊 MESHPAY BENCHMARK COMPARATIVE RESULTS MATRIX")
    lines.append("=" * 80)
    lines.append(f"{'Metric':<32} | {'Epidemic Baseline':<18} | {'SDN-DTN Routing':<18} | {'Improvement':<12}")
    lines.append("-" * 88)

    ep_fin = epidemic_stats["finality_rate"]
    sdn_fin = sdn_stats["finality_rate"]
    fin_diff = sdn_fin - ep_fin
    lines.append(f"{'Finality Rate (%)':<32} | {ep_fin:>17.1f}% | {sdn_fin:>17.1f}% | {fin_diff:>+11.1f}%")

    ep_lat = epidemic_stats["avg_latency_ms"]
    sdn_lat = sdn_stats["avg_latency_ms"]
    lat_diff = ((ep_lat - sdn_lat) / ep_lat * 100) if ep_lat > 0 else 0.0
    lines.append(f"{'Avg End-to-End Latency (ms)':<32} | {ep_lat:>17.2f}  | {sdn_lat:>17.2f}  | {lat_diff:>+10.1f}% (reduced)")

    ep_ctrl = epidemic_stats["control_bytes"]
    sdn_ctrl = sdn_stats["control_bytes"]
    ctrl_diff = ((ep_ctrl - sdn_ctrl) / ep_ctrl * 100) if ep_ctrl > 0 else 0.0
    lines.append(f"{'Total Control Overhead (Bytes)':<32} | {ep_ctrl:>18,d} | {sdn_ctrl:>18,d} | {ctrl_diff:>+10.1f}% (reduced)")

    ep_data = epidemic_stats["data_bytes"]
    sdn_data = sdn_stats["data_bytes"]
    data_diff = ((ep_data - sdn_data) / ep_data * 100) if ep_data > 0 else 0.0
    lines.append(f"{'Total Forwarding Overhead (Bytes)':<32} | {ep_data:>18,d} | {sdn_data:>18,d} | {data_diff:>+10.1f}% (reduced)")

    ep_buf = epidemic_stats["avg_buffer_size"]
    sdn_buf = sdn_stats["avg_buffer_size"]
    buf_diff = ((ep_buf - sdn_buf) / ep_buf * 100) if ep_buf > 0 else 0.0
    lines.append(f"{'Remaining Buffer Size (items)':<32} | {ep_buf:>17.1f}  | {sdn_buf:>17.1f}  | {buf_diff:>+10.1f}% (purged)")
    lines.append("=" * 88)
    return "\n".join(lines)


def write_json_output(path: str | Path, payload: dict) -> None:
    """Write JSON output for the CLI wrapper."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f)

