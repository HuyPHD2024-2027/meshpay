#!/usr/bin/env python3
"""Generate deterministic synthetic packet-loss attack data for plotting.

This script intentionally creates dummy metrics. It does not rewrite real
benchmark results or change routing behavior. The generated data is shaped to
illustrate packet-loss trends for the three routing protocols.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

LOSSES = [0.0, 0.25, 0.5, 0.8]
ROUTINGS = ["epidemic", "spray-and-wait", "prophet"]
PAYMENT_RATE = 10.0
PAYMENTS_CREATED = 400
DURATION_S = 150.0
ATTACK_START_S = 30.0
ATTACK_STOP_S = 90.0

CONFIRMATION_RATE = {
    "epidemic": [53.0, 47.0, 43.0, 37.0],
    "spray-and-wait": [98.0, 72.0, 62.0, 48.0],
    "prophet": [79.0, 68.0, 61.0, 56.0],
}

ACCEPTANCE_RATE = {
    "epidemic": [28.0, 23.0, 19.0, 15.0],
    "spray-and-wait": [86.0, 64.0, 55.0, 42.0],
    "prophet": [70.0, 60.0, 55.0, 51.0],
}

QUORUM_LATENCY_S = {
    "epidemic": [3.1, 3.8, 4.9, 6.2],
    "prophet": [4.0, 4.9, 6.0, 7.1],
    "spray-and-wait": [5.2, 5.8, 7.2, 8.9],
}

NETWORK_TX_KIB_S = {
    "epidemic": [230.0, 190.0, 165.0, 120.0],
    "spray-and-wait": [210.0, 170.0, 145.0, 105.0],
    "prophet": [190.0, 150.0, 125.0, 90.0],
}

NETWORK_RX_KIB_S = {
    "epidemic": [170.0, 140.0, 120.0, 85.0],
    "spray-and-wait": [155.0, 125.0, 105.0, 78.0],
    "prophet": [140.0, 110.0, 90.0, 65.0],
}

AVG_HOP_COUNT = {
    "epidemic": [3.15, 3.31, 3.44, 3.36],
    "spray-and-wait": [2.19, 2.23, 2.33, 2.24],
    "prophet": [1.72, 1.78, 1.83, 1.80],
}

ROUTING_ABBR = {
    "epidemic": "Epi",
    "spray-and-wait": "SnW",
    "prophet": "Prophet",
}


def _loss_label(loss: float) -> str:
    if loss == 0.0:
        return "loss0"
    return f"loss{str(loss).replace('.', 'p')}"


def _bytes_per_second(kib_per_second: float) -> float:
    return kib_per_second * 1024.0


def _payments(rate_percent: float) -> int:
    return int(round(PAYMENTS_CREATED * rate_percent / 100.0))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _reference_log_for_routing(root_dir: Path, routing: str) -> Path:
    if routing == "prophet":
        return (
            root_dir
            / "logs"
            / "benchmarks"
            / "prophet_loss_seed_21"
            / "003_c6_a4_r1000_rate10_mmesh_rtProphet_attPL_loss0p5"
            / "payment.log"
        )
    return (
        root_dir
        / "logs"
        / "benchmarks"
        / "saw_loss_seed_21"
        / "003_c6_a4_r1000_rate10_mmesh_rtSnW_attPL_loss0p5"
        / "payment.log"
    )


def _load_reference_payload_template(root_dir: Path, routing: str) -> list[tuple[int, int]]:
    reference_log = _reference_log_for_routing(root_dir, routing)
    if not reference_log.exists():
        return []

    events = []
    with reference_log.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))

    attack_started = next((e for e in events if e.get("event") == "attack_started"), None)
    if attack_started is None:
        return []

    t0 = float(attack_started["time"]) - float(attack_started.get("tpre", ATTACK_START_S))
    bins = [[0, 0] for _ in range(int(DURATION_S) + 1)]
    for event in events:
        name = event.get("event")
        if name not in {"payload_injected", "payment_payload_delivered"} or "time" not in event:
            continue
        bucket = int(math.floor(float(event["time"]) - t0))
        if bucket < 0 or bucket >= len(bins):
            continue
        size = int(event.get("payload_size_bytes", 0) or 0)
        if name == "payload_injected":
            bins[bucket][0] += size
        else:
            bins[bucket][1] += size
    return [(tx, rx) for tx, rx in bins]


def _fallback_payload_template() -> list[tuple[int, int]]:
    tx_shape = [14504, 15793, 14761, 17331, 18877, 20174, 21451, 31039, 25771, 17115,
                31300, 36278, 64008, 31180, 28791, 100604, 87706, 98942, 49404, 81228,
                67852, 29021, 59715, 32894, 59161, 41236, 27343, 47610, 17788, 49495]
    rx_shape = [0, 1962, 393, 4317, 6949, 9460, 11426, 22145, 12778, 7569,
                25282, 9641, 30119, 14074, 4232, 152637, 40263, 129514, 48981, 80420,
                47885, 87927, 91630, 27543, 63164, 48411, 20951, 62291, 12127, 26584]
    return [(tx_shape[i % len(tx_shape)], rx_shape[i % len(rx_shape)]) for i in range(int(DURATION_S) + 1)]


def _write_payment_log(
    root_dir: Path,
    run_dir: Path,
    routing: str,
    loss: float,
    tx_kib_s: float,
    rx_kib_s: float,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    template = _load_reference_payload_template(root_dir, routing) or _fallback_payload_template()
    if len(template) < int(DURATION_S) + 1:
        template.extend(_fallback_payload_template()[len(template):])

    tx_scale = tx_kib_s / NETWORK_TX_KIB_S["spray-and-wait"][LOSSES.index(0.5)]
    rx_scale = rx_kib_s / NETWORK_RX_KIB_S["spray-and-wait"][LOSSES.index(0.5)]
    samples = [
        {"event": "attack_started", "time": ATTACK_START_S, "loss_probability": loss, "tpre": ATTACK_START_S, "tatk": ATTACK_STOP_S - ATTACK_START_S, "tpost": DURATION_S - ATTACK_STOP_S},
        {"event": "attack_stopped", "time": ATTACK_STOP_S, "loss_probability": loss, "tpre": ATTACK_START_S, "tatk": ATTACK_STOP_S - ATTACK_START_S, "tpost": DURATION_S - ATTACK_STOP_S},
    ]

    preattack_len = int(ATTACK_START_S)
    attack_len = int(ATTACK_STOP_S - ATTACK_START_S)
    for second in range(int(DURATION_S)):
        if routing == "epidemic":
            if second < ATTACK_START_S:
                source_second = second
                phase_factor = 1.0
            elif second < ATTACK_STOP_S:
                source_second = second
                phase_factor = 0.48
            else:
                source_second = second % preattack_len
                phase_factor = 0.92
        elif routing == "spray-and-wait":
            if second < ATTACK_START_S:
                source_second = (second + 7) % preattack_len
                phase_factor = 0.96
            elif second < ATTACK_STOP_S:
                source_second = int(ATTACK_START_S) + ((second - int(ATTACK_START_S) + 11) % attack_len)
                phase_factor = 0.44
            else:
                source_second = (second + 13) % preattack_len
                phase_factor = 0.88
        else:
            if second < ATTACK_START_S:
                source_second = (second * 3 + 5) % preattack_len
                phase_factor = 0.90
            elif second < ATTACK_STOP_S:
                source_second = int(ATTACK_START_S) + ((second - int(ATTACK_START_S) + 19) % attack_len)
                phase_factor = 0.36
            else:
                source_second = (second * 2 + 17) % preattack_len
                phase_factor = 0.82

        ref_tx, ref_rx = template[source_second]
        wobble = 1.0 + (((second % 9) - 4) * 0.018)
        tx_size = int(ref_tx * tx_scale * phase_factor * wobble)
        rx_size = int(ref_rx * rx_scale * phase_factor * (2.0 - wobble))
        event_time = float(second) + 0.15
        if tx_size > 0:
            samples.append({
                "event": "payload_injected",
                "time": event_time,
                "payload_size_bytes": tx_size,
                "source": f"dummy_from_{routing}_reference",
            })
        if rx_size > 0:
            samples.append({
                "event": "payment_payload_delivered",
                "time": event_time + 0.25,
                "payload_size_bytes": rx_size,
                "source": f"dummy_from_{routing}_reference",
            })

    samples.sort(key=lambda event: (float(event.get("time", 0.0)), str(event.get("event", ""))))
    with (run_dir / "payment.log").open("w", encoding="utf-8") as f:
        for event in samples:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    benchmark = {
        "timing": {"started_at": 0.0, "ended_at": DURATION_S},
        "config": {
            "routing": routing,
            "payment_rate": PAYMENT_RATE,
            "duration": DURATION_S,
            "attack_tpre": ATTACK_START_S,
            "attack_tatk": ATTACK_STOP_S - ATTACK_START_S,
            "attack_tpost": DURATION_S - ATTACK_STOP_S,
        },
        "attack": {
            "type": "packetloss",
            "loss_probability": loss,
            "tpre": ATTACK_START_S,
            "tatk": ATTACK_STOP_S - ATTACK_START_S,
            "tpost": DURATION_S - ATTACK_STOP_S,
        },
    }
    _write_json(run_dir / "benchmark.json", benchmark)


def _summary_row(root_dir: Path, routing: str, loss: float, run_index: int) -> dict[str, Any]:
    idx = LOSSES.index(loss)
    run_id = f"{run_index:03d}_c6_a4_r1000_rate10_mmesh_rt{ROUTING_ABBR[routing]}_attPL_{_loss_label(loss)}"
    run_dir = root_dir / "dummy_logs" / run_id

    confirmation_rate = CONFIRMATION_RATE[routing][idx]
    acceptance_rate = ACCEPTANCE_RATE[routing][idx]
    quorum_latency_ms = QUORUM_LATENCY_S[routing][idx] * 1000.0
    avg_hop_count = AVG_HOP_COUNT[routing][idx]
    tx_bps = _bytes_per_second(NETWORK_TX_KIB_S[routing][idx])
    rx_bps = _bytes_per_second(NETWORK_RX_KIB_S[routing][idx])
    confirmed = _payments(confirmation_rate)
    accepted = _payments(acceptance_rate)

    if loss == 0.5:
        _write_payment_log(root_dir, run_dir, routing, loss, NETWORK_TX_KIB_S[routing][idx], NETWORK_RX_KIB_S[routing][idx])
    else:
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_json(run_dir / "benchmark.json", {
            "timing": {"started_at": 0.0, "ended_at": DURATION_S},
            "config": {"routing": routing, "payment_rate": PAYMENT_RATE, "duration": DURATION_S},
        })
        (run_dir / "payment.log").write_text("", encoding="utf-8")

    return {
        "exit_code": 0,
        "run_id": run_id,
        "run_dir": str(run_dir.resolve()),
        "param.routing": routing,
        "param.payment_rate": PAYMENT_RATE,
        "param.attack": "packetloss",
        "param.attack_loss_probability": loss,
        "param.attack_tpre": ATTACK_START_S,
        "param.attack_tatk": ATTACK_STOP_S - ATTACK_START_S,
        "param.attack_tpost": DURATION_S - ATTACK_STOP_S,
        "param.clients": 6,
        "param.authorities": 4,
        "param.duration": DURATION_S,
        "param.seed": 21,
        "payments_created": PAYMENTS_CREATED,
        "payments_confirmed": confirmed,
        "payments_unconfirmed": PAYMENTS_CREATED - confirmed,
        "payments_accepted": accepted,
        "payments_unaccepted": PAYMENTS_CREATED - accepted,
        "payment_confirmation_rate_percent": confirmation_rate,
        "payment_acceptance_rate_percent": acceptance_rate,
        "avg_time_to_quorum_ms": quorum_latency_ms,
        "avg_hop_count": avg_hop_count,
        "p50_time_to_quorum_ms": quorum_latency_ms * 0.92,
        "p95_time_to_quorum_ms": quorum_latency_ms * 1.35,
        "avg_time_to_acceptance_ms": quorum_latency_ms * 0.72,
        "p50_time_to_acceptance_ms": quorum_latency_ms * 0.65,
        "p95_time_to_acceptance_ms": quorum_latency_ms * 1.05,
        "time_to_quorum_completed_count": confirmed,
        "time_to_quorum_censored_count": PAYMENTS_CREATED - confirmed,
        "time_to_quorum_sample_count": confirmed,
        "network_tx_bytes_per_second": tx_bps,
        "network_rx_bytes_per_second": rx_bps,
        "network_tx_plus_rx_bytes_per_second": tx_bps + rx_bps,
        "network_tx_bytes": int(tx_bps * DURATION_S),
        "network_rx_bytes": int(rx_bps * DURATION_S),
        "network_tx_plus_rx_bytes": int((tx_bps + rx_bps) * DURATION_S),
        "tx_plus_rx_bytes_per_second": (tx_bps + rx_bps) * 0.08,
        "tx_bytes_per_second": tx_bps * 0.08,
        "rx_bytes_per_second": rx_bps * 0.08,
    }


def main() -> int:
    root_dir = Path(__file__).resolve().parents[1]
    output_path = root_dir / "dummy_figures" / "combined_seed21_dummy_summary.json"
    rows = []
    run_index = 1
    for routing in ROUTINGS:
        for loss in LOSSES:
            rows.append(_summary_row(root_dir, routing, loss, run_index))
            run_index += 1

    _write_json(output_path, rows)
    print(f"Wrote {len(rows)} dummy summary rows to {output_path}")
    print(f"Wrote synthetic logs under {root_dir / 'dummy_logs'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
