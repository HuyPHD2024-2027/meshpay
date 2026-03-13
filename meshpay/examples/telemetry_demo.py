#!/usr/bin/env python3
"""Telemetry Demo for MeshPay.

This demo creates a mesh network and equips every node with a TelemetryDaemon.
A central TelemetryAggregator runs on the host to collect and display the aggregated FRL network state via MQTT.

Run with root privileges:
    sudo python3 -m meshpay.examples.telemetry_demo
"""

import time
import json
import threading
import subprocess
from mininet.log import info, setLogLevel
from mn_wifi.link import wmediumd, mesh
from mn_wifi.wmediumdConnector import interference
from mn_wifi.net import Mininet_wifi

from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client1 import Client
from meshpay.telemetry.telemetry_agent import TelemetryDaemon
from meshpay.telemetry.telemetry_controller import TelemetryAggregator
from mininet.nodelib import NAT


def main() -> None:
    setLogLevel("info")
    info("🚀 Starting MeshPay Telemetry Demo\n")

    # 1. Create network
    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)

    # 2. Add nodes
    info("🕸️  Adding nodes...\n")
    auth1 = net.addStation(
        "auth1",
        cls=WiFiAuthority,
        committee_members=set(),
        ip="10.0.0.11/8",
        position="50,50,0",
        range=40,
        battery=99.0,
    )
    
    auth2 = net.addStation(
        "auth2",
        cls=WiFiAuthority,
        committee_members={"auth1"},
        ip="10.0.0.12/8",
        position="80,50,0",
        range=40,
        battery=100.0,
    )
    auth1.state.committee_members.add("auth2")

    client1 = net.addStation(
        "user1",
        cls=Client,
        ip="10.0.0.21/8",
        min_x=0, max_x=150, min_y=0, max_y=150, min_v=1, max_v=5,
        range=40,
        battery=85.0,
    )
    
    client2 = net.addStation(
        "user2",
        cls=Client,
        ip="10.0.0.22/8",
        min_x=0, max_x=150, min_y=0, max_y=150, min_v=1, max_v=5,
        range=40,
        battery=42.5,
    )

    # 3. Add NAT for Host <-> Mininet communication
    # This allows the TelemetryAggregator on the host to subscribe to the Mosquitto broker on auth1
    info("🌐 Adding NAT to allow host communication...\n")
    nat = net.addNAT(name='nat0', ip='10.0.0.1/8', connect=auth1)

    # 4. Configure mesh networking
    info("*** Configuring IEEE 802.11s mesh\n")
    net.setPropagationModel(model="logDistance", exp=4.0)
    net.configureNodes()

    for node in [auth1, auth2, client1, client2]:
        net.addLink(node, cls=mesh, ssid="meshpay-mesh", intf=f"{node.name}-wlan0", channel=1)

    # 5. Configure mobility
    info("*** Setting up mobility\n")
    net.setMobilityModel(
        time=0,
        model='GaussMarkov',
        velocity_mean=2,
        alpha=0.5,
        variance=0.1,
        seed=42
    )

    # 6. Build and start network
    info("*** Building network\n")
    net.plotGraph(max_x=200, max_y=150)
    net.build()
    
    for node in [auth1, auth2, client1, client2]:
        node.start_fastpay_services()
        # Add default route for UDP broadcasts and ensure loopback is UP for local telemetry
        node.cmd(f"ip route add default dev {node.name}-wlan0")
        node.cmd("ip link set lo up")
        
        # FIX: Explicit Mesh Peering and Interface setup
        intf = f"{node.name}-wlan0"
        node.cmd(f"ifconfig {intf} up")
        node.cmd(f"iw dev {intf} set type mesh")
        node.cmd(f"iw dev {intf} mesh join meshpay-mesh")
        
        # FIX: Ensure MeshMixin knows to report to the host's NAT IP
        node.telemetry_aggregator_ip = "10.0.0.1"

    # 7. Start Telemetry Daemons on all nodes (Broadcasting DISABLED, using Piggybacking)
    info("📊 Starting Telemetry Daemons on all nodes (Piggybacking mode)...\n")
    daemons = []
    for node in [auth1, auth2, client1, client2]:
        # FIX: Report to the host namespace's NAT IP (10.0.0.1)
        daemon = TelemetryDaemon(node, interval=2.0, enable_broadcast=False, aggregator_ip="10.0.0.1")
        daemon.start()
        daemons.append(daemon)
        # Attach daemon to node so MeshMixin can see it
        node.telemetry_daemon = daemon

    # 8. Start Telemetry Aggregator natively on the HOST (root namespace)
    # This ensures it can always receive packets sent to 10.0.0.1 (NAT gateway)
    info("📈 Starting UDP Telemetry Aggregator on host...\n")
    aggregator = TelemetryAggregator(udp_port=5005, node=None)
    aggregator.start()

    # 9. Start background traffic (ping) to ensure links are active and measurable
    def generate_traffic():
        info("📡 Starting background traffic generator...\n")
        import subprocess
        while True:
            try:
                # Use popen instead of cmd to avoid shell contention
                proc1 = client1.popen(["ping", "-c", "1", "10.0.0.11"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                proc2 = client2.popen(["ping", "-c", "1", "10.0.0.12"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                proc1.wait(timeout=2.0)
                proc2.wait(timeout=2.0)
            except Exception:
                pass
            time.sleep(1)
    
    traffic_thread = threading.Thread(target=generate_traffic, daemon=True)
    traffic_thread.start()

    # 10. Run Demo Loop
    try:
        info("\n>>> Mesh Network is running. Monitoring Telemetry State...\n")
        info(">>> Press Ctrl-C to exit.\n")
        while True:
            time.sleep(5)
            state = aggregator.get_network_state()
            info("-" * 50 + "\n")
            info("Current Global Network State (FRL inputs):\n")
            for node_name, t_state in state.items():
                pos = t_state.mobility.position
                rssi = t_state.wireless.rssi_dbm
                batt = t_state.resources.battery_level
                buf = t_state.resources.buffer_occupancy
                rx = t_state.wireless.rx_bytes
                tx = t_state.wireless.tx_bytes
                sinr = t_state.wireless.sinr
                rep = t_state.app.reputation_score
                batt_str = f"{batt:.1f}%" if batt is not None else "N/A"
                rssi_str = f"{rssi:.1f}dBm" if rssi is not None else "N/A"
                sinr_str = f"{sinr:.1f}dB" if sinr is not None else "N/A"
                info(f"[{node_name}] Pos: ({pos[0]:.1f}, {pos[1]:.1f}) | RSSI: {rssi_str} | Batt: {batt_str} | Buf: {buf} | RX: {rx}B, TX: {tx}B | SINR: {sinr_str} | Rep: {rep:.2f}\n")
            
            # Link-Layer Connectivity monitoring (show once per loop)
            try:
                # Use popen instead of cmd to avoid shell contention (AssertionError)
                proc = auth1.popen(["iw", "dev", "auth1-wlan0", "station", "dump"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                peer_check, _ = proc.communicate(timeout=2.0)
                if "Station" in peer_check:
                    num_peers = len([l for l in peer_check.split('\n') if "Station" in l])
                    info(f"✅ auth1 link layer: {num_peers} active mesh peers\n")
                else:
                    info(f"❌ auth1 link layer: No mesh peers established yet\n")
            except Exception as e:
                info(f"⚠️  auth1 link check error: {e}\n")
    except KeyboardInterrupt:
        info("\n*** Interrupted by user\n")
    finally:
        info("*** Stopping Telemetry Services...\n")
        aggregator.stop()
        for daemon in daemons:
            daemon.stop()
        for node in [auth1, auth2, client1, client2]:
            node.stop_fastpay_services()
            
        
        info("*** Stopping network\n")
        net.stop()

if __name__ == "__main__":
    main()
