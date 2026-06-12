#!/usr/bin/env python3

from __future__ import annotations

import json
import random
import threading
import time
from typing import Sequence

from dtn.bundle import Bundle
from dtn.store import BundleStore


class SyntheticLoadInjector:
    """Inject synthetic DTN bundles during an attack window."""

    def __init__(
        self,
        runtime,
        source_nodes: Sequence,
        destination_nodes: Sequence,
        rate: float,
        seed: int,
    ) -> None:
        self.runtime = runtime
        self.source_nodes = list(source_nodes)
        self.destination_nodes = list(destination_nodes)
        self.rate = float(rate)
        self.rng = random.Random(seed)

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sequence = 0

    def start(self, duration: float) -> None:
        if self.rate <= 0 or not self.source_nodes or len(self.destination_nodes) < 2:
            return

        if self._thread is not None:
            return

        self._thread = threading.Thread(
            target=self._run,
            args=(float(duration),),
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self, duration: float) -> None:
        deadline = time.time() + duration
        interval = 1.0 / self.rate
        next_send = time.time()

        while not self._stop.is_set() and time.time() < deadline:
            now = time.time()
            if now < next_send:
                time.sleep(min(next_send - now, 0.05))
                continue

            self.inject_one()
            next_send += interval

    def inject_one(self) -> None:
        source = self.rng.choice(self.source_nodes)
        destinations = [
            node
            for node in self.destination_nodes
            if node.name != source.name
        ]

        if not destinations:
            return

        destination = self.rng.choice(destinations)
        self._sequence += 1

        payload = {
            "app": "meshpay.attack",
            "type": "synthetic_load",
            "data": {
                "sequence": self._sequence,
                "src": source.name,
                "dst": destination.name,
                "created_at": time.time(),
            },
        }

        bundle = Bundle.create(
            src=source.name,
            dst=destination.name,
            payload=payload,
            ttl=300.0,
        )

        store = BundleStore(self.runtime.store_for(source.name))
        store.save(bundle)
        store.record_event(
            {
                "event": "created",
                "node": source.name,
                "bundle_id": bundle.bundle_id,
                "src": source.name,
                "dst": destination.name,
                "size_bytes": bundle.size_bytes,
                "payload": payload,
            }
        )

        payload_size_bytes = len(json.dumps(payload, sort_keys=True).encode("utf-8"))
        self.runtime.record_event(
            {
                "event": "attack_load_injected",
                "src": source.name,
                "dst": destination.name,
                "bundle_id": bundle.bundle_id,
                "payload_size_bytes": payload_size_bytes,
            }
        )

