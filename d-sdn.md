# Flash-Mesh Integration Plan: FastPay-Inspired D-SDN for Offline Mesh Payments

## 1) Updated Architecture Direction (Important Shift)

MeshPay should now be integrated around a **FastPay-inspired broadcast model** (Byzantine Consistent Broadcast, BCB), not a DAG-ordering-first model.

- **Old mindset:** global ordering of most events (consensus-heavy).
- **New mindset:** per-account locking/finality through quorum signatures (broadcast-heavy).
- **Result:** faster retail settlement in unstable wireless mesh conditions (targeting ~1 RTT finality for simple payments).

In this Flash-Mesh design, **Merchant Nodes** act as both:
1. **FastPay Authorities** (state validation + signatures), and
2. **D-SDN Anchors/Controllers** (authoritative path + traffic priority).

---

## 2) Current Project Status vs Flash-Mesh Requirements

### What you already have

1. **Offline mesh payment framing and authority quorum concepts** in docs and codebase.
2. **P4/ONOS control hooks** in `mn_wifi/bmv2.py`, suitable for SDN control-plane prototyping.
3. **Weighted committee/quorum helpers** in `mn_wifi/committee.py`, reusable for controller authorization and certificate threshold checks.

### What must change for Flash-Mesh

1. Replace roadmap language implying DAG-policy synchronization as the primary execution model.
2. Add explicit **BCB payment packet lifecycle**: `Lock -> Verify -> Vote -> Certificate -> Execute`.
3. Formalize a **2-tier architecture**:
   - Tier 1: Merchant Anchor (`FastPay Authority + SDN Controller`)
   - Tier 2: User Phones (`Forwarder + Wallet Client`)
4. Add **handoff procedure** for partition/mobility: account-shard transfer between merchants.
5. Add **in-network nonce filtering** and **TEE-attested signing paths** as first-class safety controls.

---

## 3) First Step You Should Do (Immediate)

## Build a Merchant-Anchor MVP (single-store / single-village scope)

This is the first implementation step to de-risk everything else.

1. **Create a Merchant Anchor service** combining:
   - `FastPayAuthority` API (`verify_transfer`, `sign_vote`, `verify_certificate`, `apply_certificate`)
   - `SDNController` API (`collect_link_stats`, `install_priority_flows`, `fast_reroute`)
2. **Define payment-critical traffic class** for BCB packets:
   - vote requests,
   - signed votes,
   - final certificates.
3. **Install deterministic priority rules** (P4/OpenFlow):
   - `CLASS_0_FASTPAY_BCB` (strict priority),
   - `CLASS_1_PAYMENT_DATA`,
   - `CLASS_2_BEST_EFFORT`.
4. **Implement certificate policy** for local finality:
   - `2f+1` signatures in Byzantine mode, or
   - single authoritative merchant mode in trusted 2-tier deployments.
5. **Track baseline KPIs** (before/after priority slicing):
   - payment finality P50/P95,
   - certificate assembly success rate,
   - packet delay/loss for BCB class under congestion.

Why this is first:
- It directly validates the new FastPay-first architecture.
- It proves the SDN value proposition (guaranteed fast path for broadcast/finality packets).
- It enables later decentralization without redesigning packet classes or authority APIs.

---

## 4) FastPay-Inspired Settlement Workflow (Operational Spec)

For simple payments (A -> B), implement this exact path:

1. **Lock (Client):** user signs transfer with `sender, recipient, amount, nonce, epoch`.
2. **Verify (Merchant Authority):** check signature, nonce freshness, balance sufficiency, account shard ownership.
3. **Vote (Authority):** return signed vote promise.
4. **Certify (Client):** aggregate quorum signatures into a certificate.
5. **Execute (Merchant):** goods release + ledger state update only after certificate validation.

Design principle: **No global ordering dependency for independent account updates.**

---

## 5) 2-Tier D-SDN Architecture (Flash-Mesh)

## Tier 1: Merchant Anchor (Controller + Authority)

- Maintains local topology map and payment path quality.
- Pushes flow rules to nearby forwarding phones.
- Hosts shard state for currently served accounts.
- Performs transfer checks and vote signing.

## Tier 2: User Mesh Phones (Forwarder + Wallet)

- Run lightweight forwarding behavior (follow pushed flow rules).
- Keep keys in TEE-backed wallet where possible.
- Aggregate authority signatures into payment certificates.

## Fast path requirement

A dedicated **reserved path/slice** must exist from client to merchant for BCB packets to maintain 1-RTT behavior under congestion.

---

## 6) 30/60/90-Day Execution Plan (Rebased for Flash-Mesh)

### Day 0–30: Merchant-Anchor MVP
- Implement APIs for `FastPayAuthority` and `SDNController`.
- Add BCB packet classification and strict-priority queues.
- Validate 1-RTT settlement path in small topology.
- Add anti-replay window (nonce + epoch + TTL).

