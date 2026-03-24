## MeshPay overview

### What MeshPay is

MeshPay is a **PhD research prototype** for **offline payment emulation over opportunistic IEEE 802.11s wireless mesh networks** with **Layer‑1 blockchain settlement**.

At a high level:

- **Network substrate**: Mininet‑WiFi with IEEE 802.11s mesh links.
- **Authorities**: `WiFiAuthority` nodes that form a committee, validate transfers, and sign certificates.
- **Clients**: Mobile `Client` / `Client1` nodes that hold wallets and initiate payments.
- **Goal**: Achieve **sub‑second retail payments during Internet outages**, then settle batched state on a base chain later.

This is summarized in `CLAUDE.md`:

```5:12:meshpay/CLAUDE.md
MeshPay is a **PhD research prototype** for **offline payment emulation over opportunistic IEEE 802.11s wireless mesh networks** with **Layer-1 blockchain settlement**. It extends [Mininet-WiFi] ... with a FastPay-inspired quorum protocol for sub-second payment finality in infrastructure-less environments.
```

---

## How MeshPay works (end‑to‑end payment flow)

### Nodes and roles

- **Authorities** (validators) live in `meshpay/meshpay/nodes/authority.py`:

```18:27:meshpay/meshpay/nodes/authority.py
from meshpay.types import (
    AccountOffchainState,
    Address,
    AuthorityState,
    ConfirmationOrder,
    SignedTransferOrder,
    NodeType,
    TransactionStatus,
    TransferOrder,
)
```

- **Clients** (wallets) live in `meshpay/meshpay/nodes/client.py` and `client1.py`:

```46:55:meshpay/meshpay/nodes/client.py
class Client(MeshMixin, Station):
    """Client node for opportunistic wireless mesh payment relay.
```

- Both inherit `MeshMixin` (`mesh_utils.py`) for:
  - neighbor management
  - discovery
  - DTN/mesh relay

```28:39:meshpay/meshpay/nodes/mesh_utils.py
class MeshMixin:
    """Shared mesh networking behavior for Client and Authority nodes.

    Expects the host class to have:
      - ``self.name: str``
      - ``self.address: Address``
      - ``self.state`` (with ``.neighbors``, ``.seen_order_ids``)
      - ``self.transport``
      - ``self.logger``
      - ``self._running: bool``
      - ``self.popen(...)`` (Mininet Station method)
    """
```

### Core transfer flow (simplified)

1. **Client builds transfer order**

The client constructs a `TransferOrder` and wraps it in a `TransferRequestMessage`, then in a `MeshRelayMessage`:

```164:190:meshpay/meshpay/nodes/client.py
def transfer(
    self,
    recipient: str,
    token_address: str,
    amount: int,
) -> bool:
    """Initiate a transfer by relaying the order through the mesh."""
    order = TransferOrder(
        order_id=uuid4(),
        sender=self.state.name,
        token_address=token_address,
        recipient=recipient,
        amount=amount,
        sequence_number=self.state.sequence_number,
        timestamp=time.time(),
        signature=self.state.secret,
    )
    request = TransferRequestMessage(transfer_order=order)
    self.state.pending_transfer = order
    self.state.seen_order_ids.add(f"{order.order_id}:req")

    relay_msg = self._build_relay_message(
        inner_type=MessageType.TRANSFER_REQUEST.value,
        inner_payload=request.to_payload(),
        order_id=str(order.order_id),
    )
    return self._relay_to_neighbors(relay_msg) > 0
```

2. **Mesh relay and DTN**

`MeshMixin` delegates to `MeshRelayEngine` to build and relay messages with TTL‑limited hops:

```136:155:meshpay/meshpay/nodes/mesh_utils.py
def _build_relay_message(
    self,
    inner_type: str,
    inner_payload: Dict[str, Any],
    order_id: str,
    sender_id: Optional[str] = None,
    origin_address: Optional[Dict[str, Any]] = None,
    ttl: Optional[int] = None,
    hop_path: Optional[List[str]] = None,
) -> MeshRelayMessage:
    """Build a MeshRelayMessage (DRY helper)."""
    return self.mesh_relay_engine.build_relay_message(
        inner_type=inner_type,
        inner_payload=inner_payload,
        order_id=order_id,
        sender_id=sender_id,
        origin_address=origin_address,
        ttl=ttl,
        hop_path=hop_path,
    )

def _relay_to_neighbors(self, relay: MeshRelayMessage) -> int:
    """(Legacy) Re-routes a relay message to the DTN buffer instead of pure flooding.
    Returns the number of current neighbors (to satisfy old method signatures).
    """
    return self.mesh_relay_engine.relay_to_neighbors(relay)
```

