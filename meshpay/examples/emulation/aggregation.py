"""Aggregate MeshPay emulation campaign JSON outputs into summary CSV."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev
from typing import Any, Dict, Iterable, List, Tuple


GROUP_KEYS = ("campaign", "scenario_name", "routing", "authorities", "clients", "wireless_range", "mobility_speed")
METRICS = (
    "finality_rate",
    "successful_tx",
    "submitted_payments",
    "avg_latency_ms",
    "control_bytes",
    "data_bytes",
    "total_bytes_per_success",
    "avg_buffer_size",
    "certificate_assembly_success_rate",
    "avg_vote_rtt_ms",
    "avg_handoff_interruption_ms",
    "tps",
    "peer_discovery_events",
    "contact_events",
)


def _stats_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    stats = payload.get("stats")
    return stats if isinstance(stats, dict) else payload


def load_run_records(paths: Iterable[str | Path]) -> List[Dict[str, Any]]:
    """Load run records from campaign JSON files."""

    records: List[Dict[str, Any]] = []
    for path in paths:
        run_path = Path(path)
        with run_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        stats = dict(_stats_payload(payload))
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        if isinstance(metadata, dict):
            for key, value in metadata.items():
                stats.setdefault(key, value)
        stats["run_file"] = str(run_path)
        success = float(stats.get("successful_tx", 0) or 0)
        total_bytes = float(stats.get("control_bytes", 0) or 0) + float(stats.get("data_bytes", 0) or 0)
        stats["total_bytes_per_success"] = total_bytes / success if success > 0 else 0.0
        if not stats.get("submitted_payments"):
            stats["submitted_payments"] = stats.get("total_tx", 0)
        records.append(stats)
    return records


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _summarize(values: List[float]) -> Tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    avg = mean(values)
    sd = stdev(values) if len(values) > 1 else 0.0
    ci95 = 1.96 * sd / math.sqrt(len(values)) if len(values) > 1 else 0.0
    return avg, sd, ci95


def aggregate_records(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compute mean/std/95% CI rows grouped by scenario and protocol."""

    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[tuple(record.get(key, "") for key in GROUP_KEYS)].append(record)

    rows: List[Dict[str, Any]] = []
    for group_key, items in sorted(groups.items()):
        row = {key: value for key, value in zip(GROUP_KEYS, group_key)}
        row["runs"] = len(items)
        for metric in METRICS:
            avg, sd, ci95 = _summarize([_num(item.get(metric)) for item in items])
            row[f"{metric}_mean"] = avg
            row[f"{metric}_std"] = sd
            row[f"{metric}_ci95"] = ci95
        rows.append(row)
    return rows


def write_summary_csv(records: Iterable[Dict[str, Any]], output_path: str | Path) -> List[Dict[str, Any]]:
    """Write grouped campaign summary CSV and return the rows."""

    rows = aggregate_records(records)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(GROUP_KEYS) + ["runs"] + [f"{metric}_{suffix}" for metric in METRICS for suffix in ("mean", "std", "ci95")]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def aggregate_json_dir(results_dir: str | Path, summary_path: str | Path | None = None) -> List[Dict[str, Any]]:
    """Aggregate all per-run JSON files under a campaign directory."""

    root = Path(results_dir)
    paths = sorted(path for path in root.rglob("*.json") if path.name != "campaign_manifest.json")
    records = load_run_records(paths)
    return write_summary_csv(records, summary_path or root / "summary.csv")
