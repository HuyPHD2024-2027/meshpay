# MeshPay: Resilient Offline Payment with Wireless Mesh Network

MeshPay is a research prototype for resilient offline payments over wireless mesh and opportunistic networks. It combines Mininet-WiFi, DTN/Epidemic Routing, and a FastPay-style authority-quorum payment protocol to study how digital payments can continue when Internet connectivity is unavailable or intermittent.

The current implementation focuses on emulation, protocol behavior, and performance evaluation. It supports interactive offline payment demos, store-carry-forward bundle forwarding, authority signatures, quorum-based confirmation, and benchmark workloads with many virtual accounts per physical wireless station.

---

## Research Context

This repository accompanies the work:

**MeshPay: Resilient Offline Payment with Wireless Mesh Network**
Quang Huy Do, Sara Tucci-Piergiovanni, Justice Owusu Agyemang, Sami Souihi
WCNC 2026

MeshPay studies offline digital payment execution in disrupted or infrastructure-less environments. Instead of assuming permanent Internet access, payments are propagated through nearby wireless devices using a delay-tolerant mesh network. Authorities validate and sign transfer orders, while clients collect quorum signatures and issue confirmation orders.

---

## Current Features

* Wireless offline-payment simulation using Mininet-WiFi.
* DTN/Epidemic Routing daemon for store-carry-forward bundle exchange.
* Interactive MeshPay CLI for manual payment experiments.
* FastPay-style payment flow:

  * `TransferOrder`
  * `SignedTransferOrder`
  * `ConfirmationOrder`
* Authority committee with quorum-based confirmation.
* Client and authority nodes implemented as Mininet-WiFi `Station` subclasses.
* Support for physical station accounts and virtual logical accounts.
* Direct `BundleStore` injection for high-volume benchmarks.
* File-offset log reading for scalable performance collection.
* Benchmark support for:

  * payment throughput
  * TX/RX payload throughput
  * accepted transactions per second
  * quorum latency
  * end-to-end acceptance latency
* Wireless modes:

  * IEEE 802.11 ad hoc
  * IEEE 802.11s mesh
* Mobility support through Mininet-WiFi RandomDirection mobility.

---

## Repository Structure

```text
meshpay/
├── dtn/
│   ├── epidemic.py              # DTN/Epidemic Routing daemon
│   ├── bundle.py                # Bundle data model
│   └── store.py                 # BundleStore persistence
│
├── examples/
│   ├── oppnet.py                # Generic opportunistic-network demo
│   └── meshpay_offline.py       # Interactive MeshPay payment demo
│
├── benchmarks/
│   ├── oppnet_benchmark.py      # Generic DTN benchmark
│   └── meshpay_offline_benchmark.py
│
├── meshpay/
│   ├── cli/
│   │   ├── oppnet_cli.py
│   │   └── meshpay_cli.py       # Interactive MeshPay CLI
│   │
│   ├── offline/
│   │   ├── crypto.py            # Deterministic prototype signing
│   │   ├── dtn_adapter.py       # Payment object ↔ DTN payload bridge
│   │   ├── quorum.py            # Quorum threshold logic
│   │   ├── virtual_accounts.py  # Virtual logical account helpers
│   │   ├── wallet.py            # Client wallet state
│   │   └── nodes/
│   │       ├── client.py        # MeshPay Client Station
│   │       └── authority.py     # MeshPay Authority Station
│   │
│   ├── benchmark/
│   │   ├── metrics.py
│   │   ├── payment_metrics.py
│   │   ├── report.py
│   │   └── traffic.py
│   │
│   └── types/
│       ├── common.py
│       ├── state.py
│       └── transaction.py
│
└── logs/
    ├── examples/
    └── benchmarks/
```

---

## Prerequisites

Recommended environment:

* Ubuntu 20.04 LTS or later
* Linux kernel 5.x or later
* Python 3.8+
* Root privileges for network namespaces
* Mininet-WiFi
* `wmediumd` support for wireless interference simulation

