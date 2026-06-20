#!/usr/bin/env python3
import json
import sys
from pathlib import Path

def main():
    root_dir = Path(__file__).resolve().parents[1]
    summary_path = root_dir / "logs" / "benchmarks" / "scripts" / "summary.json"
    
    if len(sys.argv) > 1:
        summary_path = Path(sys.argv[1])
        
    if not summary_path.exists():
        print(f"Error: {summary_path} not found.", file=sys.stderr)
        sys.exit(1)
        
    with open(summary_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception as e:
            print(f"Error parsing JSON: {e}", file=sys.stderr)
            sys.exit(1)
            
    # Filter completed runs (exit_code must be 0, and metrics must be populated)
    completed_runs = []
    for row in data:
        if row.get("exit_code") == 0:
            completed_runs.append(row)
        # If dry run but executed is not true, we might still have rows without exit_code
        elif row.get("exit_code") is None:
            # Check if there is actual metric data like payment_acceptance_rate_percent
            if row.get("payment_acceptance_rate_percent") is not None:
                completed_runs.append(row)
                
    if not completed_runs:
        print("No completed runs with valid data found in summary.json.")
        sys.exit(0)
        
    # Group runs by (payment_rate, loss_probability)
    groups = {}
    for run in completed_runs:
        rate = run.get("param.payment_rate")
        loss = run.get("param.attack_loss_probability")
        if rate is None or loss is None:
            continue
        key = (float(rate), float(loss))
        if key not in groups:
            groups[key] = []
        groups[key].append(run)
        
    # Generate Markdown output
    markdown_lines = []
    markdown_lines.append("# MeshPay Routing Performance Comparison")
    markdown_lines.append(f"Generated from: `{summary_path.name}`\n")
    
    # Sort groups: by Rate ascending, then Loss ascending
    sorted_keys = sorted(groups.keys(), key=lambda x: (x[0], x[1]))
    
    for rate, loss in sorted_keys:
        markdown_lines.append(f"## Configuration: Offered Load = {rate} TPS, Packet Loss = {loss * 100:.0f}%")
        markdown_lines.append("")
        
        headers = [
            "Routing Protocol",
            "Acceptance Rate",
            "Confirmation Rate",
            "Avg Acceptance Latency",
            "Avg Quorum Latency",
            "Throughput (Tx+Rx)"
        ]
        
        # Markdown table header
        markdown_lines.append("| " + " | ".join(headers) + " |")
        markdown_lines.append("|" + "|".join(["---"] * len(headers)) + "|")
        
        # Sort runs in group by routing protocol (Epidemic, Spray-and-wait, PRoPHET)
        runs_in_group = groups[(rate, loss)]
        # Define a sorting order: epidemic, spray-and-wait, prophet
        order = {"epidemic": 0, "spray-and-wait": 1, "prophet": 2}
        runs_in_group.sort(key=lambda r: order.get(r.get("param.routing", "").lower(), 99))
        
        for run in runs_in_group:
            routing = run.get("param.routing", "unknown").capitalize()
            if routing.lower() == "spray-and-wait":
                routing = "Spray-and-Wait"
            elif routing.lower() == "prophet":
                routing = "PRoPHET"
                
            acc_rate_val = run.get("payment_acceptance_rate_percent")
            acc_rate = f"{acc_rate_val:.2f}%" if acc_rate_val is not None else "N/A"
            
            conf_rate_val = run.get("payment_confirmation_rate_percent")
            conf_rate = f"{conf_rate_val:.2f}%" if conf_rate_val is not None else "N/A"
            
            avg_acc_val = run.get("avg_time_to_acceptance_ms")
            avg_acc = f"{avg_acc_val / 1000.0:.2f}s" if avg_acc_val is not None else "N/A"
            
            avg_q_val = run.get("avg_time_to_quorum_ms")
            avg_q = f"{avg_q_val / 1000.0:.2f}s" if avg_q_val is not None else "N/A"
            
            # Overhead tx+rx in KB/s
            bytes_sec = run.get("tx_plus_rx_bytes_per_second")
            throughput = f"{bytes_sec / 1024.0:.2f} KB/s" if bytes_sec is not None else "N/A"
            
            row_cols = [
                routing,
                acc_rate,
                conf_rate,
                avg_acc,
                avg_q,
                throughput
            ]
            markdown_lines.append("| " + " | ".join(row_cols) + " |")
            
        markdown_lines.append("")
        
    markdown_output = "\n".join(markdown_lines)
    print(markdown_output)
    
    # Save the output to comparison_table.md alongside summary.json
    output_path = root_dir / "comparison_table.md"
    try:
        output_path.write_text(markdown_output, encoding="utf-8")
        print(f"\nSaved Markdown table to: {output_path}", file=sys.stderr)
    except Exception as e:
        print(f"Error saving file: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
