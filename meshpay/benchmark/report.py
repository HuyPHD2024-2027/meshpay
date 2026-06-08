#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict


def write_json_report(report: Dict[str, Any], output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")


def flatten(prefix: str, value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        result = {}

        for key, nested_value in value.items():
            nested_prefix = f"{prefix}.{key}" if prefix else str(key)
            result.update(flatten(nested_prefix, nested_value))

        return result

    return {prefix: value}


def write_summary_csv(report: Dict[str, Any], output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    flattened = flatten("", report)

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])

        for key in sorted(flattened):
            writer.writerow([key, flattened[key]])


def write_reports(report: Dict[str, Any], output_dir: str | Path) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    write_json_report(report, output_dir / "benchmark.json")
    write_summary_csv(report, output_dir / "benchmark.csv")