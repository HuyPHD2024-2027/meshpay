#!/usr/bin/env python3

from __future__ import annotations

import threading
from typing import Any

# Mininet Node.cmd()/sendCmd() is not thread-safe.  If two Python threads call
# node.cmd() on the same node concurrently, Mininet can raise:
#     AssertionError: assert self.shell and not self.waiting
# Store one shared re-entrant lock on each node object so every subsystem
# (payment injection, packet-loss attack, debug commands, cleanup) serialises
# access to the node shell.
_LOCK_ATTR = "_meshpay_cmd_lock"
_FALLBACK_LOCKS: dict[int, threading.RLock] = {}
_FALLBACK_LOCKS_GUARD = threading.RLock()


def node_cmd_lock(node: Any) -> threading.RLock:
    """Return the shared command lock for a Mininet node."""
    lock = getattr(node, _LOCK_ATTR, None)
    if isinstance(lock, threading.RLock().__class__):
        return lock

    with _FALLBACK_LOCKS_GUARD:
        lock = getattr(node, _LOCK_ATTR, None)
        if isinstance(lock, threading.RLock().__class__):
            return lock

        new_lock = threading.RLock()
        try:
            setattr(node, _LOCK_ATTR, new_lock)
            return new_lock
        except Exception:
            # Very defensive fallback in case a node implementation rejects
            # dynamic attributes.
            key = id(node)
            existing = _FALLBACK_LOCKS.get(key)
            if existing is None:
                _FALLBACK_LOCKS[key] = new_lock
                existing = new_lock
            return existing


def safe_node_cmd(node: Any, cmd: str) -> str:
    """Run ``node.cmd(cmd)`` under the node's shared command lock."""
    with node_cmd_lock(node):
        return node.cmd(cmd)