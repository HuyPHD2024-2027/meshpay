#!/usr/bin/env python3

from __future__ import annotations

import json
import math
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


def latency_summary(
    values_ms: List[float],
    *,
    completed_count: Optional[int] = None,
    censored_count: Optional[int] = None,
    sample_scope: Optional[str] = None,
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "sample_count": len(values_ms),
    }

    if completed_count is not None:
        metadata["completed_count"] = completed_count

    if censored_count is not None:
        metadata["censored_count"] = censored_count

    if sample_scope is not None:
        metadata["sample_scope"] = sample_scope

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
            **metadata,
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
        **metadata,
    }


def kaplan_meier_time_to_quorum(
    created_by_order: Dict[str, Dict[str, Any]],
    confirmed_by_order: Dict[str, Dict[str, Any]],
    ended_at: float,
) -> Dict[str, Any]:
    observations: List[tuple[float, bool]] = []
    for order_id, created in created_by_order.items():
        start = float(created.get("time", ended_at))
        confirmed = confirmed_by_order.get(order_id)
        stop = float(confirmed.get("time", ended_at)) if confirmed else float(ended_at)
        observations.append((max(stop - start, 0.0), confirmed is not None))
    if not observations:
        return {"observations": 0, "events": 0, "censored": 0, "curve": []}

    at_risk = len(observations)
    survival = 1.0
    curve = [{"time_s": 0.0, "survival_probability": 1.0, "at_risk": at_risk,
              "confirmed": 0, "censored": 0}]
    for duration in sorted(set(value for value, _event in observations)):
        confirmed = sum(1 for value, event in observations if value == duration and event)
        censored = sum(1 for value, event in observations if value == duration and not event)
        if confirmed and at_risk:
            survival *= 1.0 - (confirmed / at_risk)
        curve.append({
            "time_s": duration, "survival_probability": survival,
            "at_risk": at_risk, "confirmed": confirmed, "censored": censored,
        })
        at_risk -= confirmed + censored
    return {
        "observations": len(observations),
        "events": sum(1 for _value, event in observations if event),
        "censored": sum(1 for _value, event in observations if not event),
        "curve": curve,
        "note": "Survival is the probability that quorum has not yet been reached; run-end observations are right-censored.",
    }


def _time_binned_metrics(
    events: List[Dict[str, Any]],
    created_by_order: Dict[str, Dict[str, Any]],
    confirmed_by_order: Dict[str, Dict[str, Any]],
    accepted_by_order: Dict[str, Dict[str, Any]],
    started_at: float,
    ended_at: float,
    reachability_samples: List[Dict[str, Any]],
    bin_size_s: float = 10.0,
) -> List[Dict[str, Any]]:
    bins = []
    count = max(1, int(math.ceil(max(ended_at - started_at, 0.0) / bin_size_s)))
    created_events = list(created_by_order.values())
    confirmed_events = list(confirmed_by_order.values())
    accepted_events = list(accepted_by_order.values())
    for index in range(count):
        start = started_at + index * bin_size_s
        end = min(start + bin_size_s, ended_at)
        duration = max(end - start, 0.000001)
        created = [event for event in created_events if start <= float(event.get("time", 0.0)) < end]
        confirmed = [event for event in confirmed_events if start <= float(event.get("time", 0.0)) < end]
        accepted = [event for event in accepted_events if start <= float(event.get("time", 0.0)) < end]
        latencies = []
        for event in confirmed:
            original = created_by_order.get(str(event.get("order_id")))
            if original:
                latencies.append((float(event["time"]) - float(original["time"])) * 1000.0)
        created_so_far = sum(1 for event in created_events if float(event.get("time", 0.0)) < end)
        confirmed_so_far = sum(1 for event in confirmed_events if float(event.get("time", 0.0)) < end)
        samples = [sample for sample in reachability_samples if start <= float(sample.get("time", 0.0)) < end]
        latest = samples[-1] if samples else None
        bins.append({
            "index": index, "start": start, "end": end,
            "relative_start_s": start - started_at, "relative_end_s": end - started_at,
            "created": len(created), "confirmed": len(confirmed), "accepted": len(accepted),
            "created_tps": len(created) / duration,
            "confirmed_tps": len(confirmed) / duration,
            "accepted_tps": len(accepted) / duration,
            "time_to_quorum_ms_p50": percentile(latencies, 50),
            "time_to_quorum_ms_p95": percentile(latencies, 95),
            "outstanding_payments": max(created_so_far - confirmed_so_far, 0),
            "backlog_size": max(created_so_far - confirmed_so_far, 0),
            "reachable_authority_count": latest.get("reachable_authority_count") if latest else None,
            "reachable_voting_power": latest.get("actual_reachable_power") if latest else None,
            "weight_epoch": latest.get("epoch") if latest else None,
        })
    return bins


