#!/usr/bin/env python3
# ---------------------------------------------------------------------------
# Bundle store / exchange size
# ---------------------------------------------------------------------------

# Maximum bundles transferred in one TCP exchange direction.
# The current wire protocol sends one bundle_batch per side,
# so this value also bounds one application-level TCP message.
# Raise carefully only after verifying there are no socket timeouts.
DEFAULT_MAX_BUNDLES_PER_EXCHANGE: int = 150

# Default bundle time-to-live (seconds). 3600 s ensures bundles survive even
# the longest benchmark/demo run without expiring mid-flight.
DEFAULT_BUNDLE_TTL: float = 3600.0

# ---------------------------------------------------------------------------
# Network ports
# ---------------------------------------------------------------------------

DEFAULT_DISCOVERY_PORT: int = 45555
DEFAULT_EXCHANGE_PORT: int = 46666

# ---------------------------------------------------------------------------
# Discovery timing
# ---------------------------------------------------------------------------

DEFAULT_DISCOVERY_INTERVAL: float = 0.5

# ---------------------------------------------------------------------------
# Exchange cooldowns
# ---------------------------------------------------------------------------

DEFAULT_SUCCESS_COOLDOWN: float = 0.5
DEFAULT_EMPTY_SYNC_COOLDOWN: float = 3.0

# ---------------------------------------------------------------------------
# TCP timeouts
# ---------------------------------------------------------------------------

DEFAULT_CONNECT_TIMEOUT: float = 5.0
DEFAULT_SOCKET_TIMEOUT: float = 15.0

# ---------------------------------------------------------------------------
# Backoff / concurrency
# ---------------------------------------------------------------------------

DEFAULT_MAX_BACKOFF: float = 5.0

# Keep moderate: Mininet-WiFi + Python daemons can become CPU/GIL-bound if each
# node opens too many simultaneous contacts.
DEFAULT_MAX_PARALLEL_EXCHANGES: int = 12

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

DEFAULT_CONTACT_MISS_LOG_INTERVAL: float = 30.0

# ---------------------------------------------------------------------------
# Payment polling (used by MeshPay wallet / authority nodes)
# ---------------------------------------------------------------------------

DEFAULT_PAYMENT_POLL_INTERVAL: float = 0.1