Under the hood, `MeshRelayEngine`:

- Computes dedup keys per `(order_id, inner_type, authority)` to avoid duplicates.
- Buffers messages in a DTN store‑carry‑forward buffer.
- Uses `EpidemicRouting` to exchange summary vectors and request missing IDs.

3. **Authorities process relayed transfer requests**

Authorities unwrap incoming `MESH_RELAY` messages, validate and sign:

```576:585:meshpay/meshpay/nodes/authority.py
if relay.inner_message_type == MessageType.TRANSFER_REQUEST.value:
    request = TransferRequestMessage.from_payload(relay.inner_payload)
    response = self.handle_transfer_order(request.transfer_order)

    self.logger.info(
        f"Processed relayed transfer {order_key} from {relay.original_sender_id}"
    )

    response_relay = self._build_relay_message(
        inner_type=MessageType.TRANSFER_RESPONSE.value,
        inner_payload=response.to_payload(),
        order_id=relay.order_id,
        sender_id=relay.original_sender_id,
        origin_address=relay.origin_address,
    )
    self._relay_to_neighbors(response_relay)
```

4. **Client collects quorum of authority responses**

The client tracks certificates and checks a 2/3 + 1 quorum:

```218:223:meshpay/meshpay/nodes/client.py
committee_size = len(self.state.committee)
quorum = int(committee_size * 2 / 3) + 1 if committee_size > 0 else 1
if len(self.state.sent_certificates) >= quorum and self.state.pending_transfer:
    self.logger.info("Quorum reached – broadcasting confirmation via mesh")
    self.broadcast_confirmation()
```

5. **Confirmation broadcast and settlement**

Once quorum is reached:

- Client broadcasts a `CONFIRMATION_REQUEST` via the mesh.
- Authorities mark the transfer as confirmed and update off‑chain balances.
- Later, a blockchain client submits aggregated confirmations to L1.

### Networking layers involved

- **Transport**: `TCPTransport` (`transport/tcp.py`) runs tiny TCP servers/clients inside each node namespace and exchanges JSON‑encoded `Message` envelopes.
- **Discovery**: `DiscoveryService` (`mesh/discovery_service.py`) handles UDP `PEER_DISCOVERY` broadcasts and neighbor maintenance.
- **DTN routing**: `EpidemicRouting` (`routing/epidemic.py`) exchanges summary vectors and missing keys to keep DTN buffers in sync.
- **Mesh relay**: `MeshRelayEngine` (`mesh/mesh_relay_engine.py`) handles dedup keys, TTL, buffering and re‑relaying.

---

## Telemetry subsystem

MeshPay includes:

- A **telemetry subsystem** in `meshpay/meshpay/telemetry/`.
- A **link‑stats collector** and QoS controller in `meshpay/meshpay/controller/`.

### Telemetry data model

`TelemetryState` captures a **multi‑layer snapshot** per node:

```4:44:meshpay/meshpay/telemetry/telemetry_metrics.py
@dataclass
class WirelessMetrics:
    """Wireless Link & Physical Layer Metrics."""
    rssi_dbm: Optional[float] = None
    tx_bytes: int = 0
    rx_bytes: int = 0
    tx_retries: int = 0
    tx_failed: int = 0
    sinr: Optional[float] = None

@dataclass
class MobilityMetrics:
    """Node Context & Mobility Metrics."""
    position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    speed: float = 0.0
    heading: float = 0.0
    active_neighbors: List[str] = field(default_factory=list)

@dataclass
class ResourceMetrics:
    """Device Resource Metrics."""
    battery_level: Optional[float] = None
    buffer_occupancy: int = 0

@dataclass
class AppMetrics:
    """MeshPay / Application-Specific Metrics."""
    transaction_count: int = 0
    error_count: int = 0
    validation_latency_ms: float = 0.0
    reputation_score: float = 1.0

@dataclass
class TelemetryState:
    """Aggregated full node state for FRL inputs."""
    node_id: str
    timestamp: float
    wireless: WirelessMetrics
    mobility: MobilityMetrics
    resources: ResourceMetrics
    app: AppMetrics
```

