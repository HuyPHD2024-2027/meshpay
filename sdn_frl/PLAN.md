# TEMPO Implementation Plan

> **Project Path**: `/home/huydq/PHD2024-2027/meshpay/sdn_frl/`
> 
> **Goal**: Emulate "TEMPO" - a partition-tolerant, asynchronous SDN architecture via Mininet-WiFi where isolated controllers use CRDTs to synchronize routing state via Data Mules.

---

## 1. Network Topology (`tempo_topology.py`)
- Emulate a 100x100m area.
- Use `wmediumd` mapped to Mininet-WiFi's `logDistance` propagation model.
- **Controllers:** Deploy 2 Access Points (`ap1`, `ap2`) at far ends of the grid (10,10 and 90,90). They represent completely partitioned networks that have no physical link between them.
- **Mules:** Deploy 10 mobile stations (`sta1` to `sta10`).
- **Mobility:** Set `sta1` with a specific mobility path taking it from `ap1` to `ap2` and back, simulating a ferry/mule. `sta2` to `sta10` follow random walk/direction.

## 2. Controller Agent (`controller_app.py`)
- Runs independently on `ap1` and `ap2`.
- Tracks routing and system state using a Last-Writer-Wins (LWW) CRDT map.
- Listens on UDP `0.0.0.0:9000`.
- **Validation Phase**: Implements pseudo-cryptography by verifying a mocked SHA-256 hash or Merkle-DAG proof before accepting a dataset.
- **Merge Phase**: When a Data Mule uploads its payload, the controller merges incoming δ-CRDT, updates its internal view, and ships the newest synchronized δ-CRDT state back to the Mule.

## 3. Data Mule Agent (`mule_app.py`)
- Runs independently on `sta1` through `sta10`.
- Automatically loops to attempt connection with hardcoded AP IP addresses `10.0.0.101` and `10.0.0.102`.
- **Connected Mode:** Exists when the Mule successfully gets a UDP response from the AP. The CRDT map is successfully synced in both directions.
- **Isolated Mode (Density-Aware):** Exists when the UDP messages timeout. The node pings the local layer 2 segment or `iw station dump` to approximate node density.
  - Sub-critical density (< 3 peers): dissemination logical TTL set to `HIGH`.
  - Super-critical density (>= 3 peers): dissemination logical TTL set to `LOW`.

## 4. Current State
The project has successfully transitioned from standard SDN-FRL to TEMPO:
- `core/`, `fl/`, `controller/` directories have been removed in favor of lightweight python apps (`controller_app.py`, `mule_app.py`).
- `tempo_topology.py` replaces standard topology definitions, integrating both the network structure and application launcher script. 
