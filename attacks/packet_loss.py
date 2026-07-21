#!/usr/bin/env python3

from __future__ import annotations

import random
import shlex
from typing import Any, Iterable, Sequence

from meshpay.mininet_cmd import safe_node_cmd

COMMENT = "meshpay-jamming"


def parse_target_count(value: str | int, total_nodes: int) -> int:
    max_targets = max(0, total_nodes // 3)

    value_text = str(value).strip().lower()
    if value_text == "auto":
        return max_targets
    elif value_text == "all":
        return total_nodes

    count = int(value)
    if count < 0:
        raise ValueError("attack target count must be >= 0")

    return min(count, total_nodes)


def select_targets(nodes: Sequence, seed: int, target_count: str | int = "auto") -> list:
    count = parse_target_count(target_count, len(nodes))
    if count <= 0:
        return []

    rng = random.Random(seed)
    candidates = list(nodes)
    rng.shuffle(candidates)
    return candidates[:count]



def _parse_iptables_rule_stats(output: str) -> dict[str, dict[str, int]]:
    stats = {
        "INPUT": {"packets": 0, "bytes": 0, "rules": 0},
        "OUTPUT": {"packets": 0, "bytes": 0, "rules": 0},
    }
    for raw_line in output.splitlines():
        parts = raw_line.strip().split()
        if len(parts) < 3 or parts[0] not in stats:
            continue
        chain = parts[0]
        try:
            packets = int(parts[1])
            byte_count = int(parts[2])
        except ValueError:
            continue
        stats[chain]["packets"] += packets
        stats[chain]["bytes"] += byte_count
        stats[chain]["rules"] += 1
    return stats


def collect_packet_loss_stats(nodes: Iterable) -> dict[str, Any]:
    """Collect iptables counters for active MeshPay packet-loss rules."""
    result: dict[str, Any] = {
        "nodes": {},
        "totals": {
            "input_packets": 0,
            "input_bytes": 0,
            "input_rules": 0,
            "output_packets": 0,
            "output_bytes": 0,
            "output_rules": 0,
            "drop_packets": 0,
            "drop_bytes": 0,
            "rules": 0,
        },
    }
    command = (
        "iptables -w -L INPUT -v -x -n 2>/dev/null | "
        f"awk '/{COMMENT}/ {{print \"INPUT \" $1 \" \" $2}}'; "
        "iptables -w -L OUTPUT -v -x -n 2>/dev/null | "
        f"awk '/{COMMENT}/ {{print \"OUTPUT \" $1 \" \" $2}}'; "
        "true"
    )

    for node in list(nodes):
        raw = safe_node_cmd(node, command)
        parsed = _parse_iptables_rule_stats(raw)
        node_stats = {
            "input_packets": parsed["INPUT"]["packets"],
            "input_bytes": parsed["INPUT"]["bytes"],
            "input_rules": parsed["INPUT"]["rules"],
            "output_packets": parsed["OUTPUT"]["packets"],
            "output_bytes": parsed["OUTPUT"]["bytes"],
            "output_rules": parsed["OUTPUT"]["rules"],
        }
        node_stats["drop_packets"] = node_stats["input_packets"] + node_stats["output_packets"]
        node_stats["drop_bytes"] = node_stats["input_bytes"] + node_stats["output_bytes"]
        node_stats["rules"] = node_stats["input_rules"] + node_stats["output_rules"]
        result["nodes"][node.name] = node_stats
        for key, value in node_stats.items():
            result["totals"][key] += value

    return result

def _validate_probability(probability: float) -> float:
    probability = float(probability)
    if not 0.0 <= probability <= 1.0:
        raise ValueError("packet-loss probability must be between 0.0 and 1.0")
    return probability


def _comment_match() -> str:
    # iptables -S prints: -m comment --comment meshpay-jamming
    return shlex.quote(f"--comment {COMMENT}")


def _cleanup_command() -> str:
    # Idempotently delete every rule carrying our comment from INPUT and OUTPUT.
    # The loop is needed because iptables deletes one matching rule at a time.
    marker = _comment_match()
    return (
        "set +e; "
        "for chain in INPUT OUTPUT; do "
        f"while iptables -w -S \"$chain\" 2>/dev/null | grep -- {marker} >/dev/null 2>&1; do "
        f"rule=$(iptables -w -S \"$chain\" 2>/dev/null | grep -- {marker} | head -n 1 | sed 's/^-A /-D /'); "
        "iptables -w $rule >/dev/null 2>&1 || break; "
        "done; "
        "done; "
        "true"
    )


def cleanup_packet_loss(nodes: Iterable) -> None:
    """Remove MeshPay packet-loss iptables rules from each target node.

    This function is safe to call multiple times and from attack cleanup paths.
    It uses safe_node_cmd() because Mininet node.cmd() is not thread-safe.
    """
    command = _cleanup_command()

    for node in list(nodes):
        safe_node_cmd(node, command)


def apply_packet_loss(nodes: Iterable, probability: float) -> dict[str, Any]:
    """Apply random packet loss to INPUT and OUTPUT traffic on target nodes.

    Loopback is excluded so localhost DTN injection into 127.0.0.1:46666 still
    works even while the wireless-facing traffic is being attacked.  Returns a
    rule-installation summary so benchmark reports can validate the attack.
    """
    probability = _validate_probability(probability)
    probability_text = f"{probability:.6f}"

    target_nodes = list(nodes)
    cleanup_packet_loss(target_nodes)

    commands = [
        (
            "iptables -w -A INPUT ! -i lo "
            "-m statistic --mode random "
            f"--probability {probability_text} "
            f"-m comment --comment {shlex.quote(COMMENT)} "
            "-j DROP"
        ),
        (
            "iptables -w -A OUTPUT ! -o lo "
            "-m statistic --mode random "
            f"--probability {probability_text} "
            f"-m comment --comment {shlex.quote(COMMENT)} "
            "-j DROP"
        ),
    ]

    for node in target_nodes:
        for command in commands:
            safe_node_cmd(node, command)

    stats = collect_packet_loss_stats(target_nodes)
    attempted_per_node = len(commands)
    nodes: dict[str, Any] = {}
    for node in target_nodes:
        node_stats = stats.get("nodes", {}).get(node.name, {})
        installed_rules = int(node_stats.get("rules", 0) or 0)
        nodes[node.name] = {
            "attempted_rules": attempted_per_node,
            "installed_rules": installed_rules,
            "install_success": installed_rules == attempted_per_node,
            "input_rules": int(node_stats.get("input_rules", 0) or 0),
            "output_rules": int(node_stats.get("output_rules", 0) or 0),
        }

    attempted_rules = attempted_per_node * len(target_nodes)
    installed_rules = int(stats.get("totals", {}).get("rules", 0) or 0)
    return {
        "probability": probability,
        "target_count": len(target_nodes),
        "attempted_rules": attempted_rules,
        "installed_rules": installed_rules,
        "install_success": installed_rules == attempted_rules,
        "nodes": nodes,
    }
