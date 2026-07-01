#!/usr/bin/env python3

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []

    records: List[Dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    return records


def percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None

    if len(values) == 1:
        return values[0]

    sorted_values = sorted(values)
    index = (len(sorted_values) - 1) * (p / 100.0)

    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = index - lower

    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def latency_summary(values_ms: List[float]) -> Dict[str, Optional[float]]:
    if not values_ms:
        return {
            "min": None,
            "max": None,
            "avg": None,
            "median": None,
            "p50": None,
            "p90": None,
            "p95": None,
            "p99": None,
        }

    return {
        "min": min(values_ms),
        "max": max(values_ms),
        "avg": statistics.mean(values_ms),
        "median": statistics.median(values_ms),
        "p50": percentile(values_ms, 50),
        "p90": percentile(values_ms, 90),
        "p95": percentile(values_ms, 95),
        "p99": percentile(values_ms, 99),
    }


def collect_payment_metrics(
    log_dir: str | Path,
    started_at: float,
    ended_at: float,
) -> Dict[str, Any]:
    """Collect payment-level MeshPay metrics from payment.log."""

    log_dir = Path(log_dir)
    payment_log = log_dir / "payment.log"
    events = load_jsonl(payment_log)

    duration_s = max(ended_at - started_at, 0.000001)

    payment_created = [
        e for e in events
        if e.get("event") == "payment_created"
    ]

    confirmation_created = [
        e for e in events
        if e.get("event") == "confirmation_created"
    ]

    payment_accepted = [
        e for e in events
        if e.get("event") == "payment_accepted"
    ]

    tx_events = [
        e for e in events
        if e.get("event") == "payload_injected"
    ]

    rx_events = [
        e for e in events
        if e.get("event") == "payment_payload_delivered"
    ]

    submit_failed = [
        e for e in events
        if e.get("event") == "payment_submit_failed"
    ]

    skipped = [
        e for e in events
        if e.get("event") == "payment_skipped"
    ]

    created_by_order = {
        e["order_id"]: e
        for e in payment_created
        if "order_id" in e
    }

    confirmed_by_order = {
        e["order_id"]: e
        for e in confirmation_created
        if "order_id" in e
    }

    accepted_by_order = {
        e["order_id"]: e
        for e in payment_accepted
        if "order_id" in e
    }

    time_to_quorum_ms: List[float] = []
    time_to_acceptance_ms: List[float] = []

    for order_id, confirmed in confirmed_by_order.items():
        created = created_by_order.get(order_id)

        if created is None:
            continue

        time_to_quorum_ms.append(
            (float(confirmed["time"]) - float(created["time"])) * 1000.0
        )

    for order_id, accepted in accepted_by_order.items():
        created = created_by_order.get(order_id)

        if created is None:
            continue

        time_to_acceptance_ms.append(
            (float(accepted["time"]) - float(created["time"])) * 1000.0
        )

    tx_payloads = len(tx_events)
    rx_payloads = len(rx_events)

    tx_bytes = sum(
        int(e.get("payload_size_bytes", 0))
        for e in tx_events
    )

    rx_bytes = sum(
        int(e.get("payload_size_bytes", 0))
        for e in rx_events
    )

    payments_created = len(created_by_order)
    payments_confirmed = len(confirmed_by_order)
    payments_accepted_count = len(accepted_by_order)

    net_stats_events = [
        e for e in events
        if e.get("event") == "network_stats"
    ]

    node_samples: dict[str, list[dict]] = {}
    for e in net_stats_events:
        node = e.get("node")
        if not node:
            continue
        if node not in node_samples:
            node_samples[node] = []
        node_samples[node].append(e)

    total_net_tx_bytes = 0
    total_net_rx_bytes = 0
    total_net_tx_packets = 0
    total_net_rx_packets = 0

    for node, samples in node_samples.items():
        if len(samples) < 2:
            continue
        samples_sorted = sorted(samples, key=lambda x: float(x.get("time", 0.0)))
        first = samples_sorted[0]
        last = samples_sorted[-1]
        
        tx_diff = max(0, int(last.get("tx_bytes", 0)) - int(first.get("tx_bytes", 0)))
        rx_diff = max(0, int(last.get("rx_bytes", 0)) - int(first.get("rx_bytes", 0)))
        tx_packets_diff = max(0, int(last.get("tx_packets", 0)) - int(first.get("tx_packets", 0)))
        rx_packets_diff = max(0, int(last.get("rx_packets", 0)) - int(first.get("rx_packets", 0)))

        total_net_tx_bytes += tx_diff
        total_net_rx_bytes += rx_diff
        total_net_tx_packets += tx_packets_diff
        total_net_rx_packets += rx_packets_diff

    summary = {
        "duration_s": duration_s,

        "payments_created": payments_created,
        "payments_confirmed": payments_confirmed,
        "payments_accepted": payments_accepted_count,
        "payments_failed_to_submit": len(submit_failed),
        "payments_skipped": len(skipped),

        "network_tx_bytes": total_net_tx_bytes,
        "network_rx_bytes": total_net_rx_bytes,
        "network_tx_plus_rx_bytes": total_net_tx_bytes + total_net_rx_bytes,
        "network_tx_bytes_per_second": safe_div(total_net_tx_bytes, duration_s),
        "network_rx_bytes_per_second": safe_div(total_net_rx_bytes, duration_s),
        "network_tx_plus_rx_bytes_per_second": safe_div(
            total_net_tx_bytes + total_net_rx_bytes,
            duration_s,
        ),
        "network_tx_packets": total_net_tx_packets,
        "network_rx_packets": total_net_rx_packets,
        "network_tx_plus_rx_packets": total_net_tx_packets + total_net_rx_packets,
        "network_tx_packets_per_second": safe_div(total_net_tx_packets, duration_s),
        "network_rx_packets_per_second": safe_div(total_net_rx_packets, duration_s),
        "network_tx_plus_rx_packets_per_second": safe_div(
            total_net_tx_packets + total_net_rx_packets,
            duration_s,
        ),

        "payment_confirmation_rate_percent": (
            safe_div(payments_confirmed, payments_created) * 100.0
        ),
        "payment_acceptance_rate_percent": (
            safe_div(payments_accepted_count, payments_created) * 100.0
        ),

        # Transaction throughput.
        "created_tps": safe_div(payments_created, duration_s),
        "confirmed_tps": safe_div(payments_confirmed, duration_s),
        "accepted_tps": safe_div(payments_accepted_count, duration_s),

        # Application-level MeshPay message throughput.
        "tx_payloads": tx_payloads,
        "rx_payloads": rx_payloads,
        "tx_plus_rx_payloads": tx_payloads + rx_payloads,

        "tx_bytes": tx_bytes,
        "rx_bytes": rx_bytes,
        "tx_plus_rx_bytes": tx_bytes + rx_bytes,

        "tx_payloads_per_second": safe_div(tx_payloads, duration_s),
        "rx_payloads_per_second": safe_div(rx_payloads, duration_s),
        "tx_plus_rx_payloads_per_second": safe_div(
            tx_payloads + rx_payloads,
            duration_s,
        ),

        "tx_bytes_per_second": safe_div(tx_bytes, duration_s),
        "rx_bytes_per_second": safe_div(rx_bytes, duration_s),
        "tx_plus_rx_bytes_per_second": safe_div(
            tx_bytes + rx_bytes,
            duration_s,
        ),
    }

    payload_type_counts: Dict[str, Dict[str, int]] = {
        "tx": {},
        "rx": {},
    }

    for event in tx_events:
        payload_type = str(event.get("payload_type", "unknown"))
        payload_type_counts["tx"][payload_type] = (
            payload_type_counts["tx"].get(payload_type, 0) + 1
        )

    for event in rx_events:
        payload_type = str(event.get("payload_type", "unknown"))
        payload_type_counts["rx"][payload_type] = (
            payload_type_counts["rx"].get(payload_type, 0) + 1
        )

    return {
        "summary": summary,
        "latency_ms": {
            "time_to_quorum": latency_summary(time_to_quorum_ms),
            "time_to_acceptance": latency_summary(time_to_acceptance_ms),
        },
        "payload_type_counts": payload_type_counts,
        "paths": {
            "log_dir": str(log_dir),
            "payment_log": str(payment_log),
        },
        "raw_counts": {
            "events": len(events),
            "payment_created_events": len(payment_created),
            "confirmation_created_events": len(confirmation_created),
            "payment_accepted_events": len(payment_accepted),
            "tx_events": len(tx_events),
            "rx_events": len(rx_events),
            "submit_failed_events": len(submit_failed),
            "skipped_events": len(skipped),
        },
    }