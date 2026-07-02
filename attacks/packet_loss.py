#!/usr/bin/env python3

from __future__ import annotations

import random
import shlex
from typing import Iterable, Sequence

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
        f"while iptables -S \"$chain\" 2>/dev/null | grep -- {marker} >/dev/null 2>&1; do "
        f"rule=$(iptables -S \"$chain\" 2>/dev/null | grep -- {marker} | head -n 1 | sed 's/^-A /-D /'); "
        "iptables $rule >/dev/null 2>&1 || break; "
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


def apply_packet_loss(nodes: Iterable, probability: float) -> None:
    """Apply random packet loss to INPUT and OUTPUT traffic on target nodes.

    Loopback is excluded so localhost DTN injection into 127.0.0.1:46666 still
    works even while the wireless-facing traffic is being attacked.
    """
    probability = _validate_probability(probability)
    probability_text = f"{probability:.6f}"

    target_nodes = list(nodes)
    cleanup_packet_loss(target_nodes)

    commands = [
        (
            "iptables -A INPUT ! -i lo "
            "-m statistic --mode random "
            f"--probability {probability_text} "
            f"-m comment --comment {shlex.quote(COMMENT)} "
            "-j DROP"
        ),
        (
            "iptables -A OUTPUT ! -o lo "
            "-m statistic --mode random "
            f"--probability {probability_text} "
            f"-m comment --comment {shlex.quote(COMMENT)} "
            "-j DROP"
        ),
    ]

    for node in target_nodes:
        for command in commands:
            safe_node_cmd(node, command)