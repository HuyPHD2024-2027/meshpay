#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def collect_network_metrics(
    log_dir: Path | str,
    started_at: float | None = None,
    ended_at: float | None = None,
) -> dict[str, Any]:
    log_dir = Path(log_dir)
    raw_file = log_dir / "network_raw.jsonl"

    default_result: dict[str, Any] = {
        "per_node_time_series": [],
        "aggregate_time_series": [],
        "summary": {
            "network_rx_plus_tx_bytes_total": 0,
            "network_rx_plus_tx_packets_total": 0,
            "network_rx_plus_tx_bytes_per_second_avg": 0.0,
            "peak_rx_plus_tx_bytes_per_second": 0.0,
            "peak_rx_plus_tx_packets_per_second": 0.0,
            "network_rx_dropped_total": 0,
            "network_tx_dropped_total": 0,
            "network_rx_errors_total": 0,
            "network_tx_errors_total": 0,
            "duration_s": 0.0,
        },
    }

    if not raw_file.exists():
        return default_result

    # Load all records
    records: List[Dict[str, Any]] = []
    try:
        with raw_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except Exception:
        return default_result

    if not records:
        return default_result

    # Group records by time (sample rounds)
    rounds: Dict[float, List[Dict[str, Any]]] = {}
    for r in records:
        t = float(r["time"])
        rounds.setdefault(t, []).append(r)

    sorted_times = sorted(rounds.keys())
    if len(sorted_times) < 2:
        # Not enough samples for a time series
        # Let's populate summary with 0s but safe
        return default_result

    if started_at is not None and ended_at is not None:
        total_duration = max(ended_at - started_at, 0.000001)
    else:
        first_time = sorted_times[0]
        last_time = sorted_times[-1]
        total_duration = max(last_time - first_time, 0.000001)

    # Group records by node to calculate totals
    node_records: Dict[str, List[Dict[str, Any]]] = {}
    for r in records:
        node_records.setdefault(r["node"], []).append(r)

    # Compute per-node totals (first vs last)
    node_totals: Dict[str, Dict[str, int]] = {}
    rx_bytes_total = 0
    tx_bytes_total = 0
    rx_packets_total = 0
    tx_packets_total = 0
    rx_dropped_total = 0
    tx_dropped_total = 0
    rx_errors_total = 0
    tx_errors_total = 0

    for node, r_list in node_records.items():
        sorted_r = sorted(r_list, key=lambda x: float(x["time"]))
        first_r = sorted_r[0]
        last_r = sorted_r[-1]

        def get_delta(field: str) -> int:
            return max(0, int(last_r.get(field, 0)) - int(first_r.get(field, 0)))

        rx_b = get_delta("rx_bytes")
        tx_b = get_delta("tx_bytes")
        rx_p = get_delta("rx_packets")
        tx_p = get_delta("tx_packets")
        rx_d = get_delta("rx_dropped")
        tx_d = get_delta("tx_dropped")
        rx_e = get_delta("rx_errors")
        tx_e = get_delta("tx_errors")

        rx_bytes_total += rx_b
        tx_bytes_total += tx_b
        rx_packets_total += rx_p
        tx_packets_total += tx_p
        rx_dropped_total += rx_d
        tx_dropped_total += tx_d
        rx_errors_total += rx_e
        tx_errors_total += tx_e

    network_rx_plus_tx_bytes_total = rx_bytes_total + tx_bytes_total
    network_rx_plus_tx_packets_total = rx_packets_total + tx_packets_total

    # Calculate time series
    per_node_ts: List[Dict[str, Any]] = []
    aggregate_ts: List[Dict[str, Any]] = []

    for i in range(len(sorted_times) - 1):
        t_start = sorted_times[i]
        t_end = sorted_times[i + 1]
        duration = t_end - t_start
        if duration <= 0:
            continue

        start_records = {r["node"]: r for r in rounds[t_start]}
        end_records = {r["node"]: r for r in rounds[t_end]}

        # Fields to aggregate
        agg_fields = {
            "rx_bytes_delta": 0,
            "tx_bytes_delta": 0,
            "rx_packets_delta": 0,
            "tx_packets_delta": 0,
            "rx_dropped_delta": 0,
            "tx_dropped_delta": 0,
            "rx_errors_delta": 0,
            "tx_errors_delta": 0,
            "rx_plus_tx_bytes_delta": 0,
            "rx_plus_tx_packets_delta": 0,
        }

        common_nodes = set(start_records.keys()) & set(end_records.keys())
        if not common_nodes:
            continue

        # Get first node's relative start/end for the interval
        first_node_rec = end_records[list(common_nodes)[0]]
        rel_start = float(first_node_rec["relative_time_s"]) - duration
        rel_end = float(first_node_rec["relative_time_s"])

        for node in common_nodes:
            r_start = start_records[node]
            r_end = end_records[node]

            def cell_delta(field: str) -> int:
                return max(0, int(r_end.get(field, 0)) - int(r_start.get(field, 0)) if field in r_end and field in r_start else 0)

            node_rx_b = cell_delta("rx_bytes")
            node_tx_b = cell_delta("tx_bytes")
            node_rx_p = cell_delta("rx_packets")
            node_tx_p = cell_delta("tx_packets")
            node_rx_d = cell_delta("rx_dropped")
            node_tx_d = cell_delta("tx_dropped")
            node_rx_e = cell_delta("rx_errors")
            node_tx_e = cell_delta("tx_errors")

            node_rx_plus_tx_b = node_rx_b + node_tx_b
            node_rx_plus_tx_p = node_rx_p + node_tx_p

            # Update aggregates
            agg_fields["rx_bytes_delta"] += node_rx_b
            agg_fields["tx_bytes_delta"] += node_tx_b
            agg_fields["rx_packets_delta"] += node_rx_p
            agg_fields["tx_packets_delta"] += node_tx_p
            agg_fields["rx_dropped_delta"] += node_rx_d
            agg_fields["tx_dropped_delta"] += node_tx_d
            agg_fields["rx_errors_delta"] += node_rx_e
            agg_fields["tx_errors_delta"] += node_tx_e
            agg_fields["rx_plus_tx_bytes_delta"] += node_rx_plus_tx_b
            agg_fields["rx_plus_tx_packets_delta"] += node_rx_plus_tx_p

            per_node_ts.append({
                "interval_start_s": rel_start,
                "interval_end_s": rel_end,
                "node": node,
                "iface": r_end.get("iface", ""),
                "rx_bytes_delta": node_rx_b,
                "tx_bytes_delta": node_tx_b,
                "rx_packets_delta": node_rx_p,
                "tx_packets_delta": node_tx_p,
                "rx_dropped_delta": node_rx_d,
                "tx_dropped_delta": node_tx_d,
                "rx_errors_delta": node_rx_e,
                "tx_errors_delta": node_tx_e,
                "rx_plus_tx_bytes_delta": node_rx_plus_tx_b,
                "rx_plus_tx_packets_delta": node_rx_plus_tx_p,
                "rx_bytes_per_second": node_rx_b / duration,
                "tx_bytes_per_second": node_tx_b / duration,
                "rx_packets_per_second": node_rx_p / duration,
                "tx_packets_per_second": node_tx_p / duration,
                "rx_dropped_per_second": node_rx_d / duration,
                "tx_dropped_per_second": node_tx_d / duration,
                "rx_errors_per_second": node_rx_e / duration,
                "tx_errors_per_second": node_tx_e / duration,
                "rx_plus_tx_bytes_per_second": node_rx_plus_tx_b / duration,
                "rx_plus_tx_packets_per_second": node_rx_plus_tx_p / duration,
            })

        # Add to aggregate time series
        aggregate_ts.append({
            "interval_start_s": rel_start,
            "interval_end_s": rel_end,
            "rx_bytes_delta": agg_fields["rx_bytes_delta"],
            "tx_bytes_delta": agg_fields["tx_bytes_delta"],
            "rx_packets_delta": agg_fields["rx_packets_delta"],
            "tx_packets_delta": agg_fields["tx_packets_delta"],
            "rx_dropped_delta": agg_fields["rx_dropped_delta"],
            "tx_dropped_delta": agg_fields["tx_dropped_delta"],
            "rx_errors_delta": agg_fields["rx_errors_delta"],
            "tx_errors_delta": agg_fields["tx_errors_delta"],
            "rx_plus_tx_bytes_delta": agg_fields["rx_plus_tx_bytes_delta"],
            "rx_plus_tx_packets_delta": agg_fields["rx_plus_tx_packets_delta"],
            "rx_bytes_per_second": agg_fields["rx_bytes_delta"] / duration,
            "tx_bytes_per_second": agg_fields["tx_bytes_delta"] / duration,
            "rx_packets_per_second": agg_fields["rx_packets_delta"] / duration,
            "tx_packets_per_second": agg_fields["tx_packets_delta"] / duration,
            "rx_dropped_per_second": agg_fields["rx_dropped_delta"] / duration,
            "tx_dropped_per_second": agg_fields["tx_dropped_delta"] / duration,
            "rx_errors_per_second": agg_fields["rx_errors_delta"] / duration,
            "tx_errors_per_second": agg_fields["tx_errors_delta"] / duration,
            "rx_plus_tx_bytes_per_second": agg_fields["rx_plus_tx_bytes_delta"] / duration,
            "rx_plus_tx_packets_per_second": agg_fields["rx_plus_tx_packets_delta"] / duration,
        })

    # Peak throughput in aggregate time series
    peak_bytes_ps = 0.0
    peak_packets_ps = 0.0
    if aggregate_ts:
        peak_bytes_ps = max(item["rx_plus_tx_bytes_per_second"] for item in aggregate_ts)
        peak_packets_ps = max(item["rx_plus_tx_packets_per_second"] for item in aggregate_ts)

    network_rx_plus_tx_bytes_per_second_avg = (
        network_rx_plus_tx_bytes_total / total_duration if total_duration > 0 else 0.0
    )

    summary = {
        "network_rx_plus_tx_bytes_total": network_rx_plus_tx_bytes_total,
        "network_rx_plus_tx_packets_total": network_rx_plus_tx_packets_total,
        "network_rx_plus_tx_bytes_per_second_avg": network_rx_plus_tx_bytes_per_second_avg,
        "peak_rx_plus_tx_bytes_per_second": peak_bytes_ps,
        "peak_rx_plus_tx_packets_per_second": peak_packets_ps,
        "network_rx_dropped_total": rx_dropped_total,
        "network_tx_dropped_total": tx_dropped_total,
        "network_rx_errors_total": rx_errors_total,
        "network_tx_errors_total": tx_errors_total,
        "duration_s": total_duration,
        "tx_bytes": tx_bytes_total,
        "rx_bytes": rx_bytes_total,
        "tx_plus_rx_bytes": tx_bytes_total + rx_bytes_total,
        "tx_bytes_per_second": tx_bytes_total / total_duration if total_duration > 0 else 0.0,
        "rx_bytes_per_second": rx_bytes_total / total_duration if total_duration > 0 else 0.0,
        "tx_plus_rx_bytes_per_second": (tx_bytes_total + rx_bytes_total) / total_duration if total_duration > 0 else 0.0,
        "tx_bytes_rate": tx_bytes_total / total_duration if total_duration > 0 else 0.0,
        "rx_bytes_rate": rx_bytes_total / total_duration if total_duration > 0 else 0.0,
        "tx_plus_rx_bytes_rate": (tx_bytes_total + rx_bytes_total) / total_duration if total_duration > 0 else 0.0,
        "tx_packets": tx_packets_total,
        "rx_packets": rx_packets_total,
        "tx_plus_rx_packets": tx_packets_total + rx_packets_total,
        "tx_packets_per_second": tx_packets_total / total_duration if total_duration > 0 else 0.0,
        "rx_packets_per_second": rx_packets_total / total_duration if total_duration > 0 else 0.0,
        "tx_plus_rx_packets_per_second": (tx_packets_total + rx_packets_total) / total_duration if total_duration > 0 else 0.0,
        "tx_packets_rate": tx_packets_total / total_duration if total_duration > 0 else 0.0,
        "rx_packets_rate": rx_packets_total / total_duration if total_duration > 0 else 0.0,
        "tx_plus_rx_packets_rate": (tx_packets_total + rx_packets_total) / total_duration if total_duration > 0 else 0.0,
    }

    return {
        "per_node_time_series": per_node_ts,
        "aggregate_time_series": aggregate_ts,
        "summary": summary,
    }
