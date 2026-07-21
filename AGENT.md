# AGENT.md — AI Agent Guide for MeshPay

This file tells AI coding agents how to navigate, extend, and evaluate the MeshPay codebase.
Read this before making any changes.

---

## Project Overview

MeshPay is a research prototype for **resilient offline digital payments over wireless mesh networks**.
It runs on top of Mininet-WiFi, uses a DTN/Epidemic Routing daemon for store-carry-forward bundle
delivery, and implements a FastPay-style quorum payment protocol.

The primary research artifact is the **benchmark pipeline**: run → collect logs → generate figures.

---

## Repository Layout

```
meshpay/
├── attacks/              # Attack simulation modules (packet loss, targeted load)
│   ├── controller.py     # BenchmarkAttack: timed attack lifecycle manager
│   ├── packet_loss.py    # iptables-based random packet DROP via wmediumd bypass
│   └── targeted_load.py  # SyntheticLoadInjector: high-rate payment flood
│
├── dtn/                  # DTN routing daemons (one per Mininet node)
│   ├── router.py         # Core daemon: peer exchange, IPC delivery socket
│   ├── epidemic.py       # Epidemic (flood) forwarding policy
│   ├── prophet.py        # PRoPHET predictive forwarding policy
│   ├── spray_and_wait.py # Spray-and-Wait bounded-copy forwarding
│   ├── store.py          # BundleStore: file-based bundle persistence
│   └── bundle.py         # Bundle data model
│
├── meshpay/              # Python package: payment protocol and benchmark infra
│   ├── offline/nodes/    # Client and Authority station classes
│   ├── offline/crypto.py # Deterministic signing (prototype only)
│   ├── offline/quorum.py # Quorum threshold (⌊n/3⌋ + 1 of 4 authorities)
│   ├── benchmark/
│   │   ├── payment_metrics.py  # Payment event aggregation (phase cohorts, funnel)
│   │   ├── network_metrics.py  # Interface counter collection (network_raw.jsonl)
│   │   ├── traffic.py          # Open-loop payment traffic generator
│   │   └── report.py           # benchmark.json / benchmark.csv writer
│   └── mininet_cmd.py    # Thread-safe node.cmd() wrapper (safe_node_cmd)
│
├── examples/
│   └── meshpay_benchmark.py    # Main benchmark entry point (sudo required)
│
├── scripts/
│   ├── run_meshpay_benchmark_matrix.py  # Sweep runner: iterates loss / routing combos
│   ├── plot_loss_impact.py              # Figure generator for packet-loss benchmarks
│   └── plot_load_impact.py             # Figure generator for load-attack benchmarks
│
├── logs/benchmarks/      # Output directories (one per run)
├── figures/              # Saved figures (epidemic_loss/, prophet_loss/, saw_loss/)
└── attacks/README.md     # Quick-start benchmark commands
```

---

## Core Workflow

### 1 — Run a benchmark sweep

```bash
# Requires root. Replace <SEED> and <LABEL> as needed.
sudo python3 scripts/run_meshpay_benchmark_matrix.py \
  --clients 6 --authorities 4 --ranges 1000 \
  --payment-rate 10 --duration auto \
  --warmup 2 --settle-time 10 \
  --medium mesh \
  --routing epidemic \
  --attack packetloss \
  --attack-loss-probability 0,0.25,0.5,0.8 \
  --attack-tpre 10 --attack-tatk 20 --attack-tpost 10 \
  --attack-target-count auto \
  --execute \
  --seed <SEED> \
  --output-root "logs/benchmarks/<LABEL>_seed_<SEED>"
```

Duration is computed automatically as `warmup + attack_tpre + attack_tatk + attack_tpost + settle_time`.

### 2 — Regenerate figures

```bash
python3 scripts/plot_loss_impact.py \
  logs/benchmarks/<LABEL>_seed_<SEED>/summary.json \
  -o figures/<LABEL>/
```

No sudo needed. Reads only log files; does not touch the network.

### 3 — Inspect results

Key output files per run directory:

| File | Contents |
|------|----------|
| `benchmark.json` | Full metrics: phase cohorts, post-attack funnel, latency summaries |
| `benchmark.csv` | Flat CSV of benchmark.json scalar fields |
| `payment.log` | JSONL of every payment event (created, delivered, confirmed, accepted) |
| `network_raw.jsonl` | Per-node interface counter samples (TX/RX bytes/packets) |
| `attack_metadata.json` | iptables rule install/cleanup state, DROP counters |
| `summary.json` | Aggregate over all runs in a sweep (input to plot scripts) |
| `summary.csv` | Flat CSV of summary.json |

---

## Attack Simulation

### Packet Loss Attack

- Uses `iptables -A INPUT/OUTPUT -m statistic --mode random --probability P -j DROP`
- Rules are tagged with comment `meshpay-jamming` for idempotent cleanup
- Loopback (`! -i lo`, `! -o lo`) is excluded so DTN IPC socket still works
- Targets are selected by `select_targets(nodes, seed, count="auto")` → `max(0, total_nodes // 3)`
- Install and cleanup are both verified: `install_success` and `cleanup_success` in `attack_metadata.json`

### Targeted Load Attack

- Injects real MeshPay payments at high rate from client nodes
- Controlled by `SyntheticLoadInjector` in `attacks/targeted_load.py`
- Never pass `--attack-load-rate 0` (defaults to 200 TPS silently)

### Attack Timing

```
|<-- warmup -->|<-- tpre -->|<------ tatk ------>|<-- tpost -->|<-- settle -->|
               attack_configured                  attack_stopped
                              attack_started
```

- `tpre`: quiet period before attack (payment traffic runs)
- `tatk`: attack active period
- `tpost`: post-attack traffic window (iptables cleared, payments still flowing)
- `settle`: DTN drain window after traffic stops (no new payments)

---

## Payment Protocol (FastPay-style)

```
payment_created
  → payload_injected (TransferOrder → DTN → all authorities)
    → transfer_order_delivered_to_authority
      → authority_signed_transfer
        → payload_injected (SignedTransferOrder → DTN → sender)
          → signed_transfer_order_delivered_to_sender
            → confirmation_created (quorum reached)
              → payload_injected (ConfirmationOrder → DTN → recipient)
                → payment_accepted
```

Quorum threshold: `⌊4/3⌋ + 1 = 2` signatures from 4 authorities.

Events are written to `payment.log` as JSONL, one event per line, with a `time` field
(Unix timestamp) and `order_id` field for per-payment tracing.

---

## Metrics and Figures

### Key metrics for loss analysis

| Metric | Where | Meaning |
|--------|-------|---------|
| `cohort_before_confirmation_rate_percent` | `summary.json` | Rate for payments created *before* attack — the clean baseline |
| `cohort_during_confirmation_rate_percent` | `summary.json` | Rate during attack — shows direct impact |
| `cohort_after_confirmation_rate_percent` | `summary.json` | Rate for payments created *after* attack ends — shows recovery |
| `post_attack_funnel` | `benchmark.json` | Stage-by-stage counts where after-phase payments stop |
| `avg_time_to_quorum_ms` | `summary.json` | Avg TTQ over all confirmed payments |

### Figure outputs (`scripts/plot_loss_impact.py`)

