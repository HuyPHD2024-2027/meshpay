# SDN-FedRL Mesh Simulation

This project implements a resilient offline payment simulation over a Wireless Mesh Network (WMN) using Mininet-WiFi, SD-QoS Routing (Ryu), and Federated Reinforcement Learning (Flower).

## Prerequisites

- **Mininet-WiFi**: Installed on the host system.
- **Ryu Controller**: Using the specified book fork for compatibility.
- **Flower (flwr)**: Python package for Federated Learning.
- **wmediumd**: For realistic wireless medium emulation.

## Installation

### 1. Clone the Ryu Fork
As recommended by the Mininet-WiFi eBook, clone the specific Ryu fork and branch into your project or Mininet-WiFi directory:

```bash
git clone https://github.com/ramonfontes/ryu -b book
```

### 2. Install Dependencies
```bash
pip install flwr ryu
```

## Running the Simulation

Follow these steps in separate terminals:

### Step 1: Start the Ryu Remote Controller
Run the Ryu controller with the SD-QoS application:

```bash
cd /meshpay/ryu
sudo PYTHONPATH=. ./bin/ryu-manager /meshpay/sdn_frl/controller_app.py
```

### Step 2: Start the FRL Server (Flower)
In another terminal, start the Federated Learning aggregator:

```bash
python3 /meshpay/sdn_frl/frl_server.py
```

### Step 3: Launch the Mininet-WiFi Topology
Run the topology script (requires sudo):

```bash
sudo python3 /meshpay/sdn_frl/topology.py
```

### Step 4: Start FRL Clients
Once the Mininet-WiFi CLI is up, you can start the FRL clients on individual nodes. For example:

```bash
mininet-wifi> sta1 python3 /meshpay/sdn_frl/frl_client.py --node sta1 &
mininet-wifi> sta2 python3 /meshpay/sdn_frl/frl_client.py --node sta2 &
```

## Project Structure

- `topology.py`: Mininet-WiFi setup with mobility and wmediumd.
- `controller_app.py`: Ryu application for SD-QoS telemetry and routing.
- `frl_server.py`: Flower server for weight aggregation.
- `frl_client.py`: Flower client with an RL agent and local telemetry polling.
- `consensus_sim.py`: Simulation of weighted BFT consensus.
- `performance_logger.py`: Utility for logging metrics to CSV.

## Performance Analysis
The simulation logs PDR, BER, Latency, and Overhead to `performance_metrics.csv` for post-simulation analysis.