This is designed for **FRL/ML‑style controllers**: it exposes physical link quality, mobility, device resources and application‑level performance.

### Telemetry agent on each node

`TelemetryDaemon` runs on a Mininet‑WiFi node and periodically:

1. Samples metrics from the node.
2. Wraps them into a `TelemetryState`.
3. Broadcasts the JSON via UDP.

```7:21:meshpay/meshpay/telemetry/telemetry_agent.py
class TelemetryDaemon:
    """Daemon that gathers metrics from a Mininet-WiFi node and publishes via MQTT."""

    def __init__(
        self,
        node: Any,
        udp_port: int = 5005,
        interval: float = 2.0,
    ) -> None:
        self.node = node
        self.udp_port = udp_port
        self.interval = interval
        self._running = False
        self._thread: threading.Thread | None = None
```

It gathers metrics in `_gather_metrics`:

```42:54:meshpay/meshpay/telemetry/telemetry_agent.py
w_stats = {}
if hasattr(self.node, "get_link_stats"):
    w_stats = self.node.get_link_stats()

wireless = WirelessMetrics(
    rssi_dbm=w_stats.get("signal"),
    tx_bytes=w_stats.get("tx_bytes", 0),
    rx_bytes=w_stats.get("rx_bytes", 0),
    tx_retries=w_stats.get("tx_retries", 0),
    tx_failed=w_stats.get("tx_failed", 0),
    sinr=w_stats.get("sinr")
)
...
app = AppMetrics()
if hasattr(self.node, "performance_metrics"):
    p_stats = self.node.performance_metrics.get_stats()
    app = AppMetrics(
        transaction_count=p_stats.get("transaction_count", 0),
        error_count=p_stats.get("error_count", 0),
        validation_latency_ms=p_stats.get("validation_latency_ms", 0.0),
        reputation_score=p_stats.get("reputation_score", 1.0)
    )
```

Broadcast logic:

```118:128:meshpay/meshpay/telemetry/telemetry_agent.py
metrics = self._gather_metrics()
payload = json.dumps(metrics)
cmd = [
    "python3", "-c",
    "import sys, socket; "
    "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); "
    "s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1); "
    f"s.sendto(sys.stdin.read().encode(), ('10.255.255.255', {self.udp_port}))"
]
if hasattr(self.node, "popen"):
    proc = self.node.popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    ...
```

### Telemetry aggregator (global view)

`TelemetryAggregator` listens on the UDP port and keeps an in‑memory global state:

```9:21:meshpay/meshpay/telemetry/telemetry_controller.py
class TelemetryAggregator:
    """Aggregates telemetry from all nodes via MQTT subscriptions.
    
    Can run on an Authority node or a standalone Controller.
    """

    def __init__(self, udp_port: int = 5005, node: Any = None) -> None:
        self.udp_port = udp_port
        self.node = node
        self.global_state: Dict[str, TelemetryState] = {}
```

It spawns a simple UDP listener:

```37:47:meshpay/meshpay/telemetry/telemetry_controller.py
listener_script = (
    "import socket, sys\n"
    "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\n"
    "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    f"s.bind(('', {self.udp_port}))\n"
    "while True:\n"
    "  data, addr = s.recvfrom(65535)\n"
    "  sys.stdout.write(data.decode() + '\\n')\n"
)
```

And parses each line into `TelemetryState`:

```89:95:meshpay/meshpay/telemetry/telemetry_controller.py
def _handle_message(self, message: str) -> None:
    """Parse incoming JSON telemetry and update global state."""
    try:
        data = json.loads(message.strip())
        state = TelemetryState.from_dict(data)
        self.global_state[state.node_id] = state
```

This yields a **live map**:

- key: `node_id`
- value: latest `TelemetryState`

via `get_network_state()`.

### LinkStatsCollector and QoSManager

For Flash‑Mesh D‑SDN, MeshPay also uses `LinkStatsCollector` and `QoSManager` from `meshpay/meshpay/controller`:

