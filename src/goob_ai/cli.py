from __future__ import annotations

"""CLI for generating synthetic metrics and figures.

Usage example:
    python -m goob_ai.cli --min 1 --max 200 --peak 50 --best 50 --out ./results/synthetic

This will produce PNG figures and a JSON file with the generated arrays.
"""

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
import argparse
import json

import numpy as np

from .synthetic import MetricParams, generate_metrics
from .plotting import save_throughput_and_latency_plots


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    Returns:
        Parsed arguments as a namespace.
    """
    parser = argparse.ArgumentParser(description="Generate synthetic metrics and figures.")
    parser.add_argument("--min", dest="min_authorities", type=int, default=1,
                        help="Minimum number of authorities (inclusive)")
    parser.add_argument("--max", dest="max_authorities", type=int, default=200,
                        help="Maximum number of authorities (inclusive)")
    parser.add_argument("--step", dest="step", type=int, default=1, help="Step for authorities")
    parser.add_argument("--peak", dest="throughput_peak_at", type=int, default=50,
                        help="Authority count where throughput peaks")
    parser.add_argument("--best", dest="latency_best_at", type=int, default=50,
                        help="Authority count where latency is best (lowest)")
    parser.add_argument("--noise", dest="noise_scale", type=float, default=0.02,
                        help="Noise scale for synthetic curves")
    parser.add_argument("--out", dest="output_dir", type=Path, default=Path("./results/synthetic"),
                        help="Directory to write outputs")
    return parser.parse_args()


def main() -> None:
    """Entrypoint for the CLI script.

    Generates synthetic arrays, saves figures, and emits a JSON summary file.
    """
    args = parse_args()

    params = MetricParams(
        min_authorities=args.min_authorities,
        max_authorities=args.max_authorities,
        step=args.step,
        throughput_peak_at=args.throughput_peak_at,
        latency_best_at=args.latency_best_at,
        noise_scale=args.noise_scale,
    )

    authorities, throughput, latency = generate_metrics(params)

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    throughput_path, latency_path = save_throughput_and_latency_plots(
        authorities, throughput, latency, output_dir=output_dir
    )

    data_path = output_dir / "metrics.json"
    with data_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "params": asdict(params),
                "authorities": authorities.tolist(),
                "throughput": throughput.tolist(),
                "latency": latency.tolist(),
                "figures": {
                    "throughput": str(throughput_path),
                    "latency": str(latency_path),
                },
            },
            f,
            indent=2,
        )


if __name__ == "__main__":
    main()
