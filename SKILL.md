# SKILL.md — Domain Skills Reference for MeshPay

This file describes the key technical domains that appear throughout MeshPay.
Use this as a reference when reading code, debugging, or extending the system.

---

## 1. Delay-Tolerant Networking (DTN)

### What it is
DTN is a networking architecture designed for environments with **intermittent connectivity**,
long link delays, or frequent link failures. MeshPay uses a **store-carry-forward** model:
nodes buffer bundles and forward them opportunistically when a contact window opens.

### Key concepts

| Concept | MeshPay Implementation |
|---------|------------------------|
| Bundle | `dtn/bundle.py`: payload + destination + metadata |
| Store | `dtn/store.py`: file-based persistence, indexed by bundle ID |
| Exchange | `dtn/router.py`: peer contact → summary vector → missing bundles transferred |
| Delivery | `dtn/router.py`: IPC socket write to localhost:46666 when destination = self |
| Routing policy | Determines *which* bundles to forward to *which* peers |

### Routing protocols

| Protocol | File | Behaviour |
|----------|------|-----------|
| Epidemic | `dtn/epidemic.py` | Forward everything to everyone (maximum delivery, high overhead) |
| PRoPHET | `dtn/prophet.py` | Forward based on delivery predictability (P-values updated on contact) |
| Spray-and-Wait | `dtn/spray_and_wait.py` | Spray L copies, then wait until direct contact with destination |

### IPC delivery socket
The DTN router binds `127.0.0.1:46666` per node. When a bundle arrives for this node's
address, the router writes the payload to the socket. The MeshPay payment node (client or
authority) reads from this socket. Loopback (`lo`) is **never** blocked by the packet-loss
attack so this path always works.

---

## 2. MeshPay Payment Protocol (FastPay-style)

### Overview
MeshPay adapts the FastPay Byzantine fault-tolerant payment protocol for offline/mesh use.
There is no P2P connection between client and authority — all messages go through the DTN layer.

### Transaction lifecycle

```
Step 1  — Sender creates TransferOrder (signed with sender's key)
Step 2  — TransferOrder injected into DTN, addressed to ALL authorities
Step 3  — Each authority validates sequence number, signs, returns SignedTransferOrder to sender
Step 4  — Sender collects ≥ ⌊f+1⌋ signatures  (with 4 authorities, f=1, quorum=2)
Step 5  — Sender creates ConfirmationOrder, injects into DTN addressed to recipient + authorities
Step 6  — Recipient validates ConfirmationOrder, updates balance → payment_accepted
Step 7  — Authorities update their off-chain account state
```

### Quorum
- **f**: maximum number of Byzantine (faulty) authorities tolerated
- With 4 authorities: f = 1, quorum = ⌊4/3⌋ + 1 = **2 signatures**
- Implemented in `meshpay/offline/quorum.py`

### Virtual accounts
Physical Mininet-WiFi stations (sta1 … sta6) each host N virtual accounts (sta1/u00001, etc.).
A single `payment.log` collects events from all virtual accounts on all physical stations.
The benchmark traffic generator (`meshpay/benchmark/traffic.py`) picks random sender/recipient
pairs from the virtual account pool.

---

## 3. Mininet-WiFi Emulation

### What it does
Mininet-WiFi creates a **software-defined wireless topology** inside Linux network namespaces.
Each station (sta1, sta2, …, auth1, auth2, …) has its own network namespace with a virtual
wireless interface.

### Wireless modes

| Mode | Flag | Description |
|------|------|-------------|
| 802.11s mesh | `--medium mesh` | All nodes form a multi-hop mesh; routing done in kernel |
| 802.11 ad-hoc | `--medium adhoc` | IBSS mode; single-hop broadcast |

With `--ranges 1000`, all nodes are within range — the topology is **fully connected**.
Packet loss is applied by iptables, not by wmediumd signal attenuation.

### Key Mininet-WiFi constraints
- Operations on node objects (e.g., `node.cmd()`) are **not thread-safe**.
  Always use `safe_node_cmd(node, cmd)` from `meshpay/mininet_cmd.py`.
- The CLI (`Mininet-WiFi>`) is blocked during benchmarks. Use log files instead.
- Node cleanup (`sudo mn -c`) must be run between benchmark runs.

---

