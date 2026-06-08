#!/usr/bin/env python3

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class BenchmarkMessage:
    seq: int
    src: str
    dst: str
    payload: str
    scheduled_at: float


class TrafficGenerator:
    """Generates synthetic benchmark traffic.

    This is intentionally independent of Epidemic Routing.

    Today:
        payload is plain text injected into dtn/epidemic.py.

    Later:
        payload can become a serialized FastPay transfer, vote, certificate,
        reconciliation message, or offline payment receipt.
    """

    def __init__(
        self,
        stations: int,
        messages: int,
        message_rate: float,
        payload_size: int,
        seed: int,
        src: Optional[str] = None,
        dst: Optional[str] = None,
    ):
        self.stations = stations
        self.messages = messages
        self.message_rate = message_rate
        self.payload_size = payload_size
        self.seed = seed
        self.src = src
        self.dst = dst
        self.random = random.Random(seed)

    def generate(self) -> Iterable[BenchmarkMessage]:
        for seq in range(self.messages):
            src, dst = self._select_pair()
            payload = self._make_payload(seq, src, dst)
            scheduled_at = seq / self.message_rate

            yield BenchmarkMessage(
                seq=seq,
                src=src,
                dst=dst,
                payload=payload,
                scheduled_at=scheduled_at,
            )

    def _select_pair(self) -> tuple[str, str]:
        if self.src and self.dst:
            return self.src, self.dst

        if self.src and not self.dst:
            src = self.src
            dst = self._random_node_except(src)
            return src, dst

        if self.dst and not self.src:
            dst = self.dst
            src = self._random_node_except(dst)
            return src, dst

        src_index = self.random.randint(1, self.stations)
        dst_index = self.random.randint(1, self.stations)

        while dst_index == src_index:
            dst_index = self.random.randint(1, self.stations)

        return f"sta{src_index}", f"sta{dst_index}"

    def _random_node_except(self, excluded: str) -> str:
        candidates = [
            f"sta{i}"
            for i in range(1, self.stations + 1)
            if f"sta{i}" != excluded
        ]

        return self.random.choice(candidates)

    def _make_payload(self, seq: int, src: str, dst: str) -> str:
        prefix = f"bench seq={seq} src={src} dst={dst} "
        current_size = len(prefix.encode("utf-8"))

        if current_size >= self.payload_size:
            return prefix[: self.payload_size]

        padding_size = self.payload_size - current_size
        return prefix + ("x" * padding_size)