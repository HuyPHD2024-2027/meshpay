"""Progress monitoring and telemetry collection for MeshPay emulation benchmarks."""

from __future__ import annotations

import time
from typing import Any, List

from mininet.log import info

from meshpay.examples.emulation.config import BenchmarkStats
from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client import Client


def _neighbor_kind(address: Any) -> str:
    node_type = getattr(address, "node_type", "")
    value = getattr(node_type, "value", node_type)
    return str(value).lower()


def wait_for_peer_discovery(
    clients: List[Client],
    authorities: List[WiFiAuthority],
    timeout: float = 10.0,
) -> None:
    """Wait until nodes have enough discovered peers for the benchmark path."""

    deadline = time.time() + timeout
    quorum = int(len(authorities) * 2 / 3) + 1 if authorities else 1

    while time.time() < deadline:
        clients_ready = all(
            sum(1 for addr in client.state.neighbors.values() if _neighbor_kind(addr) == "authority") >= quorum
            for client in clients
        )
        authorities_ready = all(
            any(_neighbor_kind(addr) == "client" for addr in authority.state.neighbors.values())
            for authority in authorities
        )
        if clients_ready and authorities_ready:
            return
        time.sleep(0.25)

    info("*** Peer discovery timeout reached; continuing with current neighbor tables\n")


def monitor_progress(clients: List[Client], duration: int, submitted_orders: int, workload_size: int) -> None:
    """Log finalized payment progress during the emulation run."""

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
        denominator = submitted_orders or workload_size
        completed = min(completed, denominator)
        info(f"⏱️  Time: {elapsed}/{duration}s | Finalized Payments: {completed}/{denominator}\n")
        time.sleep(5)


def collect_benchmark_stats(
    clients: List[Client],
    authorities: List[WiFiAuthority],
    *,
    network_mode: str,
    wireless_interface: str,
    routing: str,
    policy_file: str,
    submitted_payments: int = 0,
    duration: int = 0,
) -> BenchmarkStats:
    """Compile final telemetry from clients and authorities."""

    info("*** Compiling evaluation metrics...\n")
    total_tx = 0
    total_latency_sum = 0.0
    latency_count = 0
    total_data_bytes = 0
    total_control_bytes = 0
    total_remaining_buffer_items = 0
    successful_ids = set()
    raw_successful_events = 0
    certificate_attempts = 0
    certificates_built = 0
    vote_rtt_sum = 0.0
    vote_rtt_count = 0
    handoff_sum = 0.0
    handoff_count = 0
    peer_discovery_events = 0

    for client in clients:
        stats = client.performance_metrics.get_stats()
        total_tx += stats.get("transaction_count", 0)
        successful_ids.update(stats.get("successful_transaction_ids", []))
        raw_successful_events += stats.get("successful_transaction_count", 0)
        certificate_attempts += stats.get("certificate_attempts", stats.get("certificate_attempt_count", 0))
        certificates_built += stats.get("certificates_built", stats.get("certificate_built_count", 0))
        vote_rtt = stats.get("average_vote_rtt_ms", stats.get("avg_vote_rtt_ms", 0.0))
        if vote_rtt and vote_rtt > 0:
            vote_rtt_sum += vote_rtt
            vote_rtt_count += 1
        handoff = stats.get("average_handoff_interruption_ms", stats.get("avg_handoff_interruption_ms", 0.0))
        if handoff and handoff > 0:
            handoff_sum += handoff
            handoff_count += 1
        peer_discovery_events += len(getattr(client.state, "neighbors", {}) or {})

        avg_lat = stats.get("average_e2e_latency_ms", 0.0)
        if avg_lat and avg_lat > 0:
            total_latency_sum += avg_lat
            latency_count += 1

        total_data_bytes += getattr(client, "data_bytes_sent", 0)
        total_control_bytes += getattr(client, "control_bytes_sent", 0)
        total_remaining_buffer_items += len(client.message_buffer)

    for authority in authorities:
        total_data_bytes += getattr(authority, "data_bytes_sent", 0)
        total_control_bytes += getattr(authority, "control_bytes_sent", 0)

    avg_latency = total_latency_sum / latency_count if latency_count > 0 else 0.0
    successful_tx = len(successful_ids) if successful_ids else raw_successful_events
    successful_tx = min(successful_tx, total_tx)
    finality_rate = (successful_tx / total_tx * 100.0) if total_tx > 0 else 0.0
    avg_buffer_size = total_remaining_buffer_items / len(clients) if clients else 0.0
    certificate_rate = (certificates_built / certificate_attempts * 100.0) if certificate_attempts > 0 else finality_rate
    avg_vote_rtt = vote_rtt_sum / vote_rtt_count if vote_rtt_count else 0.0
    avg_handoff = handoff_sum / handoff_count if handoff_count else 0.0
    tps = successful_tx / duration if duration else 0.0

    return BenchmarkStats(
        finality_rate=finality_rate,
        avg_latency_ms=avg_latency,
        control_bytes=total_control_bytes,
        data_bytes=total_data_bytes,
        avg_buffer_size=avg_buffer_size,
        total_tx=total_tx,
        successful_tx=successful_tx,
        successful_transaction_ids=sorted(successful_ids),
        raw_successful_events=raw_successful_events,
        network_mode=network_mode,
        wireless_interface=wireless_interface,
        routing=routing,
        policy_file=policy_file,
        submitted_payments=submitted_payments or total_tx,
        certificate_assembly_success_rate=certificate_rate,
        avg_vote_rtt_ms=avg_vote_rtt,
        avg_handoff_interruption_ms=avg_handoff,
        tps=tps,
        peer_discovery_events=peer_discovery_events,
        contact_events=peer_discovery_events,
    )

