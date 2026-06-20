# MeshPay DTN Routing Protocols

This repository supports three DTN routing protocols for MeshPay offline-payment benchmarks:

- `epidemic`: summary-vector epidemic forwarding. Every exchange sends bundles the peer does not already report.
- `spray-and-wait`: binary Spray-and-Wait forwarding with 8 initial copies per locally created bundle. Direct destination delivery is always allowed; non-destination forwarding splits copies and stops relaying when only one local copy remains.
- `prophet`: PRoPHET forwarding with delivery predictabilities. Contacts update direct predictability, peer summaries update transitive predictability, and bundles are forwarded only when the peer has a higher predictability for the destination or is the destination.

The protocol daemons live in `dtn/` and share the same discovery, TCP exchange, persistent bundle store, and event logging path. Benchmark output stores are written under `stores/<routing>/<node>/`.

## Default Parameters

Spray-and-Wait:

- Initial copies: `8`
- Copy policy: binary split, retaining the remainder locally
- State file: `.spray_state` in each node store

PRoPHET:

- `P_INIT = 0.75`
- `BETA = 0.25`
- `GAMMA = 0.98`
- Aging unit: one discovery interval
- State file: `.prophet_state` in each node store


## Improved PRoPHET for MeshPay

The `prophet` router is MeshPay-aware. It still learns delivery predictabilities from direct and transitive contacts, but also applies role priors for quorum routing:

- `transfer_order`: authorities receive a high routing prior.
- `signed_transfer_order`: the sender host receives a high routing prior.
- `confirmation_order`: the recipient host and authorities receive high routing priors.

PRoPHET forwarding is relaxed with an epsilon margin (`0.05`) and bounded replication budget so useful relays are not skipped while the contact graph is still learning. Transfer orders use a dedicated replication budget of `12`; signed-transfer and confirmation payloads keep their existing derived budgets of `8` and `12`.

Benchmark runs now also record:

- `routing_warmup`: router-only learning time before payment traffic. Default is `60s` for `prophet`, `0s` otherwise.
- `bundle_ttl`: DTN bundle TTL. Default is `max(900, duration + warmup + routing_warmup + 120)`.

## Packet-Loss Attack Smoke Matrix

Run the compact comparison matrix with three protocols and 25%, 50%, and 80% packet-loss attack levels:

```bash
python3 scripts/run_meshpay_benchmark_matrix.py \
  --execute \
  --continue-on-error \
  --routing epidemic,spray-and-wait,prophet \
  --attack packetloss \
  --attack-loss-probability 0.25,0.5,0.8 \
  --clients 4 \
  --authorities 4 \
  --ranges 100 \
  --accounts 10 \
  --payment-rate 50,100,500,1000 \
  --medium mesh \
  --duration auto \
  --attack-tpre 60 \
  --attack-tatk 180 \
  --attack-tpost 60 \
  --output-root logs/benchmarks/scripts
```

With `--duration auto`, each attack run keeps a 120 second observation tail after the pre-attack, attack, and recovery traffic windows.

The matrix writes per-run logs and reports in each run directory, plus `summary.json` and `summary.csv` at the output root.

## Plotting

Use `scripts/plot_attack_impact.py` on one or more run directories. The plotter reads each run's routing protocol from `benchmark_config.json` or `benchmark.json` and loads DTN throughput from `stores/<routing>`.

Example overlay for selected runs:

```bash
python3 scripts/plot_attack_impact.py \
  --label "epidemic loss=25%" logs/benchmarks/scripts/001_c4_a4_r100_rate0p5_mmesh_rtEpi_attPL_loss0p25 \
  --label "spray-and-wait loss=25%" logs/benchmarks/scripts/004_c4_a4_r100_rate0p5_mmesh_rtSnW_attPL_loss0p25 \
  --label "prophet loss=25%" logs/benchmarks/scripts/007_c4_a4_r100_rate0p5_mmesh_rtProphet_attPL_loss0p25 \
  -o figures/protocol_attack_smoke_mesh/loss25
```
