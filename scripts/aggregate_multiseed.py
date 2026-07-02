#!/usr/bin/env python3
"""Aggregate MeshPay benchmark summaries across multiple seed runs.

Usage:
    python3 scripts/aggregate_multiseed.py \
        logs/benchmarks/verification_seed_*/summary.csv \
        -o logs/benchmarks/verification_multiseed
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


GROUP_COLUMNS = [
    "param.routing",
    "param.attack_loss_probability",
    "param.payment_rate",
    "param.medium",
    "param.clients",
    "param.authorities",
    "param.node_range",
]

METRIC_COLUMNS = [
    "payment_confirmation_rate_percent",
    "payment_acceptance_rate_percent",
    "avg_time_to_quorum_ms",
    "p50_time_to_quorum_ms",
    "p95_time_to_quorum_ms",
    "confirmed_tps",
    "accepted_tps",
    "tx_plus_rx_bytes_per_second",
    "network_tx_bytes_per_second",
    "network_rx_bytes_per_second",
    "network_tx_plus_rx_bytes_per_second",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "summaries",
        nargs="+",
        help="Input summary.csv files, for example logs/benchmarks/verification_seed_*/summary.csv",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="logs/benchmarks/verification_multiseed",
        help="Directory for all_seeds.csv and summary_mean_std.csv.",
    )
    return parser.parse_args()


def read_rows(paths: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                row["seed_run"] = path.parent.name
                row["source_summary"] = str(path)
                rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result):
        return None
    return result


def aggregate(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = tuple(row.get(col, "") for col in GROUP_COLUMNS)
        groups[key].append(row)

    aggregate_rows: list[dict[str, Any]] = []
    for key, group_rows in sorted(groups.items()):
        out: dict[str, Any] = dict(zip(GROUP_COLUMNS, key))
        out["seed_count"] = len({row.get("seed_run", "") for row in group_rows})
        out["run_count"] = len(group_rows)

        for metric in METRIC_COLUMNS:
            values = [
                value
                for row in group_rows
                for value in [as_float(row.get(metric))]
                if value is not None
            ]
            out[f"{metric}.count"] = len(values)
            out[f"{metric}.mean"] = statistics.mean(values) if values else ""
            out[f"{metric}.std"] = statistics.stdev(values) if len(values) > 1 else 0.0 if values else ""
            out[f"{metric}.min"] = min(values) if values else ""
            out[f"{metric}.max"] = max(values) if values else ""

        aggregate_rows.append(out)

    return aggregate_rows


def main() -> int:
    args = parse_args()
    paths = [Path(path) for path in args.summaries]
    missing = [path for path in paths if not path.exists()]
    if missing:
        raise SystemExit(f"Missing input summary: {missing[0]}")

    rows = read_rows(paths)
    output_dir = Path(args.output_dir)

    if rows:
        all_fields = list(rows[0].keys())
    else:
        all_fields = ["seed_run", "source_summary"]
    write_csv(output_dir / "all_seeds.csv", rows, all_fields)

    aggregate_rows = aggregate(rows)
    aggregate_fields = GROUP_COLUMNS + ["seed_count", "run_count"]
    for metric in METRIC_COLUMNS:
        aggregate_fields.extend(
            [
                f"{metric}.count",
                f"{metric}.mean",
                f"{metric}.std",
                f"{metric}.min",
                f"{metric}.max",
            ]
        )
    write_csv(output_dir / "summary_mean_std.csv", aggregate_rows, aggregate_fields)

    print(f"Wrote {output_dir / 'all_seeds.csv'}")
    print(f"Wrote {output_dir / 'summary_mean_std.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