| Figure | File | Interpretation |
|--------|------|----------------|
| 0a | `cohort_phase_rate_vs_loss.{pdf,png}` | Confirmation rate before/during/after per loss — **primary recovery figure** |
| 0b | `quorum_latency_by_phase.{pdf,png}` | TTQ per phase — shows latency explosion post-attack |
| 1 | `confirmation_rate_vs_loss.{pdf,png}` | Overall rate vs. loss (may be non-monotonic) |
| 2 | `acceptance_rate_vs_loss.{pdf,png}` | Acceptance rate vs. loss |
| 3 | `heatmap_confirmation_rate.{pdf,png}` | Routing × loss heatmap |
| 4 | `network_throughput_vs_loss.{pdf,png}` | Network TX/RX vs. loss |
| 5 | `network_throghput_impact.{pdf,png}` | Time series at 50% loss |
| 6 | `quorum_latency_vs_loss.{pdf,png}` | Avg TTQ vs. loss |
| 7 | `hop_count_vs_loss.{pdf,png}` | Avg hop count vs. loss |
| 8 | `bandwidth_phase_table.{pdf,png,csv,md}` | App goodput before/during/after |
| 9 | `network_phase_table.{pdf,png,csv,md}` | Network throughput before/during/after |
| 10 | `goodput_50_loss_table.{pdf,png,csv,md}` | Goodput at 50% loss specifically |
| 11 | `cohort_phase_table.{csv,md}` | Cohort table: created/confirmed/censored per phase |
| 12 | `attack_validation_table.{csv,md}` | iptables install/cleanup validation |
| 13 | `post_attack_funnel_table.{csv,md}` | Payment-stage funnel for after-phase payments |

---

## Code Conventions

- All benchmark code is `Python 3.10+` with `from __future__ import annotations`.
- Mininet nodes must be accessed via `safe_node_cmd(node, cmd)` from `meshpay/mininet_cmd.py`
  because `node.cmd()` is **not thread-safe**.
- Use `iptables -w` (wait flag) in all iptables invocations to prevent lock races.
- Events written to `payment.log` must include at least `{"event": "...", "time": <unix_ts>}`.
- `order_id` must be included in all payment events for per-transaction tracing.
- All attack metadata is serialized to `attack_metadata.json` by `controller.py`.

---

## Common Agent Tasks

### Add a new figure
1. Add a `fig_<name>(runs, output_dir)` function to `scripts/plot_loss_impact.py`.
2. Call it in `main()` with a print label.
3. Use `_save(fig, output_dir, "<name>")` to write PDF + PNG.
4. Use `_style_ax(ax)` for consistent grid/spine styling.

### Add a new payment event type
1. Emit the event in the relevant node class (`meshpay/offline/nodes/`).
2. Add it to `payment_metrics.py` collection if needed.
3. Update the `_derive_phase_funnel()` or `_payment_stage_funnel()` if it's a funnel stage.

### Add a new attack type
1. Create a new file in `attacks/` with a class implementing `start()`, `stop()`, and `cleanup()`.
2. Register it in `attacks/controller.py` `_run()` dispatch.
3. Add the `--attack` option value to `scripts/run_meshpay_benchmark_matrix.py`.

### Debug a failed confirmation
Check `payment.log` in order:
```
payment_created → payload_injected → transfer_order_delivered_to_authority
→ authority_signed_transfer → signed_transfer_order_delivered_to_sender
→ confirmation_created → payment_accepted
```
Missing events after `authority_signed_transfer` usually indicate a DTN routing problem
on the return path (signed response not making it back to the sender).

---

## Known Pitfalls

| Pitfall | Explanation |
|---------|-------------|
| Non-monotonic confirmation rate vs. loss | The *overall* rate is diluted by after-phase failures. Use `cohort_before_confirmation_rate_percent` for a clean signal. |
| `settle-time` too short | At 10 s settle after 20 s attack, post-attack TTQ (33–35 s) exceeds the remaining window. Extend `--settle-time` to ≥ 60 s for recovery experiments. |
| iptables rules persist | If a benchmark crashes, run `sudo iptables -F` on each node or `sudo mn -c`. |
| Epidemic flooding under load | High `--payment-rate` creates many bundle copies. Use `spray-and-wait` or `prophet` to bound replication. |
| Virtual account balance exhaustion | With 1500 accounts and rate 10 TPS for 160 s, 1600 payments are attempted. Ensure `--initial-balance` is large enough. |

---

## Environment Setup

```bash
# Clean lingering Mininet state before each run
sudo mn -c
sudo pkill -f epidemic.py || true
sudo pkill -f prophet.py || true

# Remove stale locks
sudo iptables -F 2>/dev/null || true

# Python path
export PYTHONPATH=/home/huydq/PHD2024-2027/meshpay
```

Benchmarks must run as root (`sudo`). Figure scripts do not require root.
