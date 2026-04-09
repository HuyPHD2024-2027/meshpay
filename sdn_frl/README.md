# TEMPO: Partition-Tolerant Asynchronous SDN Architecture

TEMPO is an experimental Mininet-WiFi emulation testbed evaluating a novel partition-tolerant, asynchronous Software-Defined Networking architecture in Wireless Mesh Networks (WMN).

## Architecture

In TEMPO, the physical network is partitioned. To maintain global state, TEMPO replaces traditional synchronous OpenFlow channels with a Conflict-Free Replicated Data Type (CRDT) synchronization model carried by physical mobile nodes ("Data Mules").

```text
┌─────────────────────────┐                            ┌────────────────────────┐
│  Area 1                 │    .... ferry ....         │ Area 2                 │
│  [ap1 (Controller)]     │    <-- (sta1) -->          │ [ap2 (Controller)]     │
│       | UDP :9000       │                            │       | UDP :9000      │
└───────┼─────────────────┘                            └───────┼────────────────┘
        │                                                      │
[Merge CRDT locally]                                    [Merge CRDT locally]
        │                                                      │
        └────────────────────── Data Mules ────────────────────┘
```

**Key Components:**
- **Controllers (ap1, ap2):** Static Access Points acting as isolated SDN controllers. They maintain routing state using CRDTs and process updates asynchronously.
- **Data Mules (sta1-sta10):** Mobile nodes that physically carry δ-CRDT payload between controllers.
- **Density-aware Fallback:** In isolated domains, mules ping the local broadcast domain. If neighbor density falls below a critical threshold (< 3 peers), they increase a logical dissemination TTL.
- **Security:** Mules process the CRDT dictionary dynamically, and Controllers independently evaluate Merkle-DAG signatures to prevent payload injection by malicious mules.

## Getting Started

### Prerequisites

You need `mininet-wifi` installed on a Linux host (with `wmediumd` for realistic wireless medium emulation). 

### Running the Testbed

The testbed automatically spawns the simulated APs, configures the trajectories/random mobility models, and launches the Python `controller_app.py` and `mule_app.py` in the background within the network namespaces of each respective node.

```bash
cd /home/huydq/PHD2024-2027/meshpay/sdn_frl
sudo python3 tempo_topology.py
```

### Viewing Output & Logs

The script redirects the output of the node applications to the `/tmp` directory.

To view the Controller CRDT state changes:
```bash
tail -f /tmp/ap1_tempo.log
tail -f /tmp/ap2_tempo.log
```

To view a Data Mule's decision log (e.g., verifying density-awareness in isolated states vs connected states):
```bash
tail -f /tmp/sta1_tempo.log
```
