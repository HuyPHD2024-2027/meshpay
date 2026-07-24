from __future__ import annotations

import json
import os
import subprocess
import sys


def test_plotting_accepts_zero_confirmation_bins_and_failed_runs(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    benchmark = {
        "timing": {"started_at": 10.0},
        "attack": {"targets": ["auth1", "auth2"], "isolation_mode": "cut", "actual_reachable_power": 0.5},
        "payment_metrics": {
            "time_bins_10s": [
                {"start": 10.0, "end": 20.0, "confirmed_tps": 1.0, "time_to_quorum_ms_p50": 1000, "time_to_quorum_ms_p95": 2000},
                {"start": 20.0, "end": 30.0, "confirmed_tps": 0.0, "time_to_quorum_ms_p50": None, "time_to_quorum_ms_p95": None},
            ],
            "post_attack_funnel": {"cohorts_by_created_phase": {"during": {"totals": {"payment_created": 10, "confirmation_created": 0}}}},
        },
    }
    (run_dir / "benchmark.json").write_text(json.dumps(benchmark), encoding="utf-8")
    (run_dir / "payment.log").write_text(
        json.dumps({"event": "attack_started", "time": 20.0}) + "\n" +
        json.dumps({"event": "attack_stopped", "time": 30.0}) + "\n",
        encoding="utf-8",
    )
    summary = [
        {"run_id": "valid", "run_dir": str(run_dir), "exit_code": 0, "param.routing": "epidemic",
         "param.authorities": 4, "param.attack": "authority-isolation", "param.seed": 20,
         "actual_reachable_power": 0.5, "isolation_mode": "cut",
         "payment_confirmation_rate_percent": 0.0, "payment_acceptance_rate_percent": 0.0,
         "time_to_recover_90_percent_three_bins_s": None, "backlog_at_restoration": 10,
         "attack_window_eventual_confirmation_percent": 0.0, "reachable_authority_count": 2},
        {"run_id": "failed", "run_dir": str(tmp_path / "missing"), "exit_code": 1,
         "param.routing": "prophet", "actual_reachable_power": None},
    ]
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    output = tmp_path / "figures"
    env = os.environ.copy()
    env["MPLCONFIGDIR"] = str(tmp_path / "mpl")
    subprocess.run(
        [sys.executable, "scripts/plot_authority_isolation.py", str(summary_path), "-o", str(output)],
        check=True, env=env, capture_output=True, text=True,
    )
    assert (output / "primary_threshold.pdf").exists()
    assert (output / "progress_timeline.csv").exists()
    assert (output / "phase_funnel_table.csv").exists()
