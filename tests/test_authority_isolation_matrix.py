from __future__ import annotations

import subprocess
import sys


def test_matrix_dry_run_contains_isolation_parameters():
    command = [
        sys.executable, "scripts/run_meshpay_benchmark_matrix.py",
        "--no-sudo", "--clients", "4", "--authorities", "4", "--ranges", "1000",
        "--routing", "epidemic", "--payment-rate", "1", "--seed", "20,21",
        "--attack", "authority-isolation", "--attack-tpre", "30", "--attack-tatk", "60",
        "--attack-tpost", "60", "--isolation-mode", "cut", "--isolation-reachable-power", "0.75,0.60",
    ]
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    assert "planned_runs=4" in completed.stdout
    assert "--isolation-mode cut" in completed.stdout
    assert "--isolation-reachable-power 0.75" in completed.stdout
    assert "--isolation-reachable-power 0.6" in completed.stdout
    assert "seed20" in completed.stdout and "seed21" in completed.stdout
