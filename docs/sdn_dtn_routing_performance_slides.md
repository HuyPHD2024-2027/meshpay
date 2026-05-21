---
marp: true
theme: default
paginate: true
---

# Improving Opportunistic Routing with SDN-DTN in MeshPay

Performance evaluation of epidemic routing vs SDN-guided DTN routing for offline payment finality.

**Context:** Mobile wireless mesh, intermittent connectivity, 5 authorities, 3 clients.

**Speaker notes:**  
Today I will explain what I implemented to improve routing performance in MeshPay under opportunistic connectivity. The main comparison is between epidemic routing and an SDN-DTN approach that uses application-aware forwarding decisions.

---

# Problem

Opportunistic mesh networks have unstable paths.

- Nodes meet briefly and unpredictably
- No stable end-to-end route is guaranteed
- Payment messages must still reach enough authorities for quorum
- Blind flooding creates high overhead and large buffers

**Speaker notes:**  
The routing problem is not just packet delivery. For offline payments, the important goal is to collect enough authority signatures quickly and distribute the confirmation, despite mobility and disconnections.

---

# Baseline: Epidemic Routing

Epidemic routing uses store-carry-forward flooding.

1. Nodes advertise buffered message IDs
2. Neighbors request missing messages
3. Messages are replicated widely
4. Delivery improves, but overhead grows quickly

**Strength:** robust under disconnection  
**Weakness:** no awareness of payment priority or network state

**Speaker notes:**  
Epidemic routing is a good baseline because it is simple and robust. But it treats all messages similarly, so a low-priority transfer request and a quorum-critical certificate may compete for the same buffer and contact opportunity.

---

# MeshPay Message Flow

MeshPay payment finality requires three phases.

1. **Transfer request:** client sends spend intent
2. **Transfer responses:** authorities validate and sign
3. **Confirmation request:** client broadcasts quorum certificate

Finality depends on receiving at least `2f + 1` authority signatures.

**Speaker notes:**  
The critical latency is from submitting a transfer until the sender has enough authority signatures to build a confirmation. Therefore, routing should prioritize requests, votes, and confirmations differently.

---

# SDN-DTN Design Goal

Use SDN-style control to guide DTN forwarding.

- Classify traffic by payment importance
- Prioritize quorum-critical messages
- Limit unnecessary replication
- Prune finalized transactions from buffers
- Keep epidemic-style anti-entropy only as fallback

**Speaker notes:**  
The SDN-DTN design does not assume a permanent controller connection. Authorities act as embedded controllers and distribute forwarding policy opportunistically.

---

# What I Implemented

In `meshpay/routing/sdn.py`:

- SDN forwarding policy with replication limits
- Priority sorting of outgoing bundles
- Active pruning of finalized transactions
- Per-epoch policy throttling
- Fast-path push for critical payment messages

**Speaker notes:**  
The first version reduced overhead and buffer size, but latency was worse because it still waited for summary/request exchange. I then added a fast path that directly pushes critical messages to selected neighbors.

---

# Key Improvement: Priority Fast Path

Instead of waiting for:

`summary -> request -> relay`

SDN-DTN now directly pushes:

- transfer requests to authority neighbors
- authority votes back to the sender client
- confirmations to authorities and recipient

Anti-entropy remains available for repair.

**Speaker notes:**  
This is the key routing improvement. Epidemic routing needs an extra control round trip before data moves. SDN-DTN now skips that round trip for messages on the payment finality path.

---

# Why This Should Improve Latency

Payment finality is bottlenecked by quorum assembly.

SDN-DTN improves this by:

- sending transfer requests quickly to authorities
- returning signatures directly to the originating client
- prioritizing votes and confirmations over ordinary messages
- avoiding buffer congestion from stale finalized traffic

**Speaker notes:**  
The performance benefit should appear most clearly in end-to-end finality latency, not only in bandwidth overhead. If the sender receives four signatures earlier, finality is reached earlier.

---

# Buffer and Overhead Optimization

SDN-DTN reduces waste by controlling replication.

- Policies are sent once per epoch, not every discovery tick
- Summaries are throttled
- Finalized orders are removed from buffers
- Critical messages are pushed selectively
- Lower-priority repair traffic is limited

**Speaker notes:**  
This explains why SDN-DTN can reduce forwarding overhead and buffer occupancy. It does not flood everything forever; it uses application knowledge to decide what still matters.

---

# Benchmark Fixes

I also corrected benchmark logic.

- Finality now counts unique transfer order IDs
- Success is recorded only after confirmation is built
- Duplicate finality events are ignored
- Same-sender transfers are paced to avoid overwriting `pending_transfer`
- Benchmark waits for peer discovery before workload injection

**Speaker notes:**  
Before this correction, finality could exceed 100%, for example 8 successful events for 6 transfers. That was a counting and state-management bug, not a real protocol result.

---

# Experimental Setup

Current benchmark:

- 5 authority nodes
- 3 mobile client nodes
- IEEE 802.11s mesh emulation
- Gauss-Markov mobility
- 6 offline payment transfers
- 40 second emulation window

Metrics:

- finality rate
- end-to-end latency
- control overhead
- forwarding overhead
- remaining buffer occupancy

**Speaker notes:**  
These metrics are chosen because the system must not only deliver messages, but do so with bounded overhead and limited memory pressure.

---

# Current Result Interpretation

After fixing finality accounting:

- Finality is bounded at 100%
- SDN-DTN reduces control/forwarding overhead
- SDN-DTN reduces remaining buffer occupancy
- Latency depends strongly on whether fast-path neighbor discovery is ready

**Speaker notes:**  
The earlier SDN-DTN result had worse latency because SDN behaved like throttled epidemic routing. The fast-path change is intended to make SDN-DTN outperform epidemic on latency too.

---

# Main Contribution

The contribution is an application-aware SDN-DTN routing layer for offline payments.

Compared with epidemic routing:

- not all messages are treated equally
- quorum-critical traffic is prioritized
- stale finalized traffic is pruned
- replication is controlled by policy
- routing decisions align with payment finality

**Speaker notes:**  
The novelty is not just adding SDN terminology. The routing layer understands the payment protocol and optimizes around finality.

---

# Limitations

Current limitations:

- Small topology in current benchmark
- Mobility randomness affects contact opportunities
- SDN policies are mock-signed
- Neighbor selection is still simple
- One pending transfer per client limits workload realism

**Speaker notes:**  
These limitations are important to state clearly. They also define the next research steps.

---

# Next Steps

Planned improvements:

- larger topology and repeated trials
- confidence intervals over multiple seeds
- adaptive neighbor scoring using telemetry
- queue scheduling by deadline and message class
- stronger policy authentication
- support multiple pending transfers per client

**Speaker notes:**  
The next step is to make the evaluation statistically stronger and make the SDN policy adaptive, not only rule-based.

---

# Takeaway

Epidemic routing is robust but wasteful.

SDN-DTN improves opportunistic payment routing by using payment-aware control:

- faster quorum path
- lower overhead
- smaller buffers
- bounded finality accounting

**Speaker notes:**  
The main message is that opportunistic routing should not be blind flooding when the application has clear priorities. MeshPay can exploit those priorities to route payment-critical messages more efficiently.