```38:47:meshpay/meshpay/controller/link_stats.py
class LinkStatsCollector:
    """Background sampler that periodically gathers link metrics from nodes.
    ...
    @staticmethod
    def _collect_sample(node) -> LinkSample:
        """Run ``iw`` inside the node namespace and parse results."""
        intf = f"{node.name}-wlan0"
        raw = node.cmd(f"iw dev {intf} station dump 2>/dev/null")
        sample = LinkSample(node_name=node.name)
        ...
```

In `meshpay_demo.py`, these are enabled when `--flashmesh` is passed:

```365:376:meshpay/meshpay/examples/meshpay_demo.py
# Flash-Mesh D-SDN controller (optional)
if args.flashmesh:
    info("*** Enabling Flash-Mesh D-SDN controller\n")
    qos_mgr = QoSManager()
    all_nodes = list(authorities) + list(clients)
    for node in all_nodes:
        qos_mgr.install_priority(node)
    info(f"   ✅ QoS installed on {len(all_nodes)} nodes\n")

    link_stats = LinkStatsCollector(all_nodes, interval_ms=500)
    link_stats.start()
    info("   ✅ Link stats collector started (500ms interval)\n")
```

The authority‑side `MetricsCollector` aggregates these into `network_metrics`:

```60:63:meshpay/mn_wifi/metrics.py
self.network_metrics = NetworkMetrics(
    latency=0.0,
    bandwidth=0.0,
    packet_loss=0.0,
)
```

which is then exposed in the CLI via `do_network_metrics` and used in committee weighting (`committee.py`).

---

## Using telemetry to enhance MeshPay

MeshPay exposes **two complementary metric streams**:

1. **Fine‑grained wireless/performance metrics**:
   - `LinkStatsCollector` → `LinkSample` per node.
   - Aggregated into `MetricsCollector.network_metrics` on each authority.

2. **Rich FRL‑style node state**:
   - `TelemetryDaemon` & `TelemetryAggregator` → `TelemetryState` per node across wireless, mobility, resources, app layer.

### 1. Observability / debugging

Basic usage:

- Run `TelemetryDaemon` on each authority and client.
- Run a `TelemetryAggregator` (host or controller node).
- Plot or log:
  - Mesh partitions (who is visible to whom).
  - Unstable links (RSSI, expected throughput).
  - Overloaded nodes (buffer occupancy, transaction_count, error_count).

This is useful to **explain performance** and tune parameters (`DISCOVERY_INTERVAL`, `NEIGHBOR_TIMEOUT`, TTL values).

### 2. Heuristic routing / relay tuning

You can use metrics for **smarter relaying**:

- **Neighbor choice**:
  - Only relay through neighbors with `wireless.rssi_dbm` and `expected_throughput` above thresholds.
  - Prefer neighbors with high `app.reputation_score` and good `network_metrics.connectivity_ratio`.
- **TTL and retry**:
  - Increase TTL or retry frequency in dense, healthy meshes.
  - Decrease TTL / retries when packet loss is high to avoid flooding.

Conceptually:

- Extend `MeshRelayEngine.relay_to_neighbors` to consult a **local cache of telemetry** (e.g. from `TelemetryAggregator.get_network_state()`).
- Filter `self.state.neighbors` using:
  - Latest `TelemetryState.wireless` stats.
  - Recent timestamps.

On the client side (`Client1` buffered variant):

- Use `TelemetryState.app.validation_latency_ms` and `error_count` to adapt:
  - `_retry_interval` in `_retry_loop`.
  - Back‑off strategy for unstable conditions.

### 3. QoS and queueing

`QoSManager` provides strict‑priority queues:

```40:43:meshpay/meshpay/controller/qos.py
class QoSManager:
    """Manage strict-priority queues on mesh nodes.
    
    For the MVP this uses ``tc`` (works in every Mininet-WiFi namespace).
```

Combined with priorities defined in `types/network.py`:

```20:23:meshpay/meshpay/types/network.py
FASTPAY_BCB = 0     # votes, certificates — highest priority
PAYMENT_DATA = 1    # transfer payloads, balance queries
BEST_EFFORT = 2     # logs, telemetry, model updates
```

you can:

- Tag control‑plane traffic (BCB votes, confirmations) as `FASTPAY_BCB`.
- Tag transfer payloads as `PAYMENT_DATA`.
- Push logs and telemetry into `BEST_EFFORT`.

Telemetry and link stats can then drive **dynamic QoS decisions**:

- When `network_metrics.packet_loss` or RTT increase:
  - Increase share for `FASTPAY_BCB` & `PAYMENT_DATA`.
  - Possibly rate‑limit `BEST_EFFORT` traffic.

### 4. Opportunistic, offline‑aware strategies

In an opportunistic mesh:

- Connectivity is intermittent and mobility is high.
- You can use telemetry to:
  - **Pre‑emptively relay** messages when good links are available (high RSSI, high throughput).
  - Treat nodes with:
    - many `active_neighbors`,
    - strong wireless metrics,
    - good battery
    as **hubs/super‑peers**.
  - Route DTN traffic preferentially through these hubs to improve delivery probability.

TTL and buffering can be made:

- Longer for hub nodes in sparse regions.
- Shorter in dense, well‑connected clusters.

### 5. Authority reputation weighting

`committee.py` already hints at using performance + network metrics for reputation:

```146:152:meshpay/mn_wifi/committee.py
tx = int(stats.get("transaction_count", 0))
errors = int(stats.get("error_count", 0))
net = stats.get("network_metrics", {})
connectivity = float(net.get("connectivity_ratio", 1.0))
```

You can:

- Decrease voting weight (or increase slashing risk) for authorities with:
  - low transaction_count,
  - high error_count,
  - poor connectivity ratio.
- Prefer authorities with stable connectivity and good historical performance.

This **reduces stalls** due to unreachable or unreliable validators.

---

## Decentralized SDN for MeshPay

MeshPay already includes a **Flash‑Mesh D‑SDN controller design**:

- `meshpay/meshpay/controller/*` (QoS, link stats, fallback).
- High‑level plans in `OFFLINE_PAYMENT_PLAN.md` and `d-sdn.md`.

### 1. Local controllers per authority / region

Idea:

- Run a small **SDN agent** on each authority (or selected nodes) that:
  - Subscribes to telemetry (`TelemetryAggregator` or `LinkStatsCollector`).
  - Reads `performance_metrics.get_stats()` including `network_metrics`.
  - Computes **local QoS and forwarding policies**.

Each authority can maintain its own view, avoiding a single centralized controller.

### 2. Flow rules and queues

Use `QoSManager` to install `tc` rules inside each node namespace:

- Mark traffic to/from authorities as high priority.
- Prioritize BCB votes and confirmations.
- Demote non‑critical flows (logs, telemetry, HTTP).
- Optionally bias certain interfaces / neighbors when telemetry indicates better link quality.

### 3. Telemetry‑driven SDN decisions

**Inputs:**

- `TelemetryState.wireless` (RSSI, throughput).
- `TelemetryState.mobility` (position, active_neighbors).
- `TelemetryState.resources` (battery, buffer).
- `TelemetryState.app` (reputation, error_count).
- Authority `network_metrics` and per‑peer link stats.

**Decisions:**

- Which neighbors to prefer for MeshPay traffic.
- When to:
  - Reconfigure `tc` queues.
  - Adjust class weights for different traffic priorities.
  - Demote/penalize nodes that are:
    - misbehaving (high error_count),
    - frequently disconnected (connectivity_ratio low).

### 4. Putting it together (control loop sketch)

A typical SDN control loop for MeshPay might be:

1. `LinkStatsCollector` and `TelemetryDaemon` update metrics every 0.5–2 seconds.
2. For each authority:
   - The SDN agent reads:
     - `link_stats.get_all()`
     - `performance_metrics.get_stats()`
     - `TelemetryAggregator.get_network_state()` (if aggregator runs locally).
3. The agent computes:
   - Neighbor rankings (by reliability and throughput).
   - Updated QoS rules via `QoSManager.install_priority(...)` or similar.
4. MeshPay’s existing relay & routing stack automatically benefit from:
   - Lower loss and delay for control + payment data.
   - Backpressure on best‑effort traffic during congestion.

This architecture keeps **offline, opportunistic mesh payments** robust by:

- Continuously sensing network health.
- Adapting priority, paths and retry behavior.
- Leveraging Ethereum as the **credible settlement layer** above the mesh.

