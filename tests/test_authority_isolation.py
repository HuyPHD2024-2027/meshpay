from __future__ import annotations

from attacks.authority_isolation import (
    _parse_rule_stats,
    apply_cut,
    restore_positions,
    select_isolated_authorities,
)
import attacks.authority_isolation as isolation_module


def test_weighted_subset_selection_prefers_nearest_not_above_target():
    weights = {f"auth{i}": 250_000 for i in range(1, 5)}
    selected = select_isolated_authorities(list(weights), weights, 0.60, seed=20)
    assert selected["actual_reachable_power"] == 0.50
    assert selected["isolated_authority_count"] == 2
    assert selected["quorum_available"] is False


def test_exact_boundary_is_not_live():
    weights = {"auth1": 1, "auth2": 1, "auth3": 1}
    selected = select_isolated_authorities(list(weights), weights, 2 / 3, seed=4)
    assert selected["reachable_weight_units"] == 2
    assert selected["signed_distance_from_quorum"] == 0
    assert selected["quorum_available"] is False


def test_four_authority_smoke_boundary():
    weights = {f"auth{i}": 250_000 for i in range(1, 5)}
    one_isolated = select_isolated_authorities(list(weights), weights, 0.75, seed=20)
    two_isolated = select_isolated_authorities(list(weights), weights, 0.50, seed=20)
    assert one_isolated["reachable_weight_units"] * 3 > one_isolated["total_weight_units"] * 2
    assert two_isolated["reachable_weight_units"] * 3 <= two_isolated["total_weight_units"] * 2


def test_tie_breaking_is_seeded_and_prefers_fewer_targets():
    weights = {"a": 40, "b": 30, "c": 20, "d": 10}
    first = select_isolated_authorities(list(weights), weights, 0.60, seed=9)
    second = select_isolated_authorities(list(weights), weights, 0.60, seed=9)
    assert first == second
    assert first["isolated_authority_count"] == 1
    assert first["isolated_authorities"] == ["a"]


def test_explicit_targets_override_weighted_selection():
    weights = {"a": 40, "b": 30, "c": 20, "d": 10}
    selected = select_isolated_authorities(
        list(weights), weights, 0.10, seed=1, explicit_targets=["d"],
    )
    assert selected["selection"] == "explicit"
    assert selected["actual_reachable_power"] == 0.90
    assert selected["isolated_authorities"] == ["d"]


def test_epoch_weight_change_changes_actual_power_for_frozen_set():
    first = select_isolated_authorities(["a", "b", "c", "d"], {"a": 25, "b": 25, "c": 25, "d": 25}, 0.75, 3)
    isolated = first["isolated_authorities"]
    changed = {"a": 40, "b": 20, "c": 20, "d": 20}
    actual = (sum(changed.values()) - sum(changed[name] for name in isolated)) / sum(changed.values())
    assert actual in {0.6, 0.8}
    assert isolated == first["isolated_authorities"]


def test_rule_counter_parser():
    parsed = _parse_rule_stats("INPUT 3 120\nOUTPUT 4 200\nnoise\n")
    assert parsed["rules"] == 2
    assert parsed["drop_packets"] == 7
    assert parsed["drop_bytes"] == 320


class _PositionNode:
    def __init__(self, name, position):
        self.name = name
        self.position = list(position)

    def setPosition(self, value):
        self.position = [float(item) for item in value.split(",")]


def test_position_restoration_is_exact_and_idempotent():
    node = _PositionNode("auth1", [999, 999, 0])
    original = {"auth1": [10.25, 12.5, 0.0]}
    first = restore_positions([node], original)
    second = restore_positions([node], original)
    assert first["cleanup_success"] and second["cleanup_success"]
    assert node.position == original["auth1"]


def test_cut_rules_are_tagged_bidirectional_and_exclude_loopback(monkeypatch):
    node = _PositionNode("auth1", [0, 0, 0])
    commands = []

    def fake_cmd(_node, command):
        commands.append(command)
        if "-L INPUT" in command:
            return "INPUT 0 0\nOUTPUT 0 0\n"
        return ""

    monkeypatch.setattr(isolation_module, "safe_node_cmd", fake_cmd)
    result = apply_cut([node])
    installed = [command for command in commands if " -A " in command]
    assert result["install_success"]
    assert any("-A INPUT ! -i lo" in command for command in installed)
    assert any("-A OUTPUT ! -o lo" in command for command in installed)
    assert all("meshpay-authority-isolation" in command for command in installed)
