"""Centralized configuration parameters for the MeshPay Epidemic DTN router."""

# Maximum number of bundles to send in a single TCP exchange.
# Increased from 64 to 5000 to drastically reduce propagation latency 
# and the number of exchange rounds needed for synchronization.
DEFAULT_MAX_BUNDLES_PER_EXCHANGE = 5000

# Default UDP port used for neighbor discovery broadcasts/requests
DEFAULT_DISCOVERY_PORT = 45555

# Default TCP port used for bundle exchange connections
DEFAULT_EXCHANGE_PORT = 46666

# Default interval (in seconds) between UDP neighbor discovery broadcasts.
DEFAULT_DISCOVERY_INTERVAL = 1.0

# Cooldown interval (in seconds) enforced after a successful bundle exchange with a peer.
# Prevents immediately reconnecting to peers that are already fully synchronized.
DEFAULT_SUCCESS_COOLDOWN = 2.0

# Initial TCP connection timeout (in seconds) when initiating a bundle exchange.
DEFAULT_CONNECT_TIMEOUT = 5.0

# Timeout (in seconds) for active socket read/write operations during bundle exchange.
DEFAULT_SOCKET_TIMEOUT = 30.0

# Maximum backoff interval (in seconds) for retry attempts after connection failure.
DEFAULT_MAX_BACKOFF = 10.0

# Maximum number of concurrent outgoing TCP bundle exchanges allowed per node.
DEFAULT_MAX_PARALLEL_EXCHANGES = 4

# Minimum interval (in seconds) between printed 'contact_missed' log entries per peer.
DEFAULT_CONTACT_MISS_LOG_INTERVAL = 30.0
