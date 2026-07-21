#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Wallet:
    """Simple client wallet for the first offline-payment version."""

    owner: str
    balance: int = 0
    sequence_number: int = 0

    def next_sequence(self) -> int:
        """Return the next client sequence number."""

        self.sequence_number += 1
        return self.sequence_number

    def can_debit(self, amount: int) -> bool:
        return amount > 0 and self.balance >= amount

    def debit(self, amount: int) -> None:
        if amount <= 0:
            raise ValueError("amount must be positive")

        if self.balance < amount:
            raise ValueError("insufficient balance")

        self.balance -= amount

    def credit(self, amount: int) -> None:
        if amount <= 0:
            raise ValueError("amount must be positive")

        self.balance += amount