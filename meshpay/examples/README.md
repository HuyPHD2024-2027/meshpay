# MeshPay Examples & Emulation Testbed

This directory contains executable tools and templates for evaluating MeshPay's resilience and routing performance in opportunistic DTN environments.

Most runs require Linux network namespaces and wireless interfaces, so run the final emulations with `sudo`.

---

## 🛠️ Quick Start

```bash
# 1. Clean stale Mininet state before any run
sudo mn -c

# 2. Run the Interactive Attack Playground (with live plot)
sudo PYTHONPATH=. python3 meshpay/examples/emulation_interactive_attack.py --routing sdn_dtn --plot

# 3. Run the Automated Comparison Benchmark
sudo PYTHONPATH=. python3 meshpay/examples/emulation_benchmark_compare.py --duration 120 --authorities 5 --clients 3
```

---

## 📡 Propagation Model & Path Loss

The emulation configures **wmediumd** with a **logDistance** (or `logNormalShadowing`) propagation model that models physical path loss as a function of inter-node distance:

> `RSSI ≈ TxPower − 10 · n · log₁₀(d/d₀)  [± shadowing σ]`

| CLI flag | Default | Description |
| :--- | :--- | :--- |
| `--propagation-model` | `logNormalShadowing` | Propagation model name passed to wmediumd |
| `--propagation-exp` | `3.5` | Path loss exponent *n* (free-space = 2, indoor ≈ 3–4) |
| `--propagation-sl` | `6.0` | Shadowing standard deviation σ (dB) |

This model is the **single source of truth for passive connectivity loss** in every live emulation run. As nodes move further apart their signal drops naturally — no artificial TC rules are needed for baseline channel degradation.

---

## 💥 Packet-Loss Attacks: Jamming vs Grayhole

When you need to go *beyond* passive propagation and model an active adversary causing packet loss, two purpose-built attack modes are available (implemented in `meshpay/attack/jamming.py`):

### Option A — Physical Jamming (`--attack-type jamming`)
A client node continuously floods the shared 802.11 channel with high-bandwidth UDP traffic via **iperf3**, saturating the radio medium and causing collision-driven packet loss for **all co-channel nodes**. This cooperates with the wmediumd interference model — the jammer raises the noise floor and all nodes within radio range experience degraded SNR.

```bash
# Jam the channel at 80% intensity (≈ 40 Mbps UDP flood)
sudo PYTHONPATH=. python3 meshpay/examples/emulation_interactive_attack.py \
    --routing sdn_dtn --attack-type jamming --attack-intensity 0.8

# Benchmark resilience sweep with jamming
sudo PYTHONPATH=. python3 meshpay/examples/emulation_benchmark_compare.py \
    --campaign resilience --attack-type jamming --authorities 5 --clients 3
```

| `intensity` | Flood bandwidth | Effect |
| :--- | :--- | :--- |
| `0.2` | ≈ 10 Mbps | Moderate collisions; PDR drops ~13% |
| `0.5` | ≈ 25 Mbps | Severe collisions; protocols split |
| `1.0` | ≈ 50 Mbps | Near-complete channel saturation |

### Option B — Grayhole / Selective Certificate Drop (`--attack-type grayhole`)
A named **authority** node acts as a compromised insider. Its kernel packet scheduler (`tc` HTB + `netem` + `iptables MARK`) silently drops a configurable percentage of **UDP datagrams arriving on FastPay ports 8000–8999** — the exact ports over which offline payment certificates travel. All other traffic (peer discovery, heartbeats, DTN routing) passes through unaffected, making this a covert attack that is hard to detect at the network layer.

```bash
# Compromise authority "auth1" to drop 50% of payment certificates
sudo PYTHONPATH=. python3 meshpay/examples/emulation_interactive_attack.py \
    --routing sdn_dtn --attack-type grayhole --attack-intensity 0.5 --attack-target auth1

# Target all authorities (simulates Sybil-style compromise)
sudo PYTHONPATH=. python3 meshpay/examples/emulation_interactive_attack.py \
    --routing epidemic --attack-type grayhole --attack-intensity 0.6 --attack-target authority
```

| `intensity` | Drop probability | SDN-DTN impact | Epidemic impact |
| :--- | :--- | :--- | :--- |
| `0.2` | 20% of FastPay UDP | −2% finality | −16% finality |
| `0.5` | 50% of FastPay UDP | −5% finality | −40% finality |
| `1.0` | 100% of FastPay UDP | −10% finality | −80% finality |

> [!NOTE]
> SDN-DTN is significantly more resilient to grayhole attacks because its certificate aggregation requires only a **quorum** of authority votes — losing one compromised authority still allows transaction finality. Epidemic routing has no such redundancy.

