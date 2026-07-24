#!/usr/bin/env python3

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Sequence

from attacks.targeted_load import SyntheticLoadInjector
from attacks.packet_loss import (
    apply_packet_loss,
    cleanup_packet_loss,
    collect_packet_loss_stats,
    select_targets,
)
from attacks.authority_isolation import (
    STRICT_QUORUM_FRACTION,
    append_reachability_sample,
    apply_cut,
    apply_range_isolation,
    cleanup_cut,
    collect_cut_stats,
    probe_connectivity,
    restore_positions,
    select_isolated_authorities,
)


class BenchmarkAttack:
    """Timed attack controller for MeshPay benchmark runs."""

    def __init__(
        self,
        runtime,
        all_nodes: Sequence,
        client_nodes: Sequence,
        log_dir: str | Path,
        attack_type: str,
        loss_probability: float,
        tpre: float,
        tatk: float,
        tpost: float,
        target_count: str | int,
        load_rate: float,
        seed: int,
        authority_nodes: Sequence = (),
        isolation_mode: str = "cut",
        isolation_reachable_power: float = 1.0,
        isolation_loss_probability: float = 0.75,
        isolation_targets: Sequence[str] | None = None,
        weight_registry=None,
        mobility_disabled: bool = False,
    ) -> None:
        self.runtime = runtime
        self.all_nodes = list(all_nodes)
        self.client_nodes = list(client_nodes)
        self.log_dir = Path(log_dir)
        self.attack_type = attack_type
        self.loss_probability = float(loss_probability)
        self.tpre = float(tpre)
        self.tatk = float(tatk)
        self.tpost = float(tpost)
        self.target_count = target_count
        self.load_rate = float(load_rate)
        self.seed = int(seed)
        self.authority_nodes = list(authority_nodes)
        self.isolation_mode = str(isolation_mode)
        self.isolation_reachable_power = float(isolation_reachable_power)
        self.isolation_loss_probability = float(isolation_loss_probability)
        self.isolation_targets = list(isolation_targets) if isolation_targets is not None else None
        self.weight_registry = weight_registry
        self.mobility_disabled = bool(mobility_disabled)

        if self.isolation_mode not in {"cut", "loss", "range"}:
            raise ValueError("isolation mode must be one of: cut, loss, range")
        if self.isolation_mode == "range" and not self.mobility_disabled:
            raise ValueError("range isolation requires mobility to be disabled")
        if not 0.0 <= self.isolation_reachable_power <= 1.0:
            raise ValueError("isolation reachable power must be between 0.0 and 1.0")
        if not 0.0 <= self.isolation_loss_probability <= 1.0:
            raise ValueError("isolation loss probability must be between 0.0 and 1.0")

        self.targets = [] if self.attack_type == "authority-isolation" else select_targets(
            nodes=self.all_nodes, seed=self.seed, target_count=self.target_count,
        )
        self.target_names = [node.name for node in self.targets]

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load_injector: SyntheticLoadInjector | None = None
        self._packet_loss_active = False
        self._packet_loss_installation: dict | None = None
        self._packet_loss_drop_counters: dict | None = None
        self._packet_loss_cleanup: dict | None = None
        self._isolation_selection: dict | None = None
        self._isolation_installation: dict | None = None
        self._isolation_counters: dict | None = None
        self._isolation_cleanup: dict | None = None
        self._isolation_positions: dict | None = None
        self._connectivity_probes: dict[str, dict] = {}
        self._last_reachability_sample: dict | None = None
        self._last_isolation_reachability_sample: dict | None = None
        self._cleanup_lock = threading.RLock()

    def metadata(self) -> dict:
        return {
            "attack": self.attack_type,
            "loss_probability": self.loss_probability,
            "tpre": self.tpre,
            "tatk": self.tatk,
            "tpost": self.tpost,
            "target_count": self.target_count,
            "target_selection": (
                (self._isolation_selection or {}).get("selection", "pending_weighted_subset")
                if self.attack_type == "authority-isolation"
                else "all_nodes"
                if len(self.targets) == len(self.all_nodes)
                else "random_subset"
            ),
            "selected_target_count": len(self.targets),
            "targets": list(self.target_names),
            "load_rate": self.load_rate,
            "seed": self.seed,
            "attack_mode": (
                f"authority_isolation_{self.isolation_mode}"
                if self.attack_type == "authority-isolation"
                else
                "endpoint_iptables_drop"
                if self.attack_type in {"packetloss", "packetloss-load"}
                else self.attack_type
            ),
            "target_fraction": (
                len(self.targets) / len(self.all_nodes)
                if self.all_nodes
                else 0.0
            ),
            "packet_loss_installation": self._packet_loss_installation,
            "packet_loss_drop_counters": self._packet_loss_drop_counters,
            "packet_loss_cleanup": self._packet_loss_cleanup,
            "packet_loss_rules_remaining_after_cleanup": (
                self._packet_loss_cleanup or {}
            ).get("remaining_rules"),
            "isolation_mode": self.isolation_mode if self.attack_type == "authority-isolation" else None,
            "isolation_loss_probability": (
                self.isolation_loss_probability if self.attack_type == "authority-isolation" else None
            ),
            "requested_reachable_power": (
                self.isolation_reachable_power if self.attack_type == "authority-isolation" else None
            ),
            "actual_reachable_power": (self._isolation_selection or {}).get("actual_reachable_power"),
            "final_attack_reachable_power": (self._last_isolation_reachability_sample or {}).get("actual_reachable_power"),
            "strict_quorum_threshold": (
                STRICT_QUORUM_FRACTION if self.attack_type == "authority-isolation" else None
            ),
            "signed_distance_from_quorum": (self._isolation_selection or {}).get("signed_distance_from_quorum"),
            "pre_attack_weight_epoch": (self._isolation_selection or {}).get("pre_attack_weight_epoch"),
            "pre_attack_weight_snapshot": (self._isolation_selection or {}).get("pre_attack_weight_snapshot"),
            "isolated_authorities": (self._isolation_selection or {}).get("isolated_authorities", []),
            "reachable_authorities": (self._isolation_selection or {}).get("reachable_authorities", []),
            "isolated_authority_count": (self._isolation_selection or {}).get("isolated_authority_count", 0),
            "reachable_authority_count": (self._isolation_selection or {}).get("reachable_authority_count"),
            "isolation_installation": self._isolation_installation,
            "isolation_rule_counters": self._isolation_counters,
            "isolation_positions": self._isolation_positions,
            "isolation_cleanup": self._isolation_cleanup,
            "connectivity_probes": self._connectivity_probes,
            "attack_validation_success": self._isolation_validation_success(),
        }

    def _isolation_validation_success(self) -> bool | None:
        if self.attack_type != "authority-isolation":
            return None
        if self._isolation_cleanup is None:
            return None
        installed = bool((self._isolation_installation or {}).get("install_success", not self.targets))
        cleaned = bool(self._isolation_cleanup.get("cleanup_success"))
        return installed and cleaned and self._isolation_selection is not None

    def write_metadata(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.log_dir / "attack_metadata.json").open("w", encoding="utf-8") as f:
            json.dump(self.metadata(), f, indent=2, sort_keys=True)
            f.write("\n")

    def _cleanup_packet_loss_rules(self) -> None:
        self._packet_loss_drop_counters = collect_packet_loss_stats(self.targets)
        rules_before = int(
            (self._packet_loss_drop_counters.get("totals", {}) if self._packet_loss_drop_counters else {})
            .get("rules", 0)
            or 0
        )
        cleanup_packet_loss(self.targets)
        post_cleanup = collect_packet_loss_stats(self.targets)
        remaining_rules = int(post_cleanup.get("totals", {}).get("rules", 0) or 0)
        self._packet_loss_cleanup = {
            "rules_before_cleanup": rules_before,
            "remaining_rules": remaining_rules,
            "removed_rules": max(rules_before - remaining_rules, 0),
            "cleanup_success": remaining_rules == 0,
            "post_cleanup_counters": post_cleanup,
        }
        self._packet_loss_active = False

    def start(self, started_at: float) -> None:
        if self.attack_type == "none" or self._thread is not None:
            return

        self.write_metadata()
        self.runtime.record_event(
            {
                "event": "attack_configured",
                **self.metadata(),
                "traffic_started_at": started_at,
            }
        )

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cleanup(self) -> None:
        with self._cleanup_lock:
            self._stop.set()

            if self._load_injector is not None:
                self._load_injector.stop()
                self._load_injector = None

            if self._packet_loss_active:
                self._cleanup_packet_loss_rules()

            if self.attack_type == "authority-isolation" and self._isolation_selection is not None and self._isolation_cleanup is None:
                self._cleanup_isolation()

        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        if self.attack_type != "none":
            self.runtime.record_event(
                {
                    "event": "attack_cleanup",
                    **self.metadata(),
                }
            )
            self.write_metadata()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _current_reachability(self, isolation_active: bool = True, phase: str | None = None) -> dict:
        if self.weight_registry is None:
            raise ValueError("authority isolation requires a weight registry")
        snapshot = self.weight_registry.current_snapshot()
        isolated = (
            set((self._isolation_selection or {}).get("isolated_authorities", []))
            if isolation_active else set()
        )
        reachable = [name for name in snapshot.committee if name not in isolated]
        units = sum(snapshot.weight_for(name) for name in reachable)
        actual = units / snapshot.total_weight_units
        return {
            "time": time.time(),
            "epoch": snapshot.epoch,
            "weights": snapshot.weights,
            "total_weight_units": snapshot.total_weight_units,
            "reachable_weight_units": units,
            "actual_reachable_power": actual,
            "strict_quorum_threshold": STRICT_QUORUM_FRACTION,
            "signed_distance_from_quorum": actual - STRICT_QUORUM_FRACTION,
            "quorum_available": units * 3 > snapshot.total_weight_units * 2,
            "isolated_authorities": sorted(isolated),
            "reachable_authorities": reachable,
            "isolated_authority_count": len(isolated),
            "reachable_authority_count": len(reachable),
            "phase": phase,
            "isolation_active": isolation_active,
        }

    def _record_reachability(self, isolation_active: bool = True, phase: str | None = None) -> None:
        sample = self._current_reachability(isolation_active=isolation_active, phase=phase)
        self._last_reachability_sample = sample
        if isolation_active:
            self._last_isolation_reachability_sample = sample
        append_reachability_sample(self.log_dir / "authority_reachability.jsonl", sample)

    def _prepare_isolation_targets(self) -> None:
        snapshot = self.weight_registry.current_snapshot()
        node_by_name = {node.name: node for node in self.all_nodes}
        explicit = self.isolation_targets
        if explicit is not None:
            unknown = sorted(set(explicit) - set(node_by_name))
            if unknown:
                raise ValueError(f"unknown isolation targets: {', '.join(unknown)}")
            explicit_authorities = [name for name in explicit if name in snapshot.committee]
            selection = select_isolated_authorities(
                snapshot.committee, snapshot.weights, self.isolation_reachable_power,
                self.seed, explicit_targets=explicit_authorities,
            )
            self.target_names = list(dict.fromkeys(explicit))
        else:
            selection = select_isolated_authorities(
                snapshot.committee, snapshot.weights, self.isolation_reachable_power, self.seed,
            )
            self.target_names = list(selection["isolated_authorities"])
        selection["pre_attack_weight_epoch"] = snapshot.epoch
        selection["pre_attack_weight_snapshot"] = {
            "epoch": snapshot.epoch,
            "committee": list(snapshot.committee),
            "committee_digest": snapshot.committee_digest,
            "weights": snapshot.weights,
            "total_weight_units": snapshot.total_weight_units,
        }
        self._isolation_selection = selection
        self.targets = [node_by_name[name] for name in self.target_names]

    def _install_isolation(self) -> None:
        reachable_nodes = [node for node in self.all_nodes if node.name not in set(self.target_names)]
        self._connectivity_probes["pre"] = probe_connectivity(reachable_nodes, self.targets)
        if self.isolation_mode == "cut":
            self._isolation_installation = apply_cut(self.targets)
        elif self.isolation_mode == "loss":
            self._isolation_installation = apply_packet_loss(self.targets, self.isolation_loss_probability)
        else:
            self._isolation_positions = apply_range_isolation(self.targets, reachable_nodes)
            self._isolation_installation = dict(self._isolation_positions)

    def _cleanup_isolation(self) -> None:
        reachable_nodes = [node for node in self.all_nodes if node.name not in set(self.target_names)]
        if self.isolation_mode == "cut":
            self._isolation_counters = collect_cut_stats(self.targets)
            before = int(self._isolation_counters["totals"]["rules"])
            cleanup_cut(self.targets)
            after_stats = collect_cut_stats(self.targets)
            remaining = int(after_stats["totals"]["rules"])
            self._isolation_cleanup = {
                "rules_before_cleanup": before,
                "removed_rules": max(before - remaining, 0),
                "remaining_rules": remaining,
                "cleanup_success": remaining == 0,
                "post_cleanup_counters": after_stats,
            }
        elif self.isolation_mode == "loss":
            self._packet_loss_drop_counters = collect_packet_loss_stats(self.targets)
            before = int(self._packet_loss_drop_counters["totals"]["rules"])
            cleanup_packet_loss(self.targets)
            post = collect_packet_loss_stats(self.targets)
            remaining = int(post["totals"]["rules"])
            self._isolation_counters = self._packet_loss_drop_counters
            self._isolation_cleanup = {
                "rules_before_cleanup": before, "removed_rules": max(before - remaining, 0),
                "remaining_rules": remaining, "cleanup_success": remaining == 0,
                "post_cleanup_counters": post,
            }
        else:
            original = (self._isolation_positions or {}).get("original_positions", {})
            self._isolation_cleanup = restore_positions(self.targets, original)
        self._connectivity_probes["post"] = probe_connectivity(reachable_nodes, self.targets)

    def _run(self) -> None:
        if self.attack_type == "authority-isolation":
            pre_deadline = time.time() + max(self.tpre, 0.0)
            while not self._stop.is_set() and time.time() < pre_deadline:
                self._record_reachability(isolation_active=False, phase="before")
                self._stop.wait(min(1.0, max(pre_deadline - time.time(), 0.0)))
            if self._stop.is_set():
                return
        elif not self._sleep(self.tpre):
            return

        if self.attack_type == "authority-isolation":
            try:
                self._prepare_isolation_targets()
                self._install_isolation()
                self._record_reachability(isolation_active=True, phase="during")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                try:
                    self._cleanup_isolation()
                    self._isolation_cleanup["attack_error"] = error
                except Exception as cleanup_exc:
                    self._isolation_cleanup = {
                        "cleanup_success": False,
                        "attack_error": error,
                        "cleanup_error": f"{type(cleanup_exc).__name__}: {cleanup_exc}",
                    }
                self.write_metadata()
                self.runtime.record_event({"event": "attack_failed", **self.metadata()})
                return

        isolation_deadline = time.time() + max(self.tatk, 0.0)

        self.runtime.record_event(
            {
                "event": "attack_started",
                **self.metadata(),
            }
        )

        if self.attack_type == "authority-isolation":
            reachable_nodes = [node for node in self.all_nodes if node.name not in set(self.target_names)]
            self._connectivity_probes["during"] = probe_connectivity(reachable_nodes, self.targets)

        if self.attack_type in {"packetloss", "packetloss-load"}:
            self._packet_loss_installation = apply_packet_loss(self.targets, self.loss_probability)
            self._packet_loss_active = True
            self.runtime.record_event(
                {
                    "event": "packet_loss_rules_installed",
                    **self.metadata(),
                }
            )

        if self.attack_type in {"load", "packetloss-load"}:
            sources = [
                node
                for node in self.client_nodes
                if node.name in self.target_names
            ] or list(self.client_nodes)
            self._load_injector = SyntheticLoadInjector(
                runtime=self.runtime,
                source_nodes=sources,
                destination_nodes=self.all_nodes,
                rate=self.load_rate,
                seed=self.seed + 1009,
            )
            self._load_injector.start(self.tatk)

        if self.attack_type == "authority-isolation":
            while not self._stop.is_set() and time.time() < isolation_deadline:
                self._record_reachability(isolation_active=True, phase="during")
                self._stop.wait(min(1.0, max(isolation_deadline - time.time(), 0.0)))
        else:
            self._sleep(self.tatk)

        if self._load_injector is not None:
            self._load_injector.stop()
            self._load_injector = None

        if self._packet_loss_active:
            self._cleanup_packet_loss_rules()
            self.runtime.record_event(
                {
                    "event": "packet_loss_drop_counters",
                    **self.metadata(),
                }
            )
            self.runtime.record_event(
                {
                    "event": "packet_loss_rules_removed",
                    **self.metadata(),
                }
            )

        if self.attack_type == "authority-isolation":
            with self._cleanup_lock:
                if self._isolation_cleanup is None:
                    self._cleanup_isolation()
            self._record_reachability(isolation_active=False, phase="after")
            self.runtime.record_event({"event": "authority_isolation_removed", **self.metadata()})

        self.runtime.record_event(
            {
                "event": "attack_stopped",
                **self.metadata(),
            }
        )
        if self.attack_type == "authority-isolation":
            post_deadline = time.time() + max(self.tpost, 0.0)
            while not self._stop.is_set() and time.time() < post_deadline:
                self._record_reachability(isolation_active=False, phase="after")
                self._stop.wait(min(1.0, max(post_deadline - time.time(), 0.0)))
        self.write_metadata()

    def _sleep(self, duration: float) -> bool:
        deadline = time.time() + max(duration, 0.0)

        while not self._stop.is_set() and time.time() < deadline:
            remaining = max(deadline - time.time(), 0.0)
            time.sleep(min(remaining, 0.25))

        return not self._stop.is_set()
