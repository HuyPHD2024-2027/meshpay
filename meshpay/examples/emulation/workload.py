"""Offline payment workload submission for MeshPay emulation benchmarks."""

from __future__ import annotations

import random
import time
from typing import Iterable, List, Tuple

from mininet.log import info
from mn_wifi.services.core.config import SUPPORTED_TOKENS

from meshpay.examples.emulation.config import WorkloadItem
from meshpay.nodes.client import Client


def generate_deterministic_workload(clients: int, size: int, seed: int) -> Tuple[WorkloadItem, ...]:
    """Generate a reproducible valid transfer workload across clients."""

    if clients < 2 or size <= 0:
        return tuple()

    rng = random.Random(seed)
    names = [f"user{i}" for i in range(1, clients + 1)]
    workload = []
    for index in range(size):
        sender = names[index % len(names)]
        recipients = [name for name in names if name != sender]
        recipient = rng.choice(recipients)
        amount = rng.randint(1, 25)
        workload.append(WorkloadItem(sender, recipient, amount))
    return tuple(workload)


def submit_workload(
    clients: List[Client],
    workload: Iterable[WorkloadItem],
    duration: int,
    *,
    interval: float = 1.5,
    pending_wait_timeout: float | None = None,
) -> int:
    """Submit the staggered offline payment workload and return accepted orders."""

    client_map = {client.name: client for client in clients}
    xtz_token = SUPPORTED_TOKENS.get("XTZ", {}).get("address", "")
    per_sender_wait_timeout = pending_wait_timeout if pending_wait_timeout is not None else max(5.0, duration / 2.0)
    submitted_orders = 0

    for item in workload:
        sender = client_map.get(item.sender)
        if not sender:
            continue

        wait_start = time.time()
        while sender.state.pending_transfer is not None and time.time() - wait_start < per_sender_wait_timeout:
            time.sleep(0.2)

        if sender.state.pending_transfer is not None:
            pending_id = sender.state.pending_transfer.order_id
            info(
                f"⚠️  [{item.sender}] Skipping transfer to {item.recipient}: "
                f"pending order {pending_id} did not clear within {per_sender_wait_timeout:.1f}s\n"
            )
            continue

        info(f"📤 [{item.sender}] Submitting transfer: {item.amount} XTZ to {item.recipient}\n")
        sender.transfer(item.recipient, xtz_token, item.amount)
        submitted_orders += 1
        time.sleep(max(0.0, interval))

    return submitted_orders

