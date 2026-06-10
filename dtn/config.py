"""Centralized configuration parameters for the MeshPay Epidemic DTN router."""

# Default UDP port used for neighbor discovery broadcasts/requests
DEFAULT_DISCOVERY_PORT = 45555

# Default TCP port used for bundle exchange connections
DEFAULT_EXCHANGE_PORT = 46666

# Default interval (in seconds) between UDP neighbor discovery broadcasts.
# Increased from 0.5s to 2.0s to mitigate UDP broadcast storms in dense networks.
DEFAULT_DISCOVERY_INTERVAL = 2.0

# Cooldown interval (in seconds) enforced after a successful bundle exchange with a peer.
# Prevents immediately reconnecting to peers that are already fully synchronized.
DEFAULT_SUCCESS_COOLDOWN = 15.0

# Initial TCP connection timeout (in seconds) when initiating a bundle exchange.
DEFAULT_CONNECT_TIMEOUT = 5.0

# Timeout (in seconds) for active socket read/write operations during bundle exchange.
DEFAULT_SOCKET_TIMEOUT = 10.0

# Maximum backoff interval (in seconds) for retry attempts after connection failure.
DEFAULT_MAX_BACKOFF = 30.0

# Maximum number of concurrent outgoing TCP bundle exchanges allowed per node.
DEFAULT_MAX_PARALLEL_EXCHANGES = 4

# Minimum interval (in seconds) between printed 'contact_missed' log entries per peer.
DEFAULT_CONTACT_MISS_LOG_INTERVAL = 30.0