## 4. Packet-Loss Attack

### Mechanism
`attacks/packet_loss.py` installs `iptables` DROP rules in the INPUT and OUTPUT chains
of selected target nodes:

```bash
iptables -w -A INPUT  ! -i lo -m statistic --mode random --probability P \
         -m comment --comment meshpay-jamming -j DROP

iptables -w -A OUTPUT ! -o lo -m statistic --mode random --probability P \
         -m comment --comment meshpay-jamming -j DROP
```

The `-w` flag prevents race conditions when multiple rules are installed concurrently.
The `! -i lo / ! -o lo` exclusion ensures DTN IPC delivery (loopback) always works.

### Target selection
```python
targets = select_targets(nodes=all_nodes, seed=seed, count="auto")
# "auto" → max(0, total_nodes // 3)
# With 10 nodes (6 clients + 4 authorities): 10 // 3 = 3 targets
```

### Cleanup and validation
After `tatk` seconds, `_cleanup_packet_loss_rules()` in `controller.py`:
1. Records DROP counters before cleanup (`packet_loss_drop_counters`)
2. Runs `_cleanup_command()` — loops until all `meshpay-jamming` rules are gone
3. Verifies `remaining_rules == 0` and records `cleanup_success`
4. Emits `packet_loss_rules_removed` event to `payment.log`

---

## 5. Benchmark Metrics and Phases

### Phase windows
The benchmark divides time into three phases relative to attack events:

| Phase | Window |
|-------|--------|
| before | `[attack_start - tpre, attack_start)` |
| during | `[attack_start, attack_stop)` |
| after | `[attack_stop, attack_stop + tpost)` |

Payments are assigned to a phase by their `payment_created` timestamp.

### Payment stage funnel
For the *after* phase, `post_attack_funnel` in `benchmark.json` tracks how many
created orders reached each stage:

```
payment_created
  → transfer_order_delivered_to_authority   (≥1 auth received it)
    → authority_signed_transfer             (≥1 auth signed it)
      → signed_transfer_order_delivered_to_sender  (signed response reached sender)
        → confirmation_created              (quorum achieved)
          → payment_accepted               (recipient accepted)
```

A drop at `signed_transfer_order_delivered_to_sender` means the DTN return path is broken
(e.g., settle window too short, or routing not recovering fast enough).

### Quorum latency by phase
`time_to_quorum_ms` is computed per cohort (before/during/after). Post-attack TTQ is typically
3–5× higher than pre-attack TTQ because:
1. Bundles queued during the attack flood through simultaneously when rules are removed
2. The DTN store processes them in FIFO order, creating head-of-line blocking
3. The remaining settle window (often only 10 s) is insufficient for 33–35 s TTQ

### Key insight: why overall confirmation rate is non-monotonic with loss
At 0% loss, all 1500 payments compete simultaneously for the same DTN bandwidth.
At 80% loss, 60 s of traffic is suppressed, so fewer payments are in-flight after the attack,
and pre-attack payments drain the pipeline more cleanly. The **before-phase confirmation rate**
(95–100% regardless of loss) is the correct uncontaminated baseline.

---

## 6. Figure Interpretation Guide

