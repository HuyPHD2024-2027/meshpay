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

        self.targets = select_targets(
            nodes=self.all_nodes,
            seed=self.seed,
            target_count=self.target_count,
        )
        self.target_names = [node.name for node in self.targets]

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._load_injector: SyntheticLoadInjector | None = None
        self._packet_loss_active = False
        self._packet_loss_installation: dict | None = None
        self._packet_loss_drop_counters: dict | None = None
        self._packet_loss_cleanup: dict | None = None

    def metadata(self) -> dict:
        return {
            "attack": self.attack_type,
            "loss_probability": self.loss_probability,
            "tpre": self.tpre,
            "tatk": self.tatk,
            "tpost": self.tpost,
            "target_count": self.target_count,
            "target_selection": (
                "all_nodes"
                if len(self.targets) == len(self.all_nodes)
                else "random_subset"
            ),
            "selected_target_count": len(self.targets),
            "targets": list(self.target_names),
            "load_rate": self.load_rate,
            "seed": self.seed,
            "attack_mode": (
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
        }

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
        self._stop.set()

        if self._load_injector is not None:
            self._load_injector.stop()
            self._load_injector = None

        if self._packet_loss_active:
            self._cleanup_packet_loss_rules()

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

    def _run(self) -> None:
        if not self._sleep(self.tpre):
            return

        self.runtime.record_event(
            {
                "event": "attack_started",
                **self.metadata(),
            }
        )

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

        self.runtime.record_event(
            {
                "event": "attack_stopped",
                **self.metadata(),
            }
        )
        self.write_metadata()

    def _sleep(self, duration: float) -> bool:
        deadline = time.time() + max(duration, 0.0)

        while not self._stop.is_set() and time.time() < deadline:
            remaining = max(deadline - time.time(), 0.0)
            time.sleep(min(remaining, 0.25))

        return not self._stop.is_set()