def _recovery_metrics(events: List[Dict[str, Any]], bins: List[Dict[str, Any]]) -> Dict[str, Any]:
    phases = _attack_phase_windows(events)
    if not phases:
        return {}
    attack_start, attack_stop = phases["during"]
    confirmations = sorted(float(event["time"]) for event in events
                           if event.get("event") == "confirmation_created" and "time" in event)
    first_post = next((value for value in confirmations if value >= attack_stop), None)
    pre_bins = [row for row in bins if row["start"] >= phases["before"][0] and row["end"] <= attack_start]
    baseline = statistics.mean([row["confirmed_tps"] for row in pre_bins]) if pre_bins else None
    recovery_time = None
    if baseline is not None and baseline > 0:
        threshold = 0.9 * baseline
        post_bins = [row for row in bins if row["start"] >= attack_stop]
        for index in range(max(len(post_bins) - 2, 0)):
            window = post_bins[index:index + 3]
            if len(window) == 3 and all(row["confirmed_tps"] >= threshold for row in window):
                recovery_time = window[0]["start"] - attack_stop
                break
    created_at_restoration = sum(1 for event in events if event.get("event") == "payment_created"
                                 and float(event.get("time", 0.0)) < attack_stop)
    confirmed_at_restoration = sum(1 for event in events if event.get("event") == "confirmation_created"
                                   and float(event.get("time", 0.0)) < attack_stop)
    return {
        "time_to_first_post_restoration_confirmation_s": (
            first_post - attack_stop if first_post is not None else None
        ),
        "pre_attack_confirmation_tps": baseline,
        "recovery_threshold_tps": 0.9 * baseline if baseline is not None and baseline > 0 else None,
        "time_to_recover_90_percent_three_bins_s": recovery_time,
        "backlog_at_restoration": max(created_at_restoration - confirmed_at_restoration, 0),
    }


