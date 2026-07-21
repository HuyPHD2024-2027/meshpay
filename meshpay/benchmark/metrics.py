#!/usr/bin/env python3

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []

    records = []

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


def percentile(values: List[float], p: float) -> float | None:
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


def collect_node_events(stores_dir: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []

    for node_dir in sorted(stores_dir.glob("sta*")):
        node_events = load_jsonl(node_dir / "events.jsonl")

        for event in node_events:
            event.setdefault("node", node_dir.name)
            events.append(event)

    return events


def collect_delivered_records(stores_dir: Path) -> List[Dict[str, Any]]:
    delivered: List[Dict[str, Any]] = []

    for node_dir in sorted(stores_dir.glob("sta*")):
        records = load_jsonl(node_dir / "delivered.log")

        for record in records:
            record.setdefault("node", node_dir.name)
            delivered.append(record)

    return delivered


def count_buffered_bundles(stores_dir: Path) -> Dict[str, int]:
    counts: Dict[str, int] = {}

    for node_dir in sorted(stores_dir.glob("sta*")):
        bundle_files = [
            path
            for path in node_dir.glob("*.json")
            if not path.name.startswith("delivered-")
        ]

        counts[node_dir.name] = len(bundle_files)

    return counts


def collect_metrics(
    log_dir: str | Path,
    routing: str,
    started_at: float,
    ended_at: float,
) -> Dict[str, Any]:
    log_dir = Path(log_dir)
    stores_dir = log_dir / "stores" / routing

    events = collect_node_events(stores_dir)
    delivered_records = collect_delivered_records(stores_dir)

    created_events = [event for event in events if event.get("event") == "created"]
    received_events = [event for event in events if event.get("event") == "received"]

    exchange_events = [
        event
        for event in events
        if event.get("event") in {"exchange", "incoming_exchange"}
    ]

    generated_ids = {event.get("bundle_id") for event in created_events}
    delivered_ids = {event.get("bundle_id") for event in delivered_records}

    generated_count = len(generated_ids)
    delivered_count = len(delivered_ids)
    lost_count = max(generated_count - delivered_count, 0)

    duration_s = max(ended_at - started_at, 0.000001)

    latencies_ms = [
        float(record["latency_ms"])
        for record in delivered_records
        if "latency_ms" in record
    ]

    generated_bytes = sum(
        int(event.get("size_bytes", 0))
        for event in created_events
    )

    delivered_bytes = sum(
        int(record.get("size_bytes", 0))
        for record in delivered_records
    )

    forwarded_copies = sum(
        int(event.get("sent", 0))
        for event in exchange_events
    )

    event_counts = Counter(event.get("event", "unknown") for event in events)

    generated_by_src = Counter(event.get("src") for event in created_events)
    delivered_by_dst = Counter(record.get("dst") for record in delivered_records)

    lat_summary = {
        "min": min(latencies_ms) if latencies_ms else None,
        "max": max(latencies_ms) if latencies_ms else None,
        "avg": statistics.mean(latencies_ms) if latencies_ms else None,
        "median": statistics.median(latencies_ms) if latencies_ms else None,
        "p50": percentile(latencies_ms, 50),
        "p90": percentile(latencies_ms, 90),
        "p95": percentile(latencies_ms, 95),
        "p99": percentile(latencies_ms, 99),
    }

    buffer_occupancy = count_buffered_bundles(stores_dir)

    summary = {
        "generated_messages": generated_count,
        "delivered_messages": delivered_count,
        "lost_messages": lost_count,

        "delivery_ratio": safe_div(delivered_count, generated_count),
        "delivery_rate_percent": safe_div(delivered_count, generated_count) * 100.0,

        # Same idea as your previous finality metric:
        # among generated benchmark messages, how many reached final delivery?
        "finality_rate_percent": safe_div(delivered_count, generated_count) * 100.0,

        "duration_s": duration_s,

        "offered_load_msg_s": safe_div(generated_count, duration_s),
        "delivered_throughput_msg_s": safe_div(delivered_count, duration_s),

        "offered_load_bytes_s": safe_div(generated_bytes, duration_s),
        "delivered_throughput_bytes_s": safe_div(delivered_bytes, duration_s),

        "generated_bytes": generated_bytes,
        "delivered_bytes": delivered_bytes,

        "received_copies": len(received_events),
        "forwarded_copies": forwarded_copies,

        "overhead_ratio": safe_div(forwarded_copies, delivered_count),
    }

    return {
        "summary": summary,
        "latency_ms": lat_summary,
        "event_counts": dict(event_counts),
        "generated_by_src": dict(generated_by_src),
        "delivered_by_dst": dict(delivered_by_dst),
        "buffer_occupancy": buffer_occupancy,
        "paths": {
            "log_dir": str(log_dir),
            "stores_dir": str(stores_dir),
        },
        "raw_counts": {
            "events": len(events),
            "created_events": len(created_events),
            "received_events": len(received_events),
            "exchange_events": len(exchange_events),
            "delivered_records": len(delivered_records),
        },
    }