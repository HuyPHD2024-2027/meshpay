"""Run-scoped performance-weight registry for MeshPay quorum certificates."""

from __future__ import annotations

import fcntl
import json
import math
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable


TOTAL_WEIGHT_UNITS = 1_000_000
REGISTRY_VERSION = 1


@dataclass(frozen=True)
class WeightSnapshot:
    epoch: int
    committee: tuple[str, ...]
    committee_digest: str
    weights: Dict[str, int]
    total_weight_units: int

    def weight_for(self, authority: str) -> int:
        return int(self.weights.get(authority, 0))


class WeightRegistry:
    """Persist weighted-quorum epochs with process-safe, idempotent updates."""

    def __init__(
        self,
        path: str | Path,
        committee: Iterable[str],
        epoch_size: int = 100,
        max_power_share: float = 0.30,
    ) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.committee = tuple(sorted(set(committee)))
        self.epoch_size = int(epoch_size)
        self.max_power_share = float(max_power_share)
        if not self.committee:
            raise ValueError("weighted quorum requires a non-empty committee")
        if self.epoch_size < 1:
            raise ValueError("weight epoch size must be at least 1")
        if not 0.0 < self.max_power_share <= 1.0:
            raise ValueError("max voting power share must be in (0, 1]")

    def initialize(self) -> WeightSnapshot:
        with self._locked_state() as state:
            self._validate_configuration(state)
            return self._snapshot(state)

    def current_snapshot(self) -> WeightSnapshot:
        return self.initialize()

    def snapshot_for_epoch(self, epoch: int) -> WeightSnapshot | None:
        with self._locked_state() as state:
            self._validate_configuration(state)
            raw = state["snapshots"].get(str(int(epoch)))
            return self._snapshot_from_raw(raw) if raw else None

    def record_finalization(self, order_id: str, signers: Iterable[str]) -> WeightSnapshot:
        """Record one finalized certificate and apply an epoch rollover if due."""
        unique_signers = sorted(set(signers))
        if not unique_signers or any(signer not in self.committee for signer in unique_signers):
            raise ValueError("finalization signers must be committee members")

        with self._locked_state() as state:
            self._validate_configuration(state)
            if order_id in state["finalized_order_ids"]:
                return self._snapshot(state)

            state["finalized_order_ids"].append(order_id)
            for signer in unique_signers:
                state["pending_rewards"][signer] += 1
            state["finalizations_in_epoch"] += 1

            if state["finalizations_in_epoch"] >= self.epoch_size:
                for authority, reward in state["pending_rewards"].items():
                    state["tx_counts"][authority] += int(reward)
                    state["pending_rewards"][authority] = 0
                state["finalizations_in_epoch"] = 0
                state["current_epoch"] += 1
                snapshot = self._make_snapshot(state)
                state["snapshots"][str(state["current_epoch"])] = snapshot

            return self._snapshot(state)

    def authority_stats(self, authority: str) -> tuple[int, int]:
        snapshot = self.current_snapshot()
        with self._locked_state() as state:
            return int(state["tx_counts"].get(authority, 0)), snapshot.weight_for(authority)

    @contextmanager
    def _locked_state(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.lock_path.open("a+", encoding="utf-8")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            state = self._read_or_create()
            yield state
            self._write(state)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def _read_or_create(self) -> dict:
        if not self.path.exists():
            state = {
                "version": REGISTRY_VERSION,
                "committee": list(self.committee),
                "committee_digest": self._committee_digest(),
                "epoch_size": self.epoch_size,
                "max_power_share": self.max_power_share,
                "current_epoch": 0,
                "tx_counts": {authority: 0 for authority in self.committee},
                "pending_rewards": {authority: 0 for authority in self.committee},
                "finalizations_in_epoch": 0,
                "finalized_order_ids": [],
                "snapshots": {},
            }
            state["snapshots"]["0"] = self._make_snapshot(state)
            return state
        with self.path.open(encoding="utf-8") as f:
            return json.load(f)

    def _validate_configuration(self, state: dict) -> None:
        expected = {
            "version": REGISTRY_VERSION,
            "committee": list(self.committee),
            "committee_digest": self._committee_digest(),
            "epoch_size": self.epoch_size,
            "max_power_share": self.max_power_share,
        }
        for key, value in expected.items():
            if state.get(key) != value:
                raise ValueError(f"weighted quorum registry mismatch for {key}")

    def _snapshot(self, state: dict) -> WeightSnapshot:
        raw = state["snapshots"][str(state["current_epoch"])]
        return self._snapshot_from_raw(raw)

    def _snapshot_from_raw(self, raw: dict) -> WeightSnapshot:
        return WeightSnapshot(
            epoch=int(raw["epoch"]),
            committee=tuple(raw["committee"]),
            committee_digest=str(raw["committee_digest"]),
            weights={str(k): int(v) for k, v in raw["weights"].items()},
            total_weight_units=int(raw["total_weight_units"]),
        )

    def _make_snapshot(self, state: dict) -> dict:
        tx_counts = {name: int(state["tx_counts"][name]) for name in self.committee}
        return {
            "epoch": int(state["current_epoch"]),
            "committee": list(self.committee),
            "committee_digest": self._committee_digest(),
            "weights": self._allocate_weights(tx_counts),
            "total_weight_units": TOTAL_WEIGHT_UNITS,
        }

    def _allocate_weights(self, tx_counts: Dict[str, int]) -> Dict[str, int]:
        raw = {name: 1 + max(0, int(tx_counts[name])) for name in self.committee}
        cap = max(
            int(math.ceil(TOTAL_WEIGHT_UNITS / len(self.committee))),
            int(math.floor(self.max_power_share * TOTAL_WEIGHT_UNITS)),
        )
        remaining = set(self.committee)
        allocated = {name: 0 for name in self.committee}
        budget = TOTAL_WEIGHT_UNITS
        while remaining:
            raw_total = sum(raw[name] for name in remaining)
            capped = [
                name for name in remaining
                if raw[name] * budget / raw_total > cap
            ]
            if not capped:
                quotas = {name: raw[name] * budget / raw_total for name in remaining}
                for name, quota in quotas.items():
                    allocated[name] = int(math.floor(quota))
                remainder = budget - sum(allocated[name] for name in remaining)
                for name in sorted(remaining, key=lambda n: (quotas[n] % 1, n), reverse=True)[:remainder]:
                    allocated[name] += 1
                break
            for name in capped:
                allocated[name] = cap
                budget -= cap
                remaining.remove(name)
        return allocated

    def _committee_digest(self) -> str:
        import hashlib

        payload = ",".join(self.committee).encode("ascii")
        return hashlib.sha256(payload).hexdigest()

    def _write(self, state: dict) -> None:
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(state, f, sort_keys=True, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, self.path)
