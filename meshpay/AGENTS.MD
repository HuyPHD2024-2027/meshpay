# Repository Guidelines

## Project Purpose

This package implements MeshPay/FastPay-style offline payment behavior over emulated wireless mesh networks. The core design combines authority nodes, user/client nodes, local transport protocols, payment messages, telemetry, controller support, and Mininet-WiFi demos. Payment safety must remain independent from transport or SDN decisions: controllers may affect forwarding, prioritization, telemetry, fallback, and QoS, but not whether a transaction is financially valid.

## Project Structure

- `nodes/`: Wi-Fi authority/client node classes and mesh helpers.
- `types/`: transaction, network, common, and off-chain state datatypes.
- `transport/`: TCP, UDP, Wi-Fi Direct, and transport abstractions.
- `routing/`: DTN and epidemic routing logic.
- `controller/`: QoS, fallback, and link statistics support.
- `telemetry/`: telemetry agents, controller, and metrics.
- `logger/`: authority, client, bridge, and formatting loggers.
- `api/`: bridge and gateway integration.
- `examples/`: runnable Mininet-WiFi demos and benchmarks, including `meshpay_demo.py`.

## Mininet-WiFi References

Use the local source tree as the primary API reference: `/home/huydq/PHD2024-2027/meshpay/mn_wifi`. Key files:

- `mn_wifi/net.py`: `Mininet_wifi`, `addStation`, `addAccessPoint`, `configureNodes`, `setPropagationModel`, `setMobilityModel`.
- `mn_wifi/node.py`: `Station`, AP classes, and Wi-Fi node behavior. MeshPay node classes used with `net.addStation(..., cls=...)` should inherit from the appropriate Mininet-WiFi node class.
- `mn_wifi/link.py`: `mesh`, `wmediumd`, wireless links, and 802.11s behavior.
- `mn_wifi/wmediumdConnector.py`: interference modes and wmediumd integration.
- `mn_wifi/mobility.py`: supported mobility models.
- `mn_wifi/propagationModels.py`: propagation model parameters.
- `mn_wifi/cli.py`: CLI behavior and inspection commands.
- `mn_wifi/examples/` and `mn_wifi/test/`: local examples and regression patterns.

Also consult the open Mininet-WiFi ebook: `https://github.com/ramonfontes/mn-wifi-ebook/tree/main`. The ebook emphasizes Linux/Ubuntu development, reproducibility, cleanup with `sudo mn -c`, position-aware experiments, propagation models, mobility, wmediumd, and OpenFlow/SDN use in wireless scenarios.

## Mininet-WiFi Topology Rules

Follow `examples/meshpay_demo.py` for the current preferred topology style:

- Use `Mininet_wifi(link=wmediumd, wmediumd_mode=interference)` for realistic wireless behavior.
- Use `net.addStation(..., cls=WiFiAuthority)` for authorities and `net.addStation(..., cls=Client)` for users.
- Use 802.11s links with `net.addLink(node, cls=mesh, ssid='meshNet', intf='node-wlan0', channel=5, ht_cap='HT40+')`.
- Call `net.setPropagationModel(...)` before `net.configureNodes()`.
- Call `net.configureNodes()` before adding wireless links.
- Use `net.plotGraph(max_x=..., max_y=...)` for visual topology demos.
- Use `net.setMobilityModel(...)` or scripted `setPosition(...)` calls for mobility.
- Run Mininet-WiFi experiments with root privileges and clean failed runs with `sudo mn -c`.

## Logging and Experiment Output

Use `meshpay/logger/` for node logs. Authority and client code should write role-specific logs, support optional xterm tailing, and record service start/stop, message send/receive, validation, transfer, balance, telemetry, mobility, and network events. Store generated experiment artifacts under a results directory rather than mixing them with source modules.

## Development Commands

Common checks:

```bash
python3 -m compileall /home/huydq/PHD2024-2027/meshpay/meshpay
sudo mn -c
```

Run the main mesh demo:

```bash
sudo python3 -m meshpay.examples.meshpay_demo --authorities 5 --clients 3 --mobility --plot --logs
```

Run benchmark-style emulation:

```bash
sudo python3 -m meshpay.examples.emulation_benchmark --authorities 5 --clients 3 --mobility --plot
```

## Coding Style

Use Python 3.10+, 4-space indentation, `snake_case` for functions/variables, and explicit datatypes where practical. Keep simulation constants in CLI arguments or config objects. Avoid hardcoded machine paths except documented local development references. Keep Mininet-WiFi imports close to runtime entry points when possible so unit-level imports do not require root or kernel wireless setup.

## Testing Guidance

When changing payment safety, authority voting, quorum logic, transport behavior, routing, telemetry, QoS, fallback, or topology construction, add deterministic tests or short smoke scripts. For Mininet-WiFi changes, verify with a short run, plot enabled, logs enabled, and cleanup afterward with `sudo mn -c`.

## Agent-Specific Rules

Do not collapse the architecture into a centralized always-online controller. Preserve intermittent wireless assumptions, authority/user roles, and payment correctness independent from networking policy. Prefer existing MeshPay node/logger/controller patterns before adding new abstractions.