### `cohort_phase_rate_vs_loss.png` (Figure 0a)
**What to look for**: Before ≈ 100% (attack rules don't affect pre-created payments),
During drops with loss, After ≈ 0% (not enough settle time for recovery).
This is the **primary evidence of non-recovery**.

### `quorum_latency_by_phase.png` (Figure 0b)
**What to look for**: After-phase TTQ (red bars) far exceeds the settle-time dashed line.
This directly explains why after-phase payments can't complete within the benchmark window.

### `post_attack_funnel_table.md`
**What to look for**: The stage where the funnel drops off is the bottleneck.
- Drop at `transfer_order_delivered_to_authority` → DTN routing not recovering
- Drop at `signed_transfer_order_delivered_to_sender` → Return path congested
- Drop at `confirmation_created` → Quorum latency exceeds settle window

### `attack_validation_table.md`
**What to look for**: `Install=True`, `Remaining Rules After Cleanup=0`, `Cleanup=True`.
If cleanup fails, the attack is still active during the post-attack window — explaining
poor recovery without the DTN being the cause.

### `bandwidth_phase_table.md`
**What to look for**: TX after ≈ TX before (injection recovering), but RX after < RX before
(delivery still suppressed). If both recover, the DTN is fine and the issue is payment-level.

---

## 7. Log File Reference

### `payment.log`
JSONL event log. Each line is a JSON object with at minimum:
```json
{"event": "...", "time": 1751234567.123, "order_id": "uuid-..."}
```

Key events:
| Event | Emitted by | Meaning |
|-------|-----------|---------|
| `payment_created` | Client | New payment initiated |
| `payload_injected` | Client/Authority | Bundle injected into DTN store |
| `payment_payload_delivered` | DTN IPC handler | Bundle arrived at destination |
| `transfer_order_delivered_to_authority` | Authority | TO received and validated |
| `authority_signed_transfer` | Authority | SignedTO created |
| `confirmation_created` | Client | Quorum collected |
| `payment_accepted` | Recipient client | ConfirmationOrder validated |
| `attack_started` | BenchmarkAttack | iptables rules installed |
| `attack_stopped` | BenchmarkAttack | iptables rules removed |
| `network_stats` | Benchmark runner | Interface TX/RX counter snapshot |

### `network_raw.jsonl`
Per-node interface counter samples:
```json
{"time": 1751234567.0, "node": "sta1", "tx_bytes": 123456, "rx_bytes": 654321, ...}
```
Used to compute network-layer throughput independently of payment events.

### `benchmark.json`
Full structured output of `collect_payment_metrics()`. Contains:
- `summary`: scalar metrics (rates, counts, TPS)
- `latency_ms`: TTQ and acceptance latency distributions
- `phase_cohorts`: per-phase cohort analysis
- `post_attack_funnel`: after-phase payment stage funnel
- `hop_count`: bundle hop count distribution
- `payload_type_counts`: TX/RX payload type breakdown

### `attack_metadata.json`
Written by `BenchmarkAttack.write_metadata()`. Contains:
- `packet_loss_installation`: rule install success and counts
- `packet_loss_drop_counters`: DROP packet/byte counters before cleanup
- `packet_loss_cleanup`: cleanup success and remaining rules

---

## 8. Testing and Validation

### Quick sanity check (no sudo needed for analysis)
```bash
python3 -c "
import json
runs = json.load(open('logs/benchmarks/epidemic_loss_seed_21/summary.json'))
for r in runs:
    loss = r.get('param.attack_loss_probability', 0)
    conf = r.get('payment_confirmation_rate_percent', 0)
    after = r.get('cohort_after_confirmation_rate_percent', 0)
    print(f'Loss={loss:.2f}: overall={conf:.1f}%, after={after:.1f}%')
"
```

### Verify attack cleanup
```bash
python3 -c "
import json
for run_dir in ['002_c6_a4_r1000_rate10_mmesh_rtEpi_attPL_loss0p25']:
    meta = json.load(open(f'logs/benchmarks/epidemic_loss_seed_21/{run_dir}/attack_metadata.json'))
    cleanup = meta.get('packet_loss_cleanup', {})
    print(f'Cleanup success: {cleanup.get(\"cleanup_success\")}, remaining={cleanup.get(\"remaining_rules\")}')
"
```

### Run figure generation (no sudo)
```bash
python3 scripts/plot_loss_impact.py \
  logs/benchmarks/epidemic_loss_seed_21/summary.json \
  -o figures/epidemic_loss/
```

---

## 9. Research Context

**Paper**: "MeshPay: Resilient Offline Payment with Wireless Mesh Network"  
Quang Huy Do, Sara Tucci-Piergiovanni, Justice Owusu Agyemang, Sami Souihi — WCNC 2026

**Research question**: How does packet loss (network attack) degrade payment confirmation
rates and latency in a DTN-based offline payment system?

**Key experimental variables**:
- Packet loss probability: 0%, 25%, 50%, 80%
- Routing protocol: Epidemic, Spray-and-Wait, PRoPHET
- Payment rate: 10 TPS (benchmark standard)
- Attack targets: ≈30% of nodes (random subset, seeded for reproducibility)

**Primary claim**: Epidemic routing under packet-loss attack shows graceful degradation
during the attack but fails to recover within the settle window. Longer settle times
or larger DTN buffer capacity would improve post-attack recovery.
