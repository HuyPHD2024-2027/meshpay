#!/usr/bin/env python3

from __future__ import annotations

import random
import shlex
from typing import Iterable, Sequence


COMMENT = "meshpay-jamming"


def parse_target_count(value: str | int, total_nodes: int) -> int:
    max_targets = total_nodes // 3

    if str(value).strip().lower() == "auto":
        return max_targets

    count = int(value)
    if count < 0:
        raise ValueError("attack target count must be >= 0")

    return min(count, max_targets)


def select_targets(nodes: Sequence, seed: int, target_count: str | int = "auto") -> list:
    count = parse_target_count(target_count, len(nodes))
    if count <= 0:
        return []

    rng = random.Random(seed)
    candidates = list(nodes)
    rng.shuffle(candidates)
    return candidates[:count]


def cleanup_packet_loss(nodes: Iterable) -> None:
    cleanup_command = (
        "for chain in INPUT OUTPUT; do "
        "while iptables -S \"$chain\" | grep -- "
        f"{shlex.quote('--comment ' + COMMENT)} >/dev/null 2>&1; do "
        "rule=$(iptables -S \"$chain\" | grep -- "
        f"{shlex.quote('--comment ' + COMMENT)} | head -n 1 | sed 's/^-A /-D /'); "
        "iptables $rule || break; "
        "done; "
        "done"
    )

    for node in nodes:
        node.cmd(cleanup_command)


def apply_packet_loss(nodes: Iterable, probability: float) -> None:
    probability_text = f"{probability:.6f}"
    commands = [
        (
            "iptables -A INPUT ! -i lo -m statistic --mode random "
            f"--probability {probability_text} -m comment --comment {COMMENT} -j DROP"
        ),
        (
            "iptables -A OUTPUT ! -o lo -m statistic --mode random "
            f"--probability {probability_text} -m comment --comment {COMMENT} -j DROP"
        ),
    ]

    target_nodes = list(nodes)
    cleanup_packet_loss(target_nodes)

    for node in target_nodes:
        for command in commands:
            node.cmd(command)