**Exit criteria:** payment BCB traffic remains low-latency during induced background load.

### Day 31–60: Multi-merchant mobility + shard handoff
- Add merchant-to-merchant account shard transfer protocol.
- Trigger handoff on mobility/link-break events.
- Add signed handoff receipts and deterministic ownership transitions.
- Add fallback if old merchant is unreachable (escrowed pending state + timeout).

**Exit criteria:** user can move from Merchant A coverage to Merchant B and continue spending with bounded interruption.

### Day 61–90: Decentralization and adversarial hardening
- Add threshold-approved merchant controller election for sparse regions.
- Add route-poison and forwarding anomaly detection.
- Add FRL as advisory signal only (never bypasses safety checks).
- Add periodic key rotation and remote attestation checks for authority TEEs.

**Exit criteria:** no unauthorized control-plane writes and stable liveness under failure/attack drills.

---

## 7) Attack and Failure Plan (Flash-Mesh Specific)

| Scenario | Symptom | Risk | Detection | Mitigation | Target |
|---|---|---|---|---|---|
| **Double-spend attempt (same nonce)** | Duplicate transfer broadcast | Fraud / inconsistent balances | Nonce cache hit + bloom filter match | P4 drop at ingress + authority reject + sender penalty | Drop in-line |
| **Replay certificate attack** | Old cert resubmitted | Duplicate execution | Cert epoch/TTL mismatch | Monotonic nonce, expiry checks, replay cache | Immediate reject |
| **Rogue merchant controller** | Unauthorized flow mods | Traffic hijack/censorship | Control signature invalid / attestation mismatch | Accept only threshold-signed policy bundles + cert pinning | Block <1s |
| **Blackhole forwarder phone** | Silent packet sink | Finality delay | Probe ACK gap, path success collapse | Fast reroute + temporary quarantine score | Reroute <500ms |
| **Wormhole path spoof** | Unrealistic low-hop/low-RTT path | Selective drop/intercept | RTT-distance plausibility checks | Multi-metric routing constraints (RTT+ETX+hop sanity) | Filter next cycle |
| **Sybil wallet swarm** | Many fake wallet IDs | Resource exhaustion / fake traffic | Identity rate anomaly + enrollment checks | Admission throttling + stake/credential gates | Contain <1 epoch |
| **Jamming near merchant** | High retries, RSSI/noise spikes | BCB starvation | PHY anomaly + control packet loss | Reserve control slice + channel hop + alt relay path | Degraded but live |
| **TEE integrity failure (merchant)** | Inconsistent signing behavior | Invalid state transitions | Remote attestation drift + behavioral anomaly | Revoke merchant signing key + rotate shard authority | Contain <1 epoch |
| **Partition between merchants** | Account handoff unavailable | Spend interruption across zones | Missing shard-transfer acks | Local-only mode + queued handoff proof + delayed activation | Safe resume after heal |
| **Merchant crash** | Anchor unreachable | Service outage | Heartbeat timeout | Hot standby merchant + last known shard snapshot | Failover <2s |

---

## 8) Safety and Liveness Rules to Enforce in Code

1. **Execute-on-certificate only:** no merchant should release goods on unsigned or partial votes.
2. **Nonce monotonicity per account shard:** strict one-time spend semantics.
3. **Control-plane writes require cryptographic authorization:** no unsigned flow-mod accepted.
4. **Priority guarantees are mandatory for BCB class:** never let best-effort traffic starve votes/certificates.
5. **Partition-safe spending scope:** local spend allowed; cross-merchant settlement waits for signed shard handoff/merge proof.

---

## 9) Minimal KPI Set for Phase-1 Success

- **Settlement KPIs:** finality P50/P95, certificate assembly time, certificate failure rate.
- **Network KPIs:** BCB packet P95 delay, BCB loss rate, queue occupancy by class.
- **Safety KPIs:** replay drops, duplicate nonce drops, unauthorized flow-mod attempts blocked.
- **Mobility KPIs:** shard handoff completion time, payment interruption window during merchant switch.

Phase-1 success definition:
**Maintain sub-second retail finality for BCB payments during congestion without increasing replay/double-spend incidents.**

---

## 10) Practical Next Commands (Implementation Checklist)

1. Define `TransferVote` and `TransferCertificate` schemas (include `nonce`, `epoch`, `ttl`, `authority_sig`).
2. Add `CLASS_0_FASTPAY_BCB` classification fields to packet headers / DSCP or metadata.
3. Implement merchant-side `verify->vote` endpoint and client-side certificate aggregator.
4. Add P4/OpenFlow rules for strict-priority BCB queue.
5. Implement replay/nonce cache in both P4 data plane and authority app logic.
6. Run three tests: congestion, mobility handoff (A->B), and replay attack injection.

This sequence aligns MeshPay with the Flash-Mesh principle: **from ordering-heavy consensus to lock-and-certificate broadcast finality optimized by merchant-anchored SDN.**
