from __future__ import annotations

import json

from meshpay.benchmark.payment_metrics import collect_payment_metrics


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_zero_confirmation_bins_and_censored_quorum_curve(tmp_path):
    events = [
        {"event": "attack_started", "time": 20.0, "tpre": 10, "tpost": 10},
        {"event": "attack_stopped", "time": 30.0},
        {"event": "payment_created", "time": 11.0, "order_id": "one"},
        {"event": "payment_created", "time": 21.0, "order_id": "two"},
        {"event": "confirmation_created", "time": 18.0, "order_id": "one", "signers": ["auth1", "auth2", "auth3"]},
        {"event": "authority_signed_transfer", "time": 22.0, "order_id": "two", "authority": "auth1"},
    ]
    reachability = [
        {"time": 21.0, "epoch": 0, "actual_reachable_power": 0.5, "reachable_authority_count": 2},
    ]
    _write_jsonl(tmp_path / "payment.log", events)
    _write_jsonl(tmp_path / "authority_reachability.jsonl", reachability)

    metrics = collect_payment_metrics(tmp_path, started_at=10.0, ended_at=40.0)
    bins = metrics["time_bins_10s"]
    assert bins[1]["confirmed"] == 0
    assert bins[1]["time_to_quorum_ms_p50"] is None
    assert bins[1]["reachable_voting_power"] == 0.5
    assert metrics["kaplan_meier_time_to_quorum"]["censored"] == 1
    assert metrics["recovery"]["attack_window_payments_confirmed_by_run_end_percent"] == 0.0
    assert metrics["authority_phase_activity"]["during"]["auth1"]["signed_votes"] == 1
