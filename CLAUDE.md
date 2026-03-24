# MeshPay — Agent Guidelines

## Project Overview

MeshPay is a **PhD research prototype** for **offline payment emulation over opportunistic IEEE 802.11s wireless mesh networks** with **Layer-1 blockchain settlement**. It extends [Mininet-WiFi](https://github.com/intrig-unicamp/mininet-wifi) with a FastPay-inspired quorum protocol for sub-second payment finality in infrastructure-less environments.

**Key research concepts:**
- **Byzantine Consistent Broadcast (BCB):** Client-driven quorum collection for 1-RTT finality
- **2-Tier D-SDN:** Merchant Anchors (controller + validator) + User Phones (forwarder + wallet)
- **Mesh relay:** TTL-constrained multi-hop forwarding with hop-path tracking

---

## Repository Structure

```
meshpay/                    # Root (extends Mininet-WiFi)
├── meshpay/                # MeshPay application layer
│   ├── nodes/
│   │   ├── authority.py    # WiFiAuthority — validator node (FastPay authority)
│   │   ├── client.py       # Client — user wallet node
│   │   └── client1.py      # Client1 — buffered variant of Client
│   ├── transport/
│   │   ├── tcp.py          # TCPTransport — primary message transport
│   │   ├── udp.py          # UDPTransport
│   │   └── wifiDirect.py   # WiFiDirectTransport
│   ├── types.py            # Domain types (Address, TransferOrder, etc.)
│   ├── messages.py         # Message DTOs (TransferRequest, PeerDiscovery, etc.)
│   ├── cli_fastpay.py      # Interactive MeshPay CLI
│   ├── examples/
│   │   └── meshpay_demo.py # Main demo script
│   ├── api/                # HTTP bridge/gateway
│   ├── controller/         # QoS, link stats, fallback
│   └── logger/             # Colored per-node logging
├── mn_wifi/                # Mininet-WiFi fork (network emulation)
│   ├── node.py             # Station / AP base classes
│   ├── net.py              # Mininet_wifi network builder
│   ├── link.py             # Wireless link / mesh interfaces
│   ├── mobility.py         # Mobility models
│   ├── propagationModels.py# Signal propagation
│   ├── services/
│   │   ├── core/config.py  # App settings (discovery interval, tokens, RPC)
│   │   └── blockchain_client.py # On-chain settlement client
│   └── committee.py        # Weighted quorum helpers
├── OFFLINE_PAYMENT_PLAN.md # Architecture: 2-Tier D-SDN design
├── d-sdn.md                # Flash-Mesh integration plan
├── Makefile                # Build/install targets
└── setup.py                # Python package setup
```

---

## Build & Run

### Install (requires sudo — installs system-wide into Mininet-WiFi)

```bash
sudo make clean install
```

This runs `python setup.py install` which copies `meshpay/` and `mn_wifi/` into `/usr/local/lib/python3.12/dist-packages/`.

> **IMPORTANT:** After ANY code change, you MUST re-run `sudo make clean install` before testing. The demo imports from the installed package, not the source tree.

### Run Demo

```bash
sudo python3 meshpay/examples/meshpay_demo.py --mobility --clients 3 --plot
```

Key flags:
- `--mobility` — Enable random waypoint mobility
- `--clients N` — Number of user nodes (default 3)
- `--authorities N` — Number of authority nodes (default 3)
- `--plot` — Show live topology plot
- `--internet` — Enable gateway to real internet (for L1 settlement)

### Clean up after crash

```bash
sudo mn -c
```

---

## Architecture Constraints

### Threading Model
- **Discovery loops** run in background `daemon` threads (one broadcast, one listen per node)
- **Message handler** runs in a background thread
- **CLI** runs on the main thread via Mininet's `CLI` class

> **CRITICAL:** Never use `self.cmd()` from background threads — it causes `RuntimeError: concurrent poll() invocation`. Use `self.popen()` instead, which spawns a separate process.

### Neighbor Discovery
- Uses **UDP broadcast** on `DISCOVERY_PORT` (default 5353)
- Broadcasts bypass WiFi range in Mininet-WiFi (Linux delivers to all listeners on same L2)
- Must gate `add_neighbor()` with `_is_reachable()` (ping via `self.popen()`) to validate actual wireless link connectivity
- Stale neighbors are pruned via `NEIGHBOR_TIMEOUT`

### Transport
- Primary transport is **TCP** (`TCPTransport`)
- Each node listens on its own `(ip, port)` — authorities on `800x`, clients on `900x`
- Messages are JSON-serialized (`Message.to_json()` / `Message.from_json()`)

### Configuration
- Settings in `mn_wifi/services/core/config.py`
- Key tunables: `DISCOVERY_PORT`, `DISCOVERY_INTERVAL`, `NEIGHBOR_TIMEOUT`
- Token contracts configured via environment variables or defaults

---

## Code Conventions

### Node Classes
All node types inherit from `mn_wifi.node.Station`:
- `WiFiAuthority` (in `authority.py`) — validator, signs transfer votes
- `Client` (in `client.py`) — user wallet, collects quorum certificates
- `Client1` (in `client1.py`) — buffered variant with batch processing

When modifying node behavior (discovery, reachability, transport), **apply changes to all three files** — they share the same patterns but are not class-hierarchy linked.

### Message Protocol
- `MessageType` enum: `TRANSFER_REQUEST`, `TRANSFER_RESPONSE`, `CONFIRMATION_REQUEST`, `PEER_DISCOVERY`, `MESH_RELAY`, etc.
- All messages wrapped in `Message` envelope with `sender`, `recipient`, `timestamp`, `payload`
- Payload-specific DTOs: `TransferRequestMessage`, `PeerDiscoveryMessage`, `MeshRelayMessage`, etc.

### Logging
- Per-node colored loggers: `AuthorityLogger`, `ClientLogger`
- Levels: `info` for important events, `debug` for noisy operations
- Keep discovery/reachability logs at `debug` level to avoid flooding the CLI

---

## Testing

### Unit Tests
```bash
python3 -m pytest tests/
```

### Integration Tests (Mininet-WiFi)
```bash
sudo python3 meshpay/examples/meshpay_demo.py --mobility --clients 3 --plot
```

In the CLI, useful commands:
- `neighbor <node>` — Show neighbor table
- `<node> ping <node>` — Test L3 reachability
- `transfer <from> <to> <amount>` — Execute payment
- `infor <node>` — Show node state (balances, transactions)
- `help_fastpay` — List all MeshPay commands

---

## Key Design Documents

| Document | Purpose |
|---|---|
| `OFFLINE_PAYMENT_PLAN.md` | 2-Tier D-SDN architecture, threat model, performance targets |
| `d-sdn.md` | Flash-Mesh integration plan, BCB workflow, 30/60/90-day roadmap |

---

## Common Pitfalls

1. **Forgot `sudo make clean install`** — Changes won't take effect; the demo imports from installed packages
2. **`concurrent poll()` crash** — Using `self.cmd()` from a background thread; switch to `self.popen()`
3. **Ghost neighbors** — UDP broadcast reaches all nodes regardless of WiFi range; must verify with `_is_reachable()` (ping)
4. **Python `-c` syntax errors** — `try...except` on one line is invalid Python; use `ping` binary instead of `python3 -c` for probes
5. **Stale Mininet state** — Run `sudo mn -c` between demo runs if things behave unexpectedly
