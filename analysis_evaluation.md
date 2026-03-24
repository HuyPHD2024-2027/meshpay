# MeshPay Implementation Analysis & Evaluation Plan

This document evaluates the current MeshPay implementation in the context of your thesis on "offline payment over opportunistic wireless mesh networks".

## 1. Analysis of Current Implementation

### What is Good (Strengths)

1. **Clean Network Abstraction ([MeshMixin](file:///home/huydq/PHD2024-2027/meshpay/meshpay/nodes/mesh_utils.py#34-335))**:
   - The separation of mesh networking logic from the payment logic is well done.
   - Uses UDP broadcast ([PeerDiscoveryMessage](file:///home/huydq/PHD2024-2027/meshpay/meshpay/messages.py#205-241)) to discover neighbors dynamically, which fits the opportunistic nature of the network.
   - Pings ([_is_reachable](file:///home/huydq/PHD2024-2027/meshpay/meshpay/nodes/mesh_utils.py#86-97)) are used effectively to validate actual link connectivity, handling the physics of signal propagation.
2. **Offline Payment Architecture**:
   - The decoupling of transactions: Clients broadcast a [TransferRequestMessage](file:///home/huydq/PHD2024-2027/meshpay/meshpay/messages.py#95-127), Authorities validate it and respond, and the Client collects signatures to form a quorum before broadcasting a [ConfirmationRequestMessage](file:///home/huydq/PHD2024-2027/meshpay/meshpay/messages.py#158-181). This asynchronous flow is highly resilient to network partitions.
   - Wraps messages in a [MeshRelayMessage](file:///home/huydq/PHD2024-2027/meshpay/meshpay/messages.py#243-284) envelope, allowing pure hop-by-hop forwarding without end-to-end IP routing.
3. **Realistic Testing Environment**:
   - Built on `Mininet-WiFi` with realistic propagation models (`logDistance`) and mobility models (`GaussMarkov`), simulating accurate node encounters.
   - Supports QoS ([tc](file:///home/huydq/PHD2024-2027/meshpay/meshpay/controller/qos.py#114-138) prioritization in [qos.py](file:///home/huydq/PHD2024-2027/meshpay/meshpay/controller/qos.py)), which helps ensure critical payment and confirmation messages bypass bulk traffic.

### What Needs to Change (Weaknesses & Missing DTN Features)

1. **Routing Mechanism (Pure Flooding)**:
   - The current [_relay_to_neighbors](file:///home/huydq/PHD2024-2027/meshpay/meshpay/nodes/mesh_utils.py#206-242) function implements basic flooding: it forwards the message to *all* neighbors not in the `hop_path` as long as `ttl > 0`.
   - **Problem**: In an opportunistic mesh, pure flooding causes severe "broadcast storms", quickly draining battery, saturating bandwidth, and causing packet collisions.
   - **Fix**: Implement a Delay-Tolerant Networking (DTN) routing protocol such as **Epidemic Routing** (with summary vectors to avoid redundant transfers), **PROPHET** (probability-based routing relying on encounter history), or **Spray and Wait**.
2. **Buffer Management (Unbounded Memory)**:
   - `self.state.seen_order_ids` is an unbounded set in [MeshMixin](file:///home/huydq/PHD2024-2027/meshpay/meshpay/nodes/mesh_utils.py#34-335) used for deduplication. Over time, in a long-running opportunistic network, this will consume all available node memory.
   - **Fix**: Implement a TTL or LRU cache for `seen_order_ids`. Evict transaction IDs after a defined period (e.g., 24 hours, or based on the sequence number).
3. **Queueing Strategy and Storage ("Store-Carry-Forward")**:
   - The current [Client](file:///home/huydq/PHD2024-2027/meshpay/meshpay/nodes/client.py#46-336) only relays messages to *active* neighbors at the exact moment of request ([_relay_to_neighbors(relay_msg)](file:///home/huydq/PHD2024-2027/meshpay/meshpay/nodes/mesh_utils.py#206-242)).
   - **Problem**: True opportunistic networking relies on the "Store-Carry-Forward" paradigm. If a node generates a transaction but has no neighbors, it must buffer the message and wait until it encounters another node. Currently, if `successes == 0` when generating [broadcast_confirmation()](file:///home/huydq/PHD2024-2027/meshpay/meshpay/nodes/client.py#259-294), the message is essentially dropped and the node doesn't retry later upon encountering a new neighbor.
   - **Fix**: Implement a local message buffer/queue. When a new neighbor is discovered, trigger an exchange of summary vectors and flush buffered relay messages to the new encounter.
4. **Security and Sybil Protection**:
   - opportunistic networks are highly vulnerable to malicious nodes dropping packets or exhausting the network with fake relay messages.
   - **Fix**: Ensure that `ttl` decrements cannot be easily spoofed, and perhaps require PoW (Proof of Work) or rate-limiting per discovered neighbor to prevent spam.

---

## 2. Evaluation Plan

The evaluation should be conducted using the [meshpay_demo.py](file:///home/huydq/PHD2024-2027/meshpay/meshpay/examples/meshpay_demo.py) Mininet-WiFi emulation to benchmark Bandwidth, Latency, and Resource Consumption.

### Emulation Configuration
Run the demo with mobility explicitly enabled:
`sudo python3 -m mn_wifi.examples.fastpay_mesh_internet_demo --authorities 5 --clients 5 --mobility`

### 1. Bandwidth Measurement
*Goal: Measure the network overhead caused by the opportunistic routing protocol and peer discovery.*
- **Method**: Use `tcpdump` or `wireshark` pcaps within the station namespaces (e.g., `user1_wlan0`).
- **Metrics**: 
  - Compare the ratio of **Control traffic** (UDP discovery beacons on port 8999, mesh relay headers) vs **Data traffic** (actual payment payloads).
  - Use `iw dev station dump` (already implemented in `Authority.get_link_stats()`) to track `rx bytes` and `tx bytes` over a fixed timeline.
  - **Expected Outcome**: Demonstrate how implementing an optimized routing protocol (like Spray and Wait) reduces the total `tx bytes` compared to the current pure flooding approach.

### 2. Latency (Transaction Finality Time)
*Goal: Measure the time representing the "offline finality" - from order creation to quorum.*
- **Method**: Modify [handle_transfer_response](file:///home/huydq/PHD2024-2027/meshpay/meshpay/nodes/client.py#206-228) in [client.py](file:///home/huydq/PHD2024-2027/meshpay/meshpay/nodes/client.py) to record a timestamp.
- **Metrics**:
  - **$T_{start}$**: Time when [transfer()](file:///home/huydq/PHD2024-2027/meshpay/meshpay/nodes/client.py#164-191) is called.
  - **$T_{quorum}$**: Time when $2/3 + 1$ signatures are collected (inside [handle_transfer_response](file:///home/huydq/PHD2024-2027/meshpay/meshpay/nodes/client.py#206-228)).
  - **End-to-End Latency** = $T_{quorum} - T_{start}$.
  - Plot a CDF (Cumulative Distribution Function) of latency across 100 random transfers spanning different source-destination pairs under the `GaussMarkov` mobility model.
  - Test under varying node densities (e.g., sparsely connected vs highly connected) to show how opportunistic encounters affect the tail latency.

### 3. Resource Consumption (CPU & Storage/Memory)
*Goal: Assess whether the implementation is lightweight enough for mobile/IoT devices.*
- **Method (CPU)**: Since Mininet uses process namespaces, measuring exact CPU per node is tricky but possible via `pidstat` mapped to the mininet processes, or using Python's `cProfile` and `memory_profiler` modules around the [_message_handler_loop](file:///home/huydq/PHD2024-2027/meshpay/meshpay/nodes/authority.py#496-506).
- **Method (Storage/Buffer)**:
  - Track the average side of the `seen_order_ids` set and the proposed Store-Carry-Forward message buffer.
  - Log the peak buffer size required to successfully deliver payments in sparse networks.
- **Metrics**: Memory footprint in MBs over time, tracking the effectiveness of a future buffer eviction strategy.

## Summary Conclusion
The current framework provides an excellent simulation foundation with realistic radio models and decoupled asynchronous payment logic. However, to truly support your thesis on **opportunistic** networks, you must replace the immediate broadcast flooding with a persistent **Store-Carry-Forward buffer** and implement a DTN routing algorithm to handle delayed peer encounters efficiently.

