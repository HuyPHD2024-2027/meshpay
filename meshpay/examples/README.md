# MeshPay Examples

This directory contains runnable MeshPay/Mininet-WiFi examples and the reusable
helpers used by the emulation benchmark.

Most examples create Linux network namespaces and wireless interfaces, so run
the actual emulations with root privileges. Help and import checks can run
without root.

## Prerequisites

- Run from the repository root: `/home/huydq/PHD2024-2027/meshpay`.
- Use `PYTHONPATH=.` when invoking scripts directly from the checkout.
- Use `sudo` for Mininet-WiFi runs.
- Clean stale Mininet state after failed runs:

```bash
sudo mn -c
```

The code may print `No .env file found ...` during local smoke checks. That is
expected unless blockchain/internet integration settings are needed.

## Example Inventory

- `meshpay_demo.py`: interactive IEEE 802.11s MeshPay demo with optional
  mobility, gateway bridge, xterm logs, and Flash-Mesh QoS/link stats.
- `emulation_benchmark_compare.py`: stable benchmark CLI for a single routing
  run or an isolated Epidemic vs SDN-DTN comparison.
- `emulation/`: importable benchmark building blocks for argument parsing,
  topology setup, workload execution, metrics, plotting, and subprocess
  comparison. These modules are not direct CLI entrypoints.

Generated telemetry such as `*_stats.json` is runtime output. Prefer writing new
results under `results/` with `--output-file`.

## Quick Commands

Show the interactive demo options:

```bash
PYTHONPATH=. python3 -B meshpay/examples/meshpay_demo.py --help
```

Start a small interactive mesh demo:

```bash
sudo PYTHONPATH=. python3 meshpay/examples/meshpay_demo.py \
  --authorities 3 \
  --clients 3 \
  --mobility
```

Start the demo with the optional HTTP gateway bridge:

```bash
sudo PYTHONPATH=. python3 meshpay/examples/meshpay_demo.py \
  --authorities 5 \
  --clients 3 \
  --internet \
  --gateway-port 8080 \
  --mobility
```

Show benchmark options:

```bash
PYTHONPATH=. python3 -B meshpay/examples/emulation_benchmark_compare.py --help
```

Create an output directory for local benchmark artifacts:

```bash
mkdir -p results
```

Run a short single-routing benchmark smoke test:

```bash
sudo PYTHONPATH=. python3 meshpay/examples/emulation_benchmark_compare.py \
  --routing epidemic \
  --duration 5 \
  --authorities 5 \
  --clients 3 \
  --output-file results/epidemic_smoke.json
```

Run the comparison benchmark:

```bash
sudo PYTHONPATH=. python3 meshpay/examples/emulation_benchmark_compare.py \
  --duration 300 \
  --authorities 5 \
  --clients 3 \
  --output-file results/routing_comparison.json \
  --plot-output results/meshpay_routing_comparison.png
```

Run a small campaign validation sweep:

```bash
sudo PYTHONPATH=. python3 meshpay/examples/emulation_benchmark_compare.py \
  --campaign disruption \
  --seeds 1 \
  --duration 30 \
  --peer-discovery-timeout 10 \
  --results-dir results/campaign/smoke_disruption
```