Install Mininet-WiFi:

```bash
git clone https://github.com/intrig-unicamp/mininet-wifi.git
cd mininet-wifi
sudo util/install.sh -Wln
```

Clean Mininet before each run if needed:

```bash
sudo mn -c
sudo pkill -f epidemic.py || true
```

---

## Installation

Clone this repository:

```bash
git clone https://github.com/HuyPHD2024-2027/meshpay.git
cd meshpay
```

Make sure Python can import the local package:

```bash
export PYTHONPATH=$PWD
```

Check imports:

```bash
python3 -c "from meshpay.types.transaction import TransferOrder; print('transaction ok')"
python3 -c "from meshpay.offline.nodes.client import Client; print('client ok')"
python3 -c "from meshpay.offline.nodes.authority import Authority; print('authority ok')"
```

---

## Interactive MeshPay Demo

Run a small offline payment demo:

```bash
sudo python3 examples/meshpay.py \
  --routing epidemic \
  --medium adhoc \
  --clients 3 \
  --authorities 4 \
  --accounts-per-station 5 \
  --initial-balance 100 \
  --node-range 100 \
  --no-mobility
```

For IEEE 802.11s mesh mode:

```bash
sudo python3 examples/meshpay.py \
  --routing epidemic \
  --medium mesh \
  --clients 3 \
  --authorities 4 \
  --accounts-per-station 5 \
  --initial-balance 100 \
  --node-range 100 \
  --no-mobility
```

---

## Interactive CLI Commands

Inside the Mininet-WiFi CLI:

```text
mininet-wifi> pay sta1 sta3 10
```

This creates a payment from the physical station account `sta1` to `sta3`.

For virtual logical accounts:

```text
mininet-wifi> vpay sta1/u00001 sta3/u00001 10
```

Show balances:

```text
mininet-wifi> balance
mininet-wifi> balance sta1
```

Show virtual accounts:

```text
mininet-wifi> accounts
mininet-wifi> accounts sta1
```

Show known payment confirmations:

```text
mininet-wifi> payments
mininet-wifi> payments sta1
```

Inspect MeshPay payment events:

```text
mininet-wifi> paymentlog
```

Inspect DTN daemon logs:

```text
mininet-wifi> dtnlog
mininet-wifi> dtnlog sta1
```

Inspect delivered DTN bundles:

```text
mininet-wifi> delivered
mininet-wifi> delivered sta3
```

Inspect injected bundle creation events:

```text
mininet-wifi> created
mininet-wifi> created sta1
```

Inspect stored bundle files:

```text
mininet-wifi> bundles
mininet-wifi> bundles sta1
```

---

## Payment Protocol Flow

A MeshPay payment follows this flow:

```text
1. Sender creates TransferOrder.
2. TransferOrder is injected into DTN and sent to all authorities.
3. Authorities validate and sign the TransferOrder.
4. Each authority returns a SignedTransferOrder to the sender.
5. Sender collects quorum signatures.
6. Sender creates ConfirmationOrder.
7. ConfirmationOrder is sent to the recipient and authorities.
8. Recipient accepts the payment after receiving a valid ConfirmationOrder.
9. Authorities update their off-chain account state.
```

The DTN layer only stores and forwards bundles. It does not interpret payment semantics. Payment logic is handled by MeshPay clients and authorities.

---

## DTN / Epidemic Routing

Each physical station runs one Epidemic Routing daemon:

```text
sta1  -> dtn/epidemic.py
sta2  -> dtn/epidemic.py
auth1 -> dtn/epidemic.py
auth2 -> dtn/epidemic.py
```

The daemon stores and forwards bundles using a delay-tolerant, store-carry-forward model. Other stations can relay `TransferOrder`, `SignedTransferOrder`, and `ConfirmationOrder` bundles without processing them as payment objects.

Only the final DTN destination processes the payment payload.

Example:

