"""Centralized configuration parameters for the MeshPay Epidemic DTN router."""

# Maximum number of bundles to send in a single TCP exchange.
# Capped at 200 to keep individual exchanges short (~100 KB) so they
# don't monopolize the wireless channel.  Priority sorting ensures
# confirmations and signatures are sent before transfer orders.
DEFAULT_MAX_BUNDLES_PER_EXCHANGE = 200

# Default UDP port used for neighbor discovery broadcasts/requests
DEFAULT_DISCOVERY_PORT = 45555

# Default TCP port used for bundle exchange connections
DEFAULT_EXCHANGE_PORT = 46666

# Default interval (in seconds) between UDP neighbor discovery broadcasts.
# Reduced from 5.0 to 1.0 for faster peer finding — the 3-round MeshPay
# protocol benefits greatly from tighter discovery cycles.
DEFAULT_DISCOVERY_INTERVAL = 1.0

# Cooldown interval (in seconds) enforced after a successful bundle exchange with a peer.
# Reduced from 1.0 to 0.1 so newly injected bundles propagate within ~100ms
# instead of waiting a full second.
DEFAULT_SUCCESS_COOLDOWN = 0.1

# Initial TCP connection timeout (in seconds) when initiating a bundle exchange.
# Reduced from 8.0 to 2.0 to free exchange slots faster when peers move
# out of range.  Still well above typical LAN RTT.
DEFAULT_CONNECT_TIMEOUT = 2.0

# Timeout (in seconds) for active socket read/write operations during bundle exchange.
DEFAULT_SOCKET_TIMEOUT = 30.0

# Maximum backoff interval (in seconds) for retry attempts after connection failure.
# Reduced from 10.0 to 3.0 so nodes reconnect faster after mobility events.
DEFAULT_MAX_BACKOFF = 3.0

# Maximum number of concurrent outgoing TCP bundle exchanges allowed per node.
# Set to 4 as a balance between parallelism (more slots = faster propagation)
# and wireless MAC contention (more slots = more simultaneous TCP streams
# causing 802.11 collisions and backoff).
DEFAULT_MAX_PARALLEL_EXCHANGES = 4

# Minimum interval (in seconds) between printed 'contact_missed' log entries per peer.
DEFAULT_CONTACT_MISS_LOG_INTERVAL = 30.0

# Polling interval (in seconds) for checking new payment bundles in the delivered log.
DEFAULT_PAYMENT_POLL_INTERVAL = 0.1
