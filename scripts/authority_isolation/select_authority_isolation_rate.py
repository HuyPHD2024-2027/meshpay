#!/usr/bin/env python3
"""Select the highest calibrated offered load that is safe for every routing."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--minimum-confirmation-rate", type=float, default=95.0)
    parser.add_argument("--maximum-p95-quorum-latency", type=float, default=30.0,
                        help="Maximum median run-level p95 TTQ in seconds.")
    args = parser.parse_args()
    rows = json.loads(args.summary.read_text(encoding="utf-8"))
    grouped = defaultdict(list)
    routings = set()
    for row in rows:
        if row.get("exit_code") not in {None, 0}:
            continue
        routing = row.get("param.routing")
        rate = row.get("param.payment_rate")
        if routing is None or rate is None:
            continue
        routings.add(str(routing))
        grouped[(float(rate), str(routing))].append(row)
    passing = []
    details = []
    for rate in sorted({key[0] for key in grouped}):
        rate_passes = True
        for routing in sorted(routings):
            group = grouped.get((rate, routing), [])
            confirmations = [float(row["payment_confirmation_rate_percent"]) for row in group
                             if isinstance(row.get("payment_confirmation_rate_percent"), (int, float))]
            latencies = [float(row["p95_time_to_quorum_ms"]) / 1000.0 for row in group
                         if isinstance(row.get("p95_time_to_quorum_ms"), (int, float))]
            confirmation = statistics.median(confirmations) if confirmations else None
            latency = statistics.median(latencies) if latencies else None
            passed = bool(confirmation is not None and latency is not None
                          and confirmation >= args.minimum_confirmation_rate
                          and latency < args.maximum_p95_quorum_latency)
            rate_passes &= passed
            details.append({"rate": rate, "routing": routing, "runs": len(group),
                            "median_confirmation_rate_percent": confirmation,
                            "median_p95_quorum_latency_s": latency, "passes": passed})
        if rate_passes and routings:
            passing.append(rate)
    selected = max(passing) if passing else 1.0
    print(json.dumps({"selected_payment_rate": selected, "fallback_used": not passing,
                      "criteria": {"minimum_confirmation_rate_percent": args.minimum_confirmation_rate,
                                   "maximum_p95_quorum_latency_s": args.maximum_p95_quorum_latency},
                      "details": details}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