```text
TransferOrder destined to auth1:
    sta2 may store and forward it
    only auth1 processes it

SignedTransferOrder destined to sta1:
    sta3 may store and forward it
    only sta1 processes it

ConfirmationOrder destined to sta3:
    sta2 may store and forward it
    only sta3 accepts the payment
```

---

## Virtual Accounts

To benchmark thousands of logical payments without creating thousands of Mininet-WiFi stations, MeshPay supports virtual accounts.

Example:

```text
Physical station:
    sta1

Virtual accounts:
    sta1/u00001
    sta1/u00002
    sta1/u00003
```

A benchmark can run:

```text
12 physical client stations
4 physical authority stations
100 virtual accounts per client station
```

This gives:

```text
16 physical Mininet-WiFi stations
1200 logical MeshPay accounts
```

This design allows high-volume payment workloads while keeping the wireless simulation within practical server limits.

---

## Benchmarking

Run a small sanity benchmark:

```bash
sudo python3 examples/meshpay_benchmark.py \
  --routing epidemic \
  --medium adhoc \
  --clients 6 \
  --authorities 4 \
  --accounts-per-station 20 \
  --payment-rate 2 \
  --amount 1 \
  --initial-balance 10000 \
  --duration 120 \
  --node-range 100 \
  --no-mobility
```

Run a larger virtual-account benchmark:

```bash
sudo python3 examples/meshpay_benchmark.py \
  --routing epidemic \
  --medium adhoc \
  --clients 12 \
  --authorities 4 \
  --accounts-per-station 100 \
  --payment-rate 10 \
  --amount 1 \
  --initial-balance 10000 \
  --duration 900 \
  --node-range 60 \
  --area-width 200 \
  --area-height 200
```

Run a high-connectivity baseline:

```bash
sudo python3 examples/meshpay_benchmark.py \
  --routing epidemic \
  --medium adhoc \
  --clients 12 \
  --authorities 4 \
  --accounts-per-station 100 \
  --payment-rate 50 \
  --amount 1 \
  --initial-balance 10000 \
  --duration 300 \
  --node-range 300 \
  --no-mobility
```

---

## Benchmark Metrics

MeshPay records payment-level and DTN-level metrics.

Main payment metrics:
The primary loss-comparison metric is `offered_confirmation_rate_percent` (`payments_confirmed / payments_attempted`). `payment_confirmation_rate_percent` remains a conditional metric over admitted, created payments and must not be used alone to claim resilience under overload.


```text
payments_planned
payments_attempted
admission_rate_percent
offered_confirmation_rate_percent
payments_created
payments_confirmed
payments_accepted

created_tps
confirmed_tps
accepted_tps

payment_confirmation_rate_percent
payment_acceptance_rate_percent
```

Payload throughput metrics:

```text
tx_payloads_per_second
rx_payloads_per_second
tx_plus_rx_payloads_per_second

tx_bytes_per_second
rx_bytes_per_second
tx_plus_rx_bytes_per_second
```

Latency metrics:

```text
time_to_quorum_ms
    all payment_created orders; confirmed payments end at confirmation_created,
    unconfirmed payments are censored at benchmark end

time_to_acceptance_ms
    all payment_created orders; accepted payments end at payment_accepted,
    unaccepted payments are censored at benchmark end

payments_unconfirmed
payments_unaccepted
quorum_latency_completed_count
quorum_latency_censored_count
acceptance_latency_completed_count
acceptance_latency_censored_count
```

Latency is now an all-created-payment metric. Under packet loss, failed or
unconfirmed payments remain in the latency distribution until the benchmark
observation window ends, so latency must still be interpreted together with
confirmation and acceptance rates.

Reports are written to:

```text
logs/benchmarks/meshpay_offline/
├── benchmark.json
├── benchmark.csv
├── benchmark_config.json
├── payment.log
├── sta1-epidemic.log
├── auth1-epidemic.log
└── stores/
```

---

## Logs

Interactive demo logs:

```text
logs/examples/meshpay_offline/
```

