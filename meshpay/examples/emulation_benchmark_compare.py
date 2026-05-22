#!/usr/bin/env python3
"""Backward-compatible CLI wrapper for MeshPay emulation benchmarks."""

from __future__ import annotations

from mininet.log import setLogLevel

from meshpay.examples.emulation.arguments import parse_args
from meshpay.examples.emulation.figures import generate_plots
from meshpay.examples.emulation.campaign import run_campaign
from meshpay.examples.emulation.research_figures import generate_research_figures
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
        generate_research_figures(summary, f"{config.results_dir}/figures", config.figure_format.split(","))
        print(f"Campaign completed: {len(outputs)} runs")
        print(f"Summary: {summary}")
        return

    if config.routing == "both":
        result = run_comparison(config)
        print(format_comparison_report(result))
        print("\n🎨 Generating evaluation plots...")
        generate_plots(result.epidemic_stats, result.sdn_stats, config.plot_output)
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
