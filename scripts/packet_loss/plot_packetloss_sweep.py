#!/usr/bin/env python3
"""Aggregate and plot packet-loss benchmark results across multiple seeds."""

import sys
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

def main() -> int:
    EXP_ROOT = Path("logs/benchmarks/packetloss_3routing_seeds20_24")
    FIG_DIR = EXP_ROOT / "figures"
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    routing_label = {
        "epidemic": "Epidemic",
        "spray-and-wait": "Spray-and-Wait",
        "prophet": "PRoPHET",
    }

    routing_marker = {
        "epidemic": "o",
        "spray-and-wait": "s",
        "prophet": "^",
    }

    rows = []

    # Find and load all summary.csv files under the target directories
    summaries = list(EXP_ROOT.glob("*_loss_seed_*/summary.csv"))
    if not summaries:
        print(f"Error: No summary.csv files found under {EXP_ROOT}", file=sys.stderr)
        print("Please run scripts/run_packetloss_sweep.sh first.", file=sys.stderr)
        return 1

    print(f"Found {len(summaries)} summary.csv files. Loading data...")
    for summary in summaries:
        try:
            df = pd.read_csv(summary)
            for _, row in df.iterrows():
                rows.append(row.to_dict())
        except Exception as e:
            print(f"Warning: Failed to load {summary}: {e}", file=sys.stderr)

    data = pd.DataFrame(rows)
    if data.empty:
        print("Error: Loaded dataset is empty.", file=sys.stderr)
        return 1

    # Normalize column names expected from your matrix runner
    data["routing"] = data["param.routing"]
    data["loss"] = data["param.attack_loss_probability"]

    # Convert ms to seconds for latency figure
    data["avg_time_to_quorum_s"] = data["avg_time_to_quorum_ms"] / 1000.0

    # Aggregate across seeds
    group_cols = ["routing", "loss"]
    agg = data.groupby(group_cols).agg(
        confirmation_rate_mean=("payment_confirmation_rate_percent", "mean"),
        confirmation_rate_std=("payment_confirmation_rate_percent", "std"),

        quorum_latency_mean=("avg_time_to_quorum_s", "mean"),
        quorum_latency_std=("avg_time_to_quorum_s", "std"),

        network_tx_kbs_mean=("network_tx_bytes_per_second", lambda x: x.mean() / 1000.0),
        network_tx_kbs_std=("network_tx_bytes_per_second", lambda x: x.std() / 1000.0),

        network_rx_kbs_mean=("network_rx_bytes_per_second", lambda x: x.mean() / 1000.0),
        network_rx_kbs_std=("network_rx_bytes_per_second", lambda x: x.std() / 1000.0),
    ).reset_index()

    # Fill NaN standard deviations (e.g. if single seed run or constant values) with 0
    agg = agg.fillna(0.0)

    # ------------------------------
    # Figure 1: Confirmation rate
    # ------------------------------
    plt.figure(figsize=(7, 5))
    for routing in ["epidemic", "spray-and-wait", "prophet"]:
        sub = agg[agg["routing"] == routing].sort_values("loss")
        if sub.empty:
            continue
        plt.errorbar(
            sub["loss"],
            sub["confirmation_rate_mean"],
            yerr=sub["confirmation_rate_std"],
            marker=routing_marker[routing],
            capsize=3,
            label=routing_label[routing],
        )

    plt.xlabel("Packet-Loss Probability")
    plt.ylabel("Confirmation Rate (%)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIG_DIR / "confirmation_rate_vs_packet_loss.png", dpi=300)
    plt.savefig(FIG_DIR / "confirmation_rate_vs_packet_loss.pdf")
    plt.close()

    # ------------------------------
    # Figure 2: Average quorum latency
    # ------------------------------
    plt.figure(figsize=(7, 5))
    for routing in ["epidemic", "spray-and-wait", "prophet"]:
        sub = agg[agg["routing"] == routing].sort_values("loss")
        if sub.empty:
            continue
        plt.errorbar(
            sub["loss"],
            sub["quorum_latency_mean"],
            yerr=sub["quorum_latency_std"],
            marker=routing_marker[routing],
            capsize=3,
            label=routing_label[routing],
        )

    plt.xlabel("Packet-Loss Probability")
    plt.ylabel("Avg. Time-to-Quorum (s)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIG_DIR / "avg_quorum_latency_vs_packet_loss.png", dpi=300)
    plt.savefig(FIG_DIR / "avg_quorum_latency_vs_packet_loss.pdf")
    plt.close()

    # ------------------------------
    # Figure 3: Network throughput TX/RX
    # ------------------------------
    plt.figure(figsize=(8, 5))
    for routing in ["epidemic", "spray-and-wait", "prophet"]:
        sub = agg[agg["routing"] == routing].sort_values("loss")
        if sub.empty:
            continue
        plt.plot(
            sub["loss"],
            sub["network_tx_kbs_mean"],
            marker=routing_marker[routing],
            linestyle="-",
            label=f"{routing_label[routing]} TX",
        )
        plt.plot(
            sub["loss"],
            sub["network_rx_kbs_mean"],
            marker=routing_marker[routing],
            linestyle="--",
            label=f"{routing_label[routing]} RX",
        )

    plt.xlabel("Packet-Loss Probability")
    plt.ylabel("Network Throughput (KB/s)")
    plt.grid(True, alpha=0.3)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(FIG_DIR / "network_throughput_vs_packet_loss.png", dpi=300)
    plt.savefig(FIG_DIR / "network_throughput_vs_packet_loss.pdf")
    plt.close()

    print(f"\nSaved figures to: {FIG_DIR}")
    print("\nAggregated Results:")
    print(agg.to_string(index=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