Benchmark logs:

```text
logs/benchmarks/meshpay_offline/
```

Useful files:

```text
payment.log
    MeshPay payment events.

stores/epidemic/<node>/events.jsonl
    DTN bundle creation, forwarding, and delivery events.

stores/epidemic/<node>/delivered.log
    Bundles delivered to a final destination node.

<node>-epidemic.log
    Per-node Epidemic Routing daemon output.
```

---

## Recommended Experiment Modes

### 1. Protocol capacity baseline

```bash
sudo python3 examples/meshpay_benchmark.py \
  --routing epidemic \
  --medium adhoc \
  --clients 12 \
  --authorities 4 \
  --accounts-per-station 100 \
  --payment-rate 50 \
  --amount 1 \
  --initial-balance 10000 \
  --duration 300 \
  --node-range 300 \
  --no-mobility
```

### 2. Dense mobile setting

```bash
sudo python3 examples/meshpay_benchmark.py \
  --routing epidemic \
  --medium adhoc \
  --clients 12 \
  --authorities 4 \
  --accounts-per-station 100 \
  --payment-rate 20 \
  --amount 1 \
  --initial-balance 10000 \
  --duration 600 \
  --node-range 80 \
  --area-width 200 \
  --area-height 200 \
  --min-velocity 0.5 \
  --max-velocity 2.0
```

### 3. Sparse opportunistic setting

```bash
sudo python3 examples/meshpay_benchmark.py \
  --routing epidemic \
  --medium adhoc \
  --clients 12 \
  --authorities 4 \
  --accounts-per-station 100 \
  --payment-rate 5 \
  --amount 1 \
  --initial-balance 10000 \
  --duration 1800 \
  --node-range 30 \
  --area-width 200 \
  --area-height 200 \
  --min-velocity 0.5 \
  --max-velocity 2.0
```

For thesis-quality evaluation, repeat each experiment with multiple seeds:

```bash
--seed 1
--seed 2
--seed 3
--seed 4
--seed 5
```

---

## Current Limitations

MeshPay is currently a research prototype.

Known limitations:

* Cryptographic signatures are deterministic prototype signatures, not production cryptography.
* The implementation is intended for simulation and experimental evaluation.
* No production blockchain settlement layer is currently integrated.
* No real mobile device deployment is included.
* Confirmation orders should use authority-identified signatures for stronger verification in future versions.
* Epidemic Routing can create many duplicate bundle copies under high load.
* Large experiments should use virtual accounts instead of increasing physical Mininet-WiFi station count.

---

## Troubleshooting

Clean Mininet and old daemons:

```bash
sudo mn -c
sudo pkill -f epidemic.py || true
```

Remove old logs:

```bash
rm -rf logs/examples/meshpay_offline
rm -rf logs/benchmarks/meshpay_offline
```

Avoid plotting during benchmarks:

```text
Do not use --plot for performance experiments.
```

If manual movement causes GUI errors, run without `--plot`.

Check if bundles are being injected:

```text
mininet-wifi> created sta1
mininet-wifi> bundles sta1
mininet-wifi> paymentlog
```

Check if DTN exchange is working:

```text
mininet-wifi> dtnlog
mininet-wifi> delivered
```

---

## Citation

If you use MeshPay in academic work, please cite:

```bibtex
@inproceedings{do2026meshpay,
  title={MeshPay: Resilient Offline Payment with Wireless Mesh Network},
  author={Do, Quang Huy and Tucci-Piergiovanni, Sara and Agyemang, Justice Owusu and Souihi, Sami},
  booktitle={2026 IEEE Wireless Communications and Networking Conference (WCNC)},
  pages={1--6},
  year={2026},
  organization={IEEE}
}

```

---

## Contact

Quang Huy Do
Email: [huydo21052000@gmail.com](mailto:huydo21052000@gmail.com)

---

## License

Licensed under the Apache License 2.0.

© 2026 Quang Huy Do.
