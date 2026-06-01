#!/usr/bin/env python3
"""Unified CLI wrapper for MeshPay emulation benchmarks and campaign sweeps."""

from __future__ import annotations

import sys
from pathlib import Path

from mininet.log import setLogLevel

from meshpay.examples.emulation.config import parse_args
from meshpay.examples.emulation.campaign import run_campaign, generate_plots
from meshpay.examples.emulation.runner import (
    format_comparison_report,
    run_comparison,
    run_single,
    write_json_output,
)


def main() -> None:
    """Run the benchmark CLI."""

    setLogLevel("info")
    config = parse_args()

    if config.campaign:
        outputs = run_campaign(config)
        summary = f"{config.results_dir}/summary.csv"
        print(f"Campaign completed: {len(outputs)} runs")
        print(f"Summary: {summary}")
        return

    if config.routing in ("both", "all"):
        result = run_comparison(config)
        print(format_comparison_report(result))
        
        plot_output = config.plot_output or "results/comparison_plot.png"
        print(f"\n🎨 Generating evaluation plots to {plot_output}...")
        generate_plots(
            result.epidemic_stats,
            result.sdn_stats,
            plot_output,
            all_stats=result.all_stats,
        )
        if config.output_file:
            write_json_output(config.output_file, result.to_dict())
            print(f"💾 Saved comparison telemetry stats to: {config.output_file}")
        print("\n✅ Benchmark study completed successfully!")
        return

    stats = run_single(config)
    if config.output_file:
        write_json_output(config.output_file, stats.to_dict())
        print(f"💾 Saved {config.routing.upper()} telemetry stats to: {config.output_file}")


if __name__ == "__main__":
    main()
