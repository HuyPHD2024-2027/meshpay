"""Centralized configuration parameters for the MeshPay Epidemic DTN router."""

# DECREASED: 10000 causes massive JSON serialization CPU spikes. 
# 200 ensures quick, lightweight TCP exchanges that don't block Python.
DEFAULT_MAX_BUNDLES_PER_EXCHANGE = 100

# Default UDP port used for neighbor discovery broadcasts/requests
DEFAULT_DISCOVERY_PORT = 45555

# Default TCP port used for bundle exchange connections
DEFAULT_EXCHANGE_PORT = 46666

# INCREASED: Give the OS breathing room between 'iw station dump' commands.
DEFAULT_DISCOVERY_INTERVAL = 3.0

# INCREASED: Nodes should wait 0.5 seconds before re-syncing with the same peer
# to prevent endless TCP synchronization storms under heavy traffic.
DEFAULT_SUCCESS_COOLDOWN = 0.5

# Initial TCP connection timeout (in seconds) when initiating a bundle exchange.
DEFAULT_CONNECT_TIMEOUT = 2.0

# Timeout (in seconds) for active socket read/write operations during bundle exchange.
DEFAULT_SOCKET_TIMEOUT = 5.0

# Maximum backoff interval (in seconds) for retry attempts after connection failure.
DEFAULT_MAX_BACKOFF = 3.0

# Maximum number of concurrent outgoing TCP bundle exchanges allowed per node.
DEFAULT_MAX_PARALLEL_EXCHANGES = 16

# Minimum interval (in seconds) between printed 'contact_missed' log entries per peer.
DEFAULT_CONTACT_MISS_LOG_INTERVAL = 30.0

# Polling interval (in seconds) for checking new payment bundles in the delivered log.
DEFAULT_PAYMENT_POLL_INTERVAL = 0.1