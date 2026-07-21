#!/usr/bin/env python3

from __future__ import annotations


def make_account_id(station_name: str, account_index: int) -> str:
    """Return a logical account id hosted by one physical station.

    Example:
        make_account_id("sta1", 7) -> "sta1/u00007"
    """

    return f"{station_name}/u{account_index:05d}"


def account_host(account_id: str) -> str:
    """Return the physical station that hosts a logical account.

    Examples:
        "sta1/u00007" -> "sta1"
        "sta1"        -> "sta1"
    """

    return account_id.split("/", 1)[0]


def is_virtual_account(account_id: str) -> bool:
    return "/" in account_id