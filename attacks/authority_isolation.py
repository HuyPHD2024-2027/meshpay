#!/usr/bin/env python3

"""Network-only isolation mechanisms for weighted MeshPay authorities."""

from __future__ import annotations

import itertools
import json
import random
import shlex
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from meshpay.mininet_cmd import safe_node_cmd


COMMENT = "meshpay-authority-isolation"
STRICT_QUORUM_FRACTION = 2.0 / 3.0


def _names(nodes: Iterable) -> list[str]:
    return [str(getattr(node, "name", node)) for node in nodes]


def select_isolated_authorities(
    authority_names: Sequence[str],
    weights: Mapping[str, int],
    requested_reachable_power: float,
    seed: int,
    explicit_targets: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Choose the frozen isolated set using the live weighted snapshot.

    Values at or below the requested power are preferred. If no such subset
    exists, the closest value above it is used. Ties prefer fewer isolated
    authorities and then the deterministic seeded authority order.
    """

    target = float(requested_reachable_power)
    if not 0.0 <= target <= 1.0:
        raise ValueError("isolation reachable power must be between 0.0 and 1.0")

    authorities = list(dict.fromkeys(str(name) for name in authority_names))
    if not authorities:
        raise ValueError("authority isolation requires at least one authority")
    missing_weights = [name for name in authorities if name not in weights]
    if missing_weights:
        raise ValueError(f"missing authority weights: {', '.join(missing_weights)}")

    total = sum(int(weights[name]) for name in authorities)
    if total <= 0:
        raise ValueError("authority weight total must be positive")

    seeded_order = list(authorities)
    random.Random(int(seed)).shuffle(seeded_order)
    rank = {name: index for index, name in enumerate(seeded_order)}

    if explicit_targets is not None:
        isolated = list(dict.fromkeys(str(name) for name in explicit_targets))
        unknown = sorted(set(isolated) - set(authorities))
        if unknown:
            raise ValueError(f"unknown explicit authority targets: {', '.join(unknown)}")
        candidates = [tuple(isolated)]
        selection = "explicit"
    else:
        candidates = [
            subset
            for size in range(len(authorities) + 1)
            for subset in itertools.combinations(authorities, size)
        ]
        selection = "weighted_subset"

    rows: list[tuple[tuple[str, ...], int, float]] = []
    for subset in candidates:
        isolated_weight = sum(int(weights[name]) for name in subset)
        reachable_units = total - isolated_weight
        rows.append((subset, reachable_units, reachable_units / total))

    below = [row for row in rows if row[2] <= target + 1e-15]
    pool = below if below else [row for row in rows if row[2] > target]

    def tie_key(row: tuple[tuple[str, ...], int, float]) -> tuple:
        subset, _units, actual = row
        distance = (target - actual) if below else (actual - target)
        subset_rank = tuple(sorted(rank[name] for name in subset))
        return (round(distance, 15), len(subset), subset_rank)

    subset, reachable_units, actual = min(pool, key=tie_key)
    isolated_set = set(subset)
    return {
        "selection": selection,
        "seeded_authority_order": seeded_order,
        "requested_reachable_power": target,
        "actual_reachable_power": actual,
        "reachable_weight_units": reachable_units,
        "total_weight_units": total,
        "isolated_authorities": [name for name in seeded_order if name in isolated_set],
        "reachable_authorities": [name for name in seeded_order if name not in isolated_set],
        "isolated_authority_count": len(isolated_set),
        "reachable_authority_count": len(authorities) - len(isolated_set),
        "strict_quorum_threshold": STRICT_QUORUM_FRACTION,
        "signed_distance_from_quorum": actual - STRICT_QUORUM_FRACTION,
        "quorum_available": reachable_units * 3 > total * 2,
    }


def _cleanup_command() -> str:
    marker = shlex.quote(f"--comment {COMMENT}")
    return (
        "set +e; for chain in INPUT OUTPUT; do "
        f"while iptables -w -S \"$chain\" 2>/dev/null | grep -- {marker} >/dev/null 2>&1; do "
        f"rule=$(iptables -w -S \"$chain\" 2>/dev/null | grep -- {marker} | head -n 1 | sed 's/^-A /-D /'); "
        "iptables -w $rule >/dev/null 2>&1 || break; done; done; true"
    )


def _stats_command() -> str:
    return (
        "iptables -w -L INPUT -v -x -n 2>/dev/null | "
        f"awk '/{COMMENT}/ {{print \"INPUT \" $1 \" \" $2}}'; "
        "iptables -w -L OUTPUT -v -x -n 2>/dev/null | "
        f"awk '/{COMMENT}/ {{print \"OUTPUT \" $1 \" \" $2}}'; true"
    )


def _parse_rule_stats(output: str) -> dict[str, int]:
    result = {"input_packets": 0, "input_bytes": 0, "input_rules": 0,
              "output_packets": 0, "output_bytes": 0, "output_rules": 0}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[0] not in {"INPUT", "OUTPUT"}:
            continue
        try:
            packets, byte_count = int(parts[1]), int(parts[2])
        except ValueError:
            continue
        prefix = parts[0].lower()
        result[f"{prefix}_packets"] += packets
        result[f"{prefix}_bytes"] += byte_count
        result[f"{prefix}_rules"] += 1
    result["drop_packets"] = result["input_packets"] + result["output_packets"]
    result["drop_bytes"] = result["input_bytes"] + result["output_bytes"]
    result["rules"] = result["input_rules"] + result["output_rules"]
    return result


def collect_cut_stats(nodes: Iterable) -> dict[str, Any]:
    per_node = {}
    totals = {key: 0 for key in _parse_rule_stats("")}
    for node in list(nodes):
        stats = _parse_rule_stats(safe_node_cmd(node, _stats_command()))
        per_node[node.name] = stats
        for key, value in stats.items():
            totals[key] += value
    return {"nodes": per_node, "totals": totals}


def cleanup_cut(nodes: Iterable) -> None:
    for node in list(nodes):
        safe_node_cmd(node, _cleanup_command())


def apply_cut(nodes: Iterable) -> dict[str, Any]:
    targets = list(nodes)
    cleanup_cut(targets)
    commands = (
        f"iptables -w -A INPUT ! -i lo -m comment --comment {shlex.quote(COMMENT)} -j DROP",
        f"iptables -w -A OUTPUT ! -o lo -m comment --comment {shlex.quote(COMMENT)} -j DROP",
    )
    for node in targets:
        for command in commands:
            safe_node_cmd(node, command)
    stats = collect_cut_stats(targets)
    installed = int(stats["totals"]["rules"])
    attempted = 2 * len(targets)
    return {
        "attempted_rules": attempted,
        "installed_rules": installed,
        "install_success": installed == attempted,
        "nodes": stats["nodes"],
    }


def save_positions(nodes: Iterable) -> dict[str, list[float]]:
    return {
        node.name: [float(value) for value in getattr(node, "position", (0.0, 0.0, 0.0))]
        for node in list(nodes)
    }


def apply_range_isolation(targets: Iterable, non_targets: Iterable) -> dict[str, Any]:
    target_nodes = list(targets)
    other_nodes = list(non_targets)
    all_nodes = target_nodes + other_nodes
    original = save_positions(target_nodes)
    max_x = max((float(node.position[0]) for node in all_nodes), default=0.0)
    max_y = max((float(node.position[1]) for node in all_nodes), default=0.0)
    def node_range(node) -> float:
        value = getattr(node, "range", None)
        if not isinstance(value, (int, float)):
            value = getattr(node, "params", {}).get("range", 0.0)
        return float(value)

    max_range = max((node_range(node) for node in all_nodes), default=0.0)
    max_range = max(max_range, 1.0)
    displaced = {}
    for index, node in enumerate(target_nodes, start=1):
        position = [max_x + 4 * max_range + index * max_range, max_y + 4 * max_range, 0.0]
        node.setPosition(",".join(str(value) for value in position))
        displaced[node.name] = position
    return {"original_positions": original, "isolated_positions": displaced, "install_success": True}


def restore_positions(nodes: Iterable, original_positions: Mapping[str, Sequence[float]]) -> dict[str, Any]:
    restored = {}
    errors = {}
    for node in list(nodes):
        position = original_positions.get(node.name)
        if position is None:
            continue
        try:
            node.setPosition(",".join(str(float(value)) for value in position))
            restored[node.name] = [float(value) for value in position]
        except Exception as exc:  # cleanup must continue across nodes
            errors[node.name] = f"{type(exc).__name__}: {exc}"
    return {"restored_positions": restored, "errors": errors, "cleanup_success": not errors}


def probe_connectivity(source_nodes: Sequence, target_nodes: Sequence) -> dict[str, Any]:
    """Run bidirectional, one-packet probes between reachable and target nodes."""
    sources = list(source_nodes)
    targets = list(target_nodes)
    results = []
    for source, target in itertools.product(sources[:1], targets):
        for left, right in ((source, target), (target, source)):
            ip_method = getattr(right, "IP", None)
            ip = str(ip_method() if callable(ip_method) else right.params.get("ip", "")).split("/", 1)[0]
            output = safe_node_cmd(left, f"ping -c 1 -W 1 {shlex.quote(ip)} >/dev/null 2>&1; echo $?")
            status_text = output.strip().splitlines()[-1] if output.strip() else "1"
            results.append({"source": left.name, "target": right.name, "success": status_text == "0"})
    return {
        "time": time.time(),
        "probes": results,
        "attempted": len(results),
        "succeeded": sum(1 for row in results if row["success"]),
    }


def append_reachability_sample(path: str | Path, sample: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(sample), sort_keys=True) + "\n")