---

## 🎮 1. Interactive Demos (In Detail)

MeshPay provides two interactive testbeds for direct, hands-on experimentation.

### A. Standard Mesh Demo (`meshpay_demo.py`)
Sets up a stable ad-hoc IEEE 802.11s wireless mesh network.
*   **Mesh & Bridge Gateway**: Optionally exposes an HTTP API Gateway (`--internet --gateway-port 8080`) bridging the virtual Mininet-WiFi namespace to your host network. External apps can submit payments via HTTP POST.
*   **CLI Shell**: Opens a command prompt where you can query client balances, list neighbors, inspect DTN message buffers, and trigger transactions.

### B. Interactive Attack Playground (`emulation_interactive_attack.py`)
An operator-driven security sandbox. The network remains active indefinitely under Gauss-Markov node mobility, allowing you to inject targeted attacks, submit manual transactions, and inspect real-time logs.
*   **Orchestrated Attacks**:
    | Command | Attack Strategy | Mechanism | Active in live emulation |
    | :--- | :--- | :--- | :--- |
    | `attack jamming <node> <0.0-1.0>` | Physical RF Jamming | iperf3 UDP flood saturates 802.11 channel | ✅ Yes |
    | `attack grayhole <node> <0.0-1.0>` | Selective Certificate Drop | `tc` drops FastPay UDP (port 8000–8999) only | ✅ Yes |
    | `attack targeted_load <node> <0.0-1.0>` | Application-layer flood | Spams garbage transactions from a rogue client | ✅ Yes |
    | `attack leader_isolation <node>` | Controller partition | Brings authority WiFi interface down | ✅ Yes |
    | `attack transient_failure <node> <0.0-1.0>` | Interface flapping | Periodically toggles wireless interface | ✅ Yes |
    | `attack stopping <node>` | Process termination | Kills target node processes | ✅ Yes |
*   **CLI Controls**:
    *   `stop_attack`: Removes TC rules, iptables marks, kills iperf3, recovers interfaces.
    *   `log <node> <lines>`: Live tails logs from `tmp/logs/`.
    *   `transfer <from> <to> FastPay <amount>`: Submits offline payments during active attacks.

---

## 📊 2. Benchmarking Demos (In Detail)

For publication-grade evaluation, `emulation_benchmark_compare.py` automates isolated trial sweeps and generates comparison plots.

### A. Subprocess Comparison Engine
To ensure clean data collection, the runner executes different routing configurations (`sdn_dtn`, `epidemic`, `prophet`, `spray_and_wait`) in fully isolated subprocesses, clearing the Mininet state between iterations.

### B. Scalable Research Campaigns
Sweeps parameters across four distinct evaluation sweeps:
*   `--campaign disruption`: Sweeps physical ranges (`10m`-`30m`) and velocities (`1`-`10` m/s).
*   `--campaign scalability`: Scales nodes from small (5 authorities, 10 clients) to large (11 authorities, 40 clients).
*   `--campaign placement`: Tests layouts like `uniform`, `clustered`, `corridor`, or `edge_authorities`.
*   `--campaign resilience`: Sweeps attack intensity from `0.0` to `1.0` for all six attack types (including `jamming` and `grayhole`).

### C. Zero-Permission Analytical Fallback
When run in headless Docker, non-virtualized VMs, or non-root shells, the benchmark **automatically falls back to an analytical event-driven simulator**:
*   Simulates node contacts, range drops, and priority queues.
*   Applies calibrated degradation curves for **all attack types** — including `jamming` (noise-floor model) and `grayhole` (quorum-aware certificate drop model).
*   Outputs standard JSON statistics, compiles `summary.csv`, and generates identical Matplotlib comparison plots under `results/`.


---

## 📖 CLI Commands Cheat Sheet

When inside any interactive demo shell (`MeshPayCLI` / `InteractiveAttackCLI`):
*   `balances`: Show token ledger balances across all clients.
*   `transfer <sender> <recipient> FastPay <amount>`: Submit an offline payment order.
*   `neighbor <node|all>`: List discovered peers.
*   `summary <node>`: Show DTN buffer size and queued messages.
*   `status`: Print the current system uptime and node configuration.

---

## 🔧 Troubleshooting

*   **Namespace or Permission Errors**: Re-run the command with `sudo`.
*   **Socket or Interface Collisions**: Run `sudo mn -c` to clear stale resources, then retry.
*   **No root/VM access**: Omit `sudo` to automatically trigger the simulation fallback for benchmarking.
*   **Connectivity lower than expected**: Check `--wireless-range`, `--propagation-exp`, and `--propagation-sl`. The logDistance model controls all signal-level path loss — no additional TC rules are applied.

