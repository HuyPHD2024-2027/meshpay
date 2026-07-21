#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class BenchmarkConfig:
    """Configuration for an OppNet/MeshPay benchmark run."""

    routing: str
    medium: str
    stations: int

    messages: int
    message_rate: float
    payload_size: int
    duration: float

    src: Optional[str]
    dst: Optional[str]

    seed: int
    log_dir: Path
    clean: bool

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["log_dir"] = str(self.log_dir)
        return data

    @property
    def injection_duration(self) -> float:
        if self.messages <= 1:
            return 0.0
        return (self.messages - 1) / self.message_rate

    def validate(self) -> None:
        if self.stations < 2:
            raise ValueError("--stations must be at least 2")

        if self.messages < 1:
            raise ValueError("--messages must be at least 1")

        if self.message_rate <= 0:
            raise ValueError("--message-rate must be greater than 0")

        if self.payload_size < 1:
            raise ValueError("--payload-size must be at least 1")

        if self.duration <= 0:
            raise ValueError("--duration must be greater than 0")

        if self.injection_duration > self.duration:
            raise ValueError(
                "Benchmark duration is too short for the requested traffic. "
                f"Need at least {self.injection_duration:.2f}s to inject "
                f"{self.messages} messages at {self.message_rate} msg/s."
            )

        valid_nodes = {f"sta{i}" for i in range(1, self.stations + 1)}

        if self.src is not None and self.src not in valid_nodes:
            raise ValueError(f"--src must be one of {sorted(valid_nodes)}")

        if self.dst is not None and self.dst not in valid_nodes:
            raise ValueError(f"--dst must be one of {sorted(valid_nodes)}")

        if self.src is not None and self.dst is not None and self.src == self.dst:
            raise ValueError("--src and --dst must be different")