from __future__ import annotations

"""Tests for synthetic metric generation and plotting.

These tests validate monotonicity around peaks and that files are produced by the CLI.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import json
import subprocess
import sys

import numpy as np
import pytest

from goob_ai.synthetic import MetricParams, generate_authority_counts, generate_metrics

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture
    from _pytest.fixtures import FixtureRequest
    from _pytest.logging import LogCaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from pytest_mock.plugin import MockerFixture


@pytest.mark.parametrize("peak,best", [(50, 50), (30, 45), (80, 60)])
def test_shapes_reasonable(peak: int, best: int) -> None:
    """Throughput has a single dominant peak and latency a clear minimum near "best".

    We check that the global extremum indices are near the configured points.
    """
    params = MetricParams(min_authorities=1, max_authorities=150, step=1,
                          throughput_peak_at=peak, latency_best_at=best, noise_scale=0.0)
    a = generate_authority_counts(params)
    _, t, l = generate_metrics(params)

    t_peak_idx = int(np.argmax(t))
    l_min_idx = int(np.argmin(l))

    assert abs(a[t_peak_idx] - peak) <= 10
    assert abs(a[l_min_idx] - best) <= 10


def test_cli_outputs(tmp_path: Path) -> None:
    """CLI writes figures and a JSON metrics file to the output directory."""
    out_dir = tmp_path / "synthetic"
    cmd = [sys.executable, "-m", "goob_ai.cli", "--min", "1", "--max", "120", "--peak", "50",
           "--best", "50", "--out", str(out_dir)]
    subprocess.check_call(cmd)

    metrics_path = out_dir / "metrics.json"
    assert metrics_path.exists()

    with metrics_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    assert "authorities" in data and "throughput" in data and "latency" in data
    assert (out_dir / "throughput_vs_authorities.png").exists()
    assert (out_dir / "latency_vs_authorities.png").exists()
