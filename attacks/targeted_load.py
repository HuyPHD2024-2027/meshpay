#!/usr/bin/env python3

from __future__ import annotations

import random
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Sequence

from meshpay.offline.virtual_accounts import account_host

DEFAULT_TARGETED_LOAD_TPS = 200.0


class SyntheticLoadInjector:
    """Submit valid MeshPay payments to targeted client ingress nodes.

    The class name is kept for compatibility with the existing attack
    controller, but the load is native MeshPay traffic rather than synthetic
    DTN bundles.
    """

    def __init__(
        self,
        runtime,
        source_nodes: Sequence,
        destination_nodes: Sequence,
        rate: float,
        seed: int,
        amount: int = 1,
        max_workers: int | None = None,
    ) -> None:
        self.runtime = runtime
        self.source_nodes = list(source_nodes)
        self.destination_nodes = list(destination_nodes)
        self.rate = float(rate) if float(rate) > 0 else DEFAULT_TARGETED_LOAD_TPS
        self.amount = int(amount)
        self.max_workers = max_workers
        self.rng = random.Random(seed)

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._sequence = 0
        self._attempted = 0
        self._succeeded = 0
        self._failed = 0
        self._backpressure = 0

        self._source_accounts = deque(self._collect_accounts(self.source_nodes))
        self._recipient_accounts = self._collect_accounts(self.destination_nodes)
        self._submit_locks = {node.name: threading.Lock() for node in self.source_nodes}

    def start(self, duration: float) -> None:
        if self.rate <= 0 or not self._source_accounts or len(self._recipient_accounts) < 2:
            self.runtime.record_event(
                {
                    "event": "attack_load_not_started",
                    "reason": "insufficient_accounts_or_rate",
                    "rate": self.rate,
                    "source_accounts": len(self._source_accounts),
                    "recipient_accounts": len(self._recipient_accounts),
                }
            )
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
            self._thread.join(timeout=10.0)
            self._thread = None

    def _run(self, duration: float) -> None:
        deadline = time.time() + duration
        interval = 1.0 / self.rate
        next_send = time.time()
        worker_count = self.max_workers or min(64, max(4, int(self.rate)))
        max_pending = max(worker_count * 2, 8)

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            pending: set[Future] = set()
            while not self._stop.is_set() and time.time() < deadline:
                pending = {future for future in pending if not future.done()}
                if len(pending) >= max_pending:
                    self._record_backpressure()
                    wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                    continue

                now = time.time()
                if now < next_send:
                    time.sleep(min(next_send - now, 0.01))
                    continue

                pair = self._next_payment_pair()
                if pair is None:
                    self._record_backpressure()
                else:
                    sender_account, recipient_account = pair
                    self._attempted += 1
                    pending.add(executor.submit(self._submit_payment, sender_account, recipient_account))

                next_send += interval
                if next_send < time.time() - 1.0:
                    next_send = time.time()

        self.runtime.record_event(
            {
                "event": "attack_load_finished",
                "rate": self.rate,
                "duration_s": duration,
                "attempted": self._attempted,
                "succeeded": self._succeeded,
                "failed": self._failed,
                "backpressure": self._backpressure,
                "source_accounts": len(self._source_accounts),
                "recipient_accounts": len(self._recipient_accounts),
            }
        )

    def _collect_accounts(self, nodes: Sequence) -> list[str]:
        accounts: list[str] = []
        for node in nodes:
            if hasattr(node, "hosted_accounts"):
                hosted = list(node.hosted_accounts(virtual_only=True))
                if not hosted:
                    hosted = list(node.hosted_accounts(virtual_only=False))
            else:
                hosted = [node.name]
            accounts.extend(hosted)

        self.rng.shuffle(accounts)
        return accounts

    def _next_payment_pair(self) -> tuple[str, str] | None:
        with self._lock:
            source_count = len(self._source_accounts)
            for _ in range(source_count):
                sender_account = self._source_accounts.popleft()
                self._source_accounts.append(sender_account)
                source_node = self.runtime.net.get(account_host(sender_account))

                with source_node._lock:
                    can_pay = source_node.can_pay_from(sender_account, self.amount)

                if not can_pay:
                    continue

                recipients = [
                    account
                    for account in self._recipient_accounts
                    if account != sender_account and account_host(account) != account_host(sender_account)
                ]
                if not recipients:
                    recipients = [account for account in self._recipient_accounts if account != sender_account]
                if not recipients:
                    return None

                return sender_account, self.rng.choice(recipients)

        return None

    def _submit_payment(self, sender_account: str, recipient_account: str) -> None:
        host = account_host(sender_account)
        try:
            submit_lock = self._submit_locks.setdefault(host, threading.Lock())
            with submit_lock:
                self.runtime.pay_account(
                    sender_account=sender_account,
                    recipient_account=recipient_account,
                    amount=self.amount,
                )
            with self._lock:
                self._sequence += 1
                sequence = self._sequence
                self._succeeded += 1
            self.runtime.record_event(
                {
                    "event": "attack_payment_created",
                    "sequence": sequence,
                    "sender": sender_account,
                    "recipient": recipient_account,
                    "sender_host": host,
                    "recipient_host": account_host(recipient_account),
                    "amount": self.amount,
                    "rate": self.rate,
                }
            )
        except Exception as exc:
            with self._lock:
                self._failed += 1
            self.runtime.record_event(
                {
                    "event": "attack_payment_submit_failed",
                    "sender": sender_account,
                    "recipient": recipient_account,
                    "sender_host": host,
                    "recipient_host": account_host(recipient_account),
                    "amount": self.amount,
                    "error": f"{type(exc).__name__}: {exc!r}",
                }
            )

    def _record_backpressure(self) -> None:
        with self._lock:
            self._backpressure += 1
            backpressure = self._backpressure

        if backpressure == 1 or backpressure % 100 == 0:
            self.runtime.record_event(
                {
                    "event": "attack_load_backpressure",
                    "reason": "no targeted sender account is currently available",
                    "backpressure": backpressure,
                    "source_accounts": len(self._source_accounts),
                    "rate": self.rate,
                }
            )