For the full research campaign, see [Emulation Campaigns](#emulation-campaigns).

By default, comparison plotting writes `meshpay_routing_comparison.png` at the
workspace root. `--plot-output` only matters for comparison mode; a single
`--routing epidemic` run writes JSON stats but does not generate a comparison
plot.

## Emulation Campaigns

Campaign mode runs isolated subprocess trials for each routing protocol and
writes reproducible per-run JSON, an aggregated `summary.csv`, and figures under
`--results-dir`. The campaign assumes honest authorities, honest clients, and
valid transfers; losses come from sparse contact, mobility, routing limits,
buffer pressure, or insufficient delivery time.

Compared protocols:

- `sdn_dtn`
- `epidemic`
- `prophet`
- `spray_and_wait`

Available campaign modes:

- `--campaign disruption`: 5 authorities, 10 clients, wireless ranges
  `10,15,20,30`, speeds `1-3`, `3-6`, and `6-10`.
- `--campaign scalability`: node scales `(5,10)`, `(7,20)`, `(9,30)`, and
  `(11,40)` for `(authorities, clients)`.
- `--campaign placement`: `uniform`, `clustered`, `corridor`, and
  `edge_authorities` layouts.
- `--campaign all`: runs all three sweeps. With seeds `1,2,3,4,5`, this expands
  to 400 isolated trials.

Run the balanced no-Byzantine campaign:

```bash
sudo PYTHONPATH=. python3 meshpay/examples/emulation_benchmark_compare.py \
  --campaign all \
  --seeds 1,2,3,4,5 \
  --duration 300 \
  --peer-discovery-timeout 30 \
  --results-dir results/campaign/no_byzantine_balanced
```

Run only one sweep when validating hardware or Mininet stability:

```bash
sudo PYTHONPATH=. python3 meshpay/examples/emulation_benchmark_compare.py \
  --campaign scalability \
  --seeds 1,2 \
  --duration 120 \
  --peer-discovery-timeout 20 \
  --results-dir results/campaign/scalability_validation
```

Campaign outputs:

- `campaign_manifest.json`: selected campaign, seeds, duration, and trial count.
- `<campaign>/<experiment_id>.json`: wrapped per-run metadata and telemetry.
- `summary.csv`: mean, standard deviation, 95% CI, and bytes-per-success
  aggregates grouped by campaign, scenario, protocol, scale, range, and speed.
- `figures/`: generated PNG/PDF research plots when the CLI campaign completes.

Regenerate figures from an existing campaign summary:

```bash
PYTHONPATH=. python3 -m meshpay.examples.emulation.research_figures \
  --summary results/campaign/no_byzantine_balanced/summary.csv \
  --output-dir results/campaign/no_byzantine_balanced/figures \
  --formats png,pdf
```

Useful operational notes:

- Use `sudo mn -c` before a long campaign if a previous Mininet run failed.
- Keep campaign results under `results/campaign/...`; do not write generated
  telemetry into source directories.
- For a quick syntax/import check without running Mininet, use `--help` or the
  import smoke check in [Troubleshooting](#troubleshooting).

## Interactive CLI Commands

When `meshpay_demo.py` starts, it opens `MeshPayCLI`. Useful commands include:

- `help_meshpay`: show MeshPay-specific commands.
- `balance <user>`: show one user's token balances.
- `balances`: show balances for all clients.
- `transfer <sender> <recipient> <token> <amount>`: submit an offline payment.
- `status`: summarize node counts and running state.
- `infor <node|all>` or `info <node|all>`: show node state.
- `neighbor <node|all>`: show discovered peers.
- `summary <node>`: show the DTN message buffer.
- `log <node> [lines]`: print recent node log output.
- `performance <authority|all>`: show authority metrics.
- `network_metrics <authority|all>`: show link and transaction metrics.

Standard Mininet-WiFi CLI commands such as `nodes`, `net`, `links`, and
`<node> ping <node>` are also available.

## Benchmark Notes

Routing options are discovered from `meshpay.routing.registry`:

- `epidemic`
- `sdn_dtn`
- `spray_and_wait`
- `prophet`

`--routing-mode sdn` is kept for compatibility and normalizes to `sdn_dtn`.
The default comparison mode runs Epidemic and SDN-DTN in isolated subprocesses
with cleanup between runs. Campaign mode compares all four routing protocols
(`sdn_dtn`, `epidemic`, `prophet`, and `spray_and_wait`) across disruption,
scalability, and placement sweeps, then writes per-run JSON, `summary.csv`, and
figures under `--results-dir`.

Wireless interface options are discovered from `meshpay.oppnet.interfaces`:

- `mesh_80211s`
- `adhoc_wifi`
- `wifi_direct`
- `physical_wifi_direct`
- `wwan_d2d`

## Troubleshooting

- Permission errors or missing network namespaces: rerun with `sudo`.
- Stale interfaces, failed sockets, or odd connectivity after a crash:
  run `sudo mn -c`, then retry.
- Peer discovery is probabilistic under mobility. For debugging, use fewer
  nodes, longer duration, or shorter mobility ranges.
- `pytest` is not installed in some local environments. A basic import smoke
  check is:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -c \
  "from meshpay.examples.emulation.runner import run_emulation; print('ok')"
```

## Refactor Audit

This section records low-risk cleanup opportunities found during the repository
scan. It is intentionally a roadmap, not a behavior change.

- `meshpay_demo.py` can be split into topology creation, account seeding,
  gateway setup, service lifecycle, and CLI banner helpers.
- `meshpay/cli_fastpay.py` can be split into command handlers and display/table
  formatting helpers.
- `meshpay/transport/tcp.py` and `meshpay/transport/udp.py` can share namespace
  script creation, message serialization, and log-tail parsing utilities.
- `meshpay/logger/*Logger.py` can share a base logger for file setup, xterm
  lifecycle, and console formatting.
- Generated telemetry and cache files should stay out of source-controlled
  example logic; prefer `results/` for new experiment outputs.