def _authority_phase_activity(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    phases = _attack_phase_windows(events)
    if not phases:
        return {}
    result: Dict[str, Dict[str, Dict[str, float]]] = {}
    for phase, (start, end) in phases.items():
        authorities: Dict[str, Dict[str, float]] = {}
        certificate_count = 0
        for event in events:
            timestamp = float(event.get("time", 0.0))
            if not start <= timestamp < end:
                continue
            if event.get("event") == "payment_payload_delivered" and event.get("payload_type") == "transfer_order":
                authority = str(event.get("node") or "")
                row = authorities.setdefault(authority, {"transfer_deliveries": 0, "signed_votes": 0, "certificates": 0})
                row["transfer_deliveries"] += 1
            elif event.get("event") == "authority_signed_transfer":
                authority = str(event.get("authority") or event.get("node") or "")
                row = authorities.setdefault(authority, {"transfer_deliveries": 0, "signed_votes": 0, "certificates": 0})
                row["signed_votes"] += 1
            elif event.get("event") == "confirmation_created":
                certificate_count += 1
                for authority in event.get("signers", []):
                    row = authorities.setdefault(str(authority), {"transfer_deliveries": 0, "signed_votes": 0, "certificates": 0})
                    row["certificates"] += 1
        for row in authorities.values():
            row["certificate_share_percent"] = safe_div(row["certificates"], certificate_count) * 100.0
        result[phase] = dict(sorted(authorities.items()))
    return result



def _attack_phase_windows(events: List[Dict[str, Any]]) -> Dict[str, tuple[float, float]]:
    starts = [
        event
        for event in events
        if event.get("event") == "attack_started" and "time" in event
    ]
    stops = [
        event
        for event in events
        if event.get("event") == "attack_stopped" and "time" in event
    ]
    if not starts or not stops:
        return {}

    attack_start = float(starts[0]["time"])
    attack_stop = float(stops[-1]["time"])
    tpre = float(starts[0].get("tpre", 0.0) or 0.0)
    tpost = float(starts[0].get("tpost", 0.0) or 0.0)
    return {
        "before": (attack_start - max(tpre, 0.0), attack_start),
        "during": (attack_start, attack_stop),
        "after": (attack_stop, attack_stop + max(tpost, 0.0)),
    }


def _phase_for_time(t: float, phases: Dict[str, tuple[float, float]]) -> str | None:
    for phase, (start, end) in phases.items():
        if start <= t < end:
            return phase
    return None



def _account_host(account: Any) -> str:
    text = str(account or "")
    return text.split("/", 1)[0] if "/" in text else text


def _new_stage_counts() -> Dict[str, int]:
    return {
        "payment_created": 0,
        "transfer_order_delivered_to_authority": 0,
        "authority_signed_transfer": 0,
        "signed_transfer_order_delivered_to_sender": 0,
        "confirmation_created": 0,
        "payment_accepted": 0,
    }


def _increment_stage(counts: Dict[str, Dict[str, int]], key: str, stage: str, amount: int = 1) -> None:
    if not key:
        key = "unknown"
    if key not in counts:
        counts[key] = _new_stage_counts()
    counts[key][stage] += amount


def _payment_stage_funnel(
    events: List[Dict[str, Any]],
    created_by_order: Dict[str, Dict[str, Any]],
    confirmed_by_order: Dict[str, Dict[str, Any]],
    accepted_by_order: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    phases = _attack_phase_windows(events)
    if not phases:
        return {}

    transfer_deliveries_by_order: Dict[str, set[str]] = {}
    signed_by_order: Dict[str, set[str]] = {}
    signed_sender_deliveries_by_order: Dict[str, set[str]] = {}

    for event in events:
        order_id = event.get("order_id")
        if not order_id:
            continue
        order_id = str(order_id)
        event_name = event.get("event")
        if event_name == "payment_payload_delivered":
            payload_type = event.get("payload_type")
            node = str(event.get("node") or "")
            if payload_type == "transfer_order":
                transfer_deliveries_by_order.setdefault(order_id, set()).add(node)
            elif payload_type == "signed_transfer_order":
                created = created_by_order.get(order_id)
                sender_host = str((created or {}).get("sender_host") or _account_host((created or {}).get("sender")))
                if node and node == sender_host:
                    signed_sender_deliveries_by_order.setdefault(order_id, set()).add(node)
        elif event_name == "authority_signed_transfer":
            authority = str(event.get("authority") or event.get("node") or "")
            signed_by_order.setdefault(order_id, set()).add(authority)

    cohorts: Dict[str, Any] = {}
    for phase, (start, end) in phases.items():
        order_ids = [
            order_id
            for order_id, created in created_by_order.items()
            if start <= float(created.get("time", 0.0)) < end
        ]
        totals = _new_stage_counts()
        by_sender_node: Dict[str, Dict[str, int]] = {}
        by_recipient_node: Dict[str, Dict[str, int]] = {}
        by_authority_node: Dict[str, Dict[str, int]] = {}

        for order_id in order_ids:
            created = created_by_order[order_id]
            sender_node = str(created.get("sender_host") or _account_host(created.get("sender")))
            recipient_node = str(created.get("recipient_host") or _account_host(created.get("recipient")))

            reached = {
                "payment_created": True,
                "transfer_order_delivered_to_authority": bool(transfer_deliveries_by_order.get(order_id)),
                "authority_signed_transfer": bool(signed_by_order.get(order_id)),
                "signed_transfer_order_delivered_to_sender": bool(signed_sender_deliveries_by_order.get(order_id)),
                "confirmation_created": order_id in confirmed_by_order,
                "payment_accepted": order_id in accepted_by_order,
            }
            for stage, did_reach in reached.items():
                if not did_reach:
                    continue
                totals[stage] += 1
                _increment_stage(by_sender_node, sender_node, stage)
                _increment_stage(by_recipient_node, recipient_node, stage)

            for authority in transfer_deliveries_by_order.get(order_id, set()):
                _increment_stage(by_authority_node, authority, "transfer_order_delivered_to_authority")
            for authority in signed_by_order.get(order_id, set()):
                _increment_stage(by_authority_node, authority, "authority_signed_transfer")

        cohorts[phase] = {
            "window_start": start,
            "window_end": end,
            "duration_s": max(end - start, 0.0),
            "totals": totals,
            "by_sender_node": dict(sorted(by_sender_node.items())),
            "by_recipient_node": dict(sorted(by_recipient_node.items())),
            "by_authority_node": dict(sorted(by_authority_node.items())),
        }

    return {
        "phase_windows": {
            phase: {"start": start, "end": end, "duration_s": max(end - start, 0.0)}
            for phase, (start, end) in phases.items()
        },
        "cohorts_by_created_phase": cohorts,
        "stage_semantics": {
            "transfer_order_delivered_to_authority": "unique created orders with at least one transfer_order delivery event",
            "authority_signed_transfer": "unique created orders signed by at least one authority",
            "signed_transfer_order_delivered_to_sender": "unique created orders with a signed_transfer_order delivered back to the sender host",
            "confirmation_created": "unique created orders that reached quorum by run end",
            "payment_accepted": "unique created orders accepted by the recipient by run end",
        },
    }

def _payment_phase_cohorts(
    events: List[Dict[str, Any]],
    created_by_order: Dict[str, Dict[str, Any]],
    confirmed_by_order: Dict[str, Dict[str, Any]],
    accepted_by_order: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    phases = _attack_phase_windows(events)
    if not phases:
        return {}

    cohorts: Dict[str, Any] = {}
    for phase, (start, end) in phases.items():
        order_ids = [
            order_id
            for order_id, created in created_by_order.items()
            if start <= float(created.get("time", 0.0)) < end
        ]
        confirmed_ids = [order_id for order_id in order_ids if order_id in confirmed_by_order]
        accepted_ids = [order_id for order_id in order_ids if order_id in accepted_by_order]
        confirmed_within_window_ids = [
            order_id
            for order_id in confirmed_ids
            if float(confirmed_by_order[order_id].get("time", 0.0)) < end
        ]
        accepted_within_window_ids = [
            order_id
            for order_id in accepted_ids
            if float(accepted_by_order[order_id].get("time", 0.0)) < end
        ]
        quorum_latencies = [
            (float(confirmed_by_order[order_id]["time"]) - float(created_by_order[order_id]["time"])) * 1000.0
            for order_id in confirmed_ids
        ]
        acceptance_latencies = [
            (float(accepted_by_order[order_id]["time"]) - float(created_by_order[order_id]["time"])) * 1000.0
            for order_id in accepted_ids
        ]
        created_count = len(order_ids)
        confirmed_count = len(confirmed_ids)
        accepted_count = len(accepted_ids)
        cohorts[phase] = {
            "window_start": start,
            "window_end": end,
            "duration_s": max(end - start, 0.0),
            "payments_created": created_count,
            "payments_confirmed_by_run_end": confirmed_count,
            "payments_accepted_by_run_end": accepted_count,
            "payments_confirmed_within_phase_window": len(confirmed_within_window_ids),
            "payments_accepted_within_phase_window": len(accepted_within_window_ids),
            "payments_censored_for_quorum": max(created_count - confirmed_count, 0),
            "payments_censored_for_acceptance": max(created_count - accepted_count, 0),
            "confirmation_rate_by_run_end_percent": safe_div(confirmed_count, created_count) * 100.0,
            "acceptance_rate_by_run_end_percent": safe_div(accepted_count, created_count) * 100.0,
            "confirmation_rate_within_phase_window_percent": (
                safe_div(len(confirmed_within_window_ids), created_count) * 100.0
            ),
            "acceptance_rate_within_phase_window_percent": (
                safe_div(len(accepted_within_window_ids), created_count) * 100.0
            ),
            "time_to_quorum_ms": latency_summary(
                quorum_latencies,
                completed_count=confirmed_count,
                censored_count=max(created_count - confirmed_count, 0),
                sample_scope="created_in_phase_confirmed_by_run_end",
            ),
            "time_to_acceptance_ms": latency_summary(
                acceptance_latencies,
                completed_count=accepted_count,
                censored_count=max(created_count - accepted_count, 0),
                sample_scope="created_in_phase_accepted_by_run_end",
            ),
        }

    return {
        "phase_windows": {
            phase: {"start": start, "end": end, "duration_s": max(end - start, 0.0)}
            for phase, (start, end) in phases.items()
        },
        "cohorts_by_created_phase": cohorts,
        "note": (
            "Cohorts group payments by payment_created time. Whole-run latency "
            "summaries still include only completed payments, so compare with "
            "censored counts before interpreting latency improvements."
        ),
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

    # ---------------------------------------------------------------------------
    # Hop count metrics (available when DTN router reports hops on delivery)
    # ---------------------------------------------------------------------------
    hop_counts: List[float] = [
        float(e["hop_count"])
        for e in rx_events
        if isinstance(e.get("hop_count"), (int, float))
    ]

    bundle_latencies_ms: List[float] = [
        float(e["bundle_latency_ms"])
        for e in rx_events
        if isinstance(e.get("bundle_latency_ms"), (int, float))
    ]

    payments_created = len(created_by_order)
    payments_confirmed = len(confirmed_by_order)
    payments_accepted_count = len(accepted_by_order)
    payments_unconfirmed = max(payments_created - payments_confirmed, 0)
    payments_unaccepted = max(payments_created - payments_accepted_count, 0)

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
        "payments_unconfirmed": payments_unconfirmed,
        "payments_unaccepted": payments_unaccepted,
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

    phase_cohorts = _payment_phase_cohorts(
        events=events,
        created_by_order=created_by_order,
        confirmed_by_order=confirmed_by_order,
        accepted_by_order=accepted_by_order,
    )
    post_attack_funnel = _payment_stage_funnel(
        events=events,
        created_by_order=created_by_order,
        confirmed_by_order=confirmed_by_order,
        accepted_by_order=accepted_by_order,
    )
    reachability_samples = load_jsonl(log_dir / "authority_reachability.jsonl")
    time_bins = _time_binned_metrics(
        events=events,
        created_by_order=created_by_order,
        confirmed_by_order=confirmed_by_order,
        accepted_by_order=accepted_by_order,
        started_at=started_at,
        ended_at=ended_at,
        reachability_samples=reachability_samples,
    )
    recovery = _recovery_metrics(events, time_bins)
    during_cohort = (
        phase_cohorts.get("cohorts_by_created_phase", {}).get("during", {})
        if phase_cohorts else {}
    )
    recovery["attack_window_payments_confirmed_by_run_end_percent"] = during_cohort.get(
        "confirmation_rate_by_run_end_percent"
    )

    return {
        "summary": summary,
        "latency_ms": {
            "time_to_quorum": latency_summary(
                time_to_quorum_ms,
                completed_count=payments_confirmed,
                censored_count=payments_unconfirmed,
                sample_scope="confirmed_payments",
            ),
            "time_to_acceptance": latency_summary(
                time_to_acceptance_ms,
                completed_count=payments_accepted_count,
                censored_count=payments_unaccepted,
                sample_scope="accepted_payments",
            ),
        },
        "hop_count": {
            "samples": len(hop_counts),
            "min": min(hop_counts) if hop_counts else None,
            "max": max(hop_counts) if hop_counts else None,
            "avg": (sum(hop_counts) / len(hop_counts)) if hop_counts else None,
            "p50": percentile(hop_counts, 50),
            "p90": percentile(hop_counts, 90),
            "p95": percentile(hop_counts, 95),
            "note": (
                "hop_count counts relay nodes traversed by a bundle. "
                "Available only when MESHPAY_DTN_EVENT_LOG is enabled or "
                "the IPC delivery socket reports hops."
            ),
        },
        "bundle_latency_ms": latency_summary(
            bundle_latencies_ms,
            sample_scope="rx_bundles",
        ),
        "payload_type_counts": payload_type_counts,
        "phase_cohorts": phase_cohorts,
        "post_attack_funnel": post_attack_funnel,
        "time_bins_10s": time_bins,
        "recovery": recovery,
        "kaplan_meier_time_to_quorum": kaplan_meier_time_to_quorum(
            created_by_order, confirmed_by_order, ended_at,
        ),
        "authority_phase_activity": _authority_phase_activity(events),
        "authority_reachability_samples": reachability_samples,
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
