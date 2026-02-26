#!/usr/bin/env python3
"""Enhanced FastPay IEEE 802.11s Mesh Network Demo with Internet Gateway.

This enhanced demo creates a mesh network that can communicate with external 
web backends through a dedicated internet gateway, enabling real-world integration
while maintaining mesh network integrity.

Key Features:
- IEEE 802.11s mesh networking with internet connectivity
- Gateway bridge for external communication
- Real-time authority discovery via TCP
- HTTP bridge server for protocol translation
- Maintained mesh network isolation and security

Run with root privileges:
    sudo python3 -m mn_wifi.examples.fastpay_mesh_internet_demo --authorities 5 --clients 3 --internet
    sudo python3 -m mn_wifi.examples.fastpay_mesh_internet_demo --authorities 8 --clients 4 --internet --plot

Options:
    --authorities N     Number of authority nodes (default: 5)
    --clients N         Number of client nodes (default: 3)
    --internet          Enable internet gateway (default: False)
    --gateway-port P    Gateway HTTP bridge port (default: 8080)
    --mobility          Enable advanced mobility models
    --plot              Enable network visualization
    --logs              Open xterm for each node
    --mesh-id ID        Mesh network identifier (default: fastpay-mesh)
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import json
import socket
import socketserver
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple, Any, Union
from urllib.parse import urlparse, parse_qs
from uuid import uuid4, UUID
import dataclasses
import random

from mininet.log import info, setLogLevel
from mininet.nodelib import NAT
from mininet.link import TCLink
from mn_wifi.link import wmediumd, mesh
from mn_wifi.wmediumdConnector import interference
from mn_wifi.net import Mininet_wifi
from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client1 import Client
from meshpay.cli_fastpay import MeshPayCLI
from meshpay.transport import TransportKind
from meshpay.types import KeyPair, AccountOffchainState, SignedTransferOrder
from meshpay.api.bridge import Bridge
from meshpay.api.gateway import Gateway
from meshpay.controller import QoSManager, LinkStatsCollector, FallbackProfile
from mn_wifi.examples.demoCommon import (
    open_xterms as _open_xterms,
    close_xterms as _close_xterms,
    parse_mesh_internet_args,
)


def create_mesh_network_with_internet(
    num_authorities: int = 5,
    num_clients: int = 3,
    mesh_id: str = "meshpay",
    enable_mobility: bool = True,
    enable_plot: bool = False,
    enable_internet: bool = False,
    gateway_port: int = 8080
) -> Tuple[Mininet_wifi, List[WiFiAuthority], List[Client], Optional[Bridge]]:
    """Create IEEE 802.11s mesh network topology with optional internet gateway.
    
    Args:
        num_authorities: Number of authority nodes to create
        num_clients: Number of client nodes to create
        mesh_id: Mesh network identifier
        enable_mobility: Enable mobility models
        enable_plot: Enable network visualization
        enable_internet: Enable internet gateway bridge
        gateway_port: Port for the HTTP bridge server
        
    Returns:
        Tuple of (network, authorities, clients, bridge)
    """
    info("🕸️  Creating IEEE 802.11s Mesh Network with Internet Gateway\n")
    
    # Create network without access points
    net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)
    # net = Mininet_wifi()

    # Create authorities as mesh points
    authorities: List[WiFiAuthority] = []
    committee = {f"auth{i}" for i in range(1, num_authorities + 1)}
    
    for i in range(1, num_authorities + 1):
        name = f"auth{i}"
        auth = net.addStation(
            name,
            cls=WiFiAuthority,
            committee_members=committee - {name},
            ip=f"10.0.0.{10 + i}/8",
            port=8000 + i,
            position=f'{40 + (i * 15)},100,0',
            range=15,
            txpower=10
        )
        authorities.append(auth)
    
    # Create mobile clients as mesh points
    clients: List[Client] = [] 
    for i in range(1, num_clients + 1):
        name = f"user{i}"
        client = net.addStation(
            name,
            cls=Client,  
            ip=f"10.0.0.{20 + i}/8",
            port=9000 + i,
            min_x=0, max_x=200, min_y=0, max_y=150, min_v=1, max_v=3,    
            range=15,
            txpower=10,
        )
        clients.append(client)
    
    # Add internet gateway if enabled
    bridge = None
    gateway = None
    if enable_internet:
        info("🌐 Adding Internet Gateway\n")
        
        # Add host node as internet gateway (replaces access point)
        gateway = net.addStation(
            'gw',
            cls=Gateway,
            ip='10.0.0.254/8',
            # position=[20, 40, 0]
        )
        
        # Add NAT for internet connectivity using the host gateway
        nat = net.addNAT(name='nat0', connect=gateway, ip='10.0.0.1/8')
        
        # Create mesh-internet bridge service, passing host so HTTP API can
        # reuse host.forward_transfer when /transfer is called.
        bridge = Bridge(gateway, net=net, port=gateway_port)
    
    # Configure mesh networking
    info("*** Configuring IEEE 802.11s mesh\n")
    
    # Set propagation model for realistic mesh behavior
    
    # Takes the logDistance model and adds a "shadowing" factor—a random variable 
    # that represents signal fluctuations caused by obstacles. This means that two nodes 
    # at the exact same distance might have different signal strengths, 
    # which is how real physics works.
    # net.setPropagationModel(model="logNormalShadowing", exp=3.5, sL=6.0)

    # From 3.5 to 5.0 or 5.5. This causes the signal to drop significantly faster. 
    # A node just 1 meter outside the range will suddenly have such a weak signal 
    # that packets will drop 100%
    net.setPropagationModel(model="logDistance", exp=5.0)

    # Configure nodes
    net.configureNodes()
    
    info("*** Creating mesh links\n")
    for i in range(1, num_authorities + 1):
        net.addLink(authorities[i-1], cls=mesh, ssid='meshNet',
                    intf=f'auth{i}-wlan0', channel=5, ht_cap='HT40+')
    for i in range(1, num_clients + 1):
        net.addLink(clients[i-1], cls=mesh, ssid='meshNet',
                intf=f'user{i}-wlan0', channel=5, ht_cap='HT40+')
    if enable_internet:
        net.addLink(gateway, cls=mesh, ssid='meshNet',
                intf=f'gw-wlan0', channel=5, ht_cap='HT40+')
    
    # Add gateway connection if internet is enabled
    if enable_internet:
        info("*** Configuring internet gateway connections\n")
        # # Connect first authority to the internet gateway host via wired link
        for i in range(1, num_authorities + 1):
            net.addLink(authorities[i-1], gateway, cls=TCLink)


    # Configure mobility if enabled
    if enable_mobility:
        info("*** Setting up mesh mobility\n")
        net.setMobilityModel(
            time=0,
            model='GaussMarkov',
            velocity_mean=1,
            alpha=0.5,
            variance=0.1,
            seed=42
        )
    
    # Assign committee (all authorities) to each client
    for client in clients:
        client.state.committee = authorities

    # Enable plotting if requested
    if enable_plot:
        info("*** Plotting mesh network\n")
        net.plotGraph(max_x=200, max_y=150)

    return net, authorities, clients, gateway, bridge

def setup_test_accounts(authorities: List[WiFiAuthority], clients: List[Client]) -> None:
    """Initialise each client account on all authorities with random balances.

    Balances are assigned per supported token to seed off-chain state so
    transfers validate immediately in demos.
    """

    info("*** Setting up test accounts\n")
    
    # Initialize committee for each client so they know which authorities to talk to
    for client in clients:
        client.state.committee = authorities
        info(f"   ✅ {client.name}: Committee configured ({len(authorities)} members)\n")

    from mn_wifi.services.core.config import SUPPORTED_TOKENS
    from mn_wifi.services.blockchain_client import TokenBalance

    for authority in authorities:
        if not hasattr(authority, 'state'):
            info(f"   ⚠️  {authority.name}: No state found, skipping\n")
            continue

        for client in clients:
            balances_map = {}
            for symbol, cfg in SUPPORTED_TOKENS.items():
                token_address = cfg.get('address')
                if not token_address:
                    continue
                decimals = int(cfg.get('decimals', 18))

                meshpay_balance = round(random.uniform(100, 1000), 3)
                wallet_balance = round(random.uniform(0, 250), 3)
                total_balance = round(meshpay_balance + wallet_balance, 3)

                balances_map[token_address] = TokenBalance(
                    token_symbol=symbol,
                    token_address=token_address,
                    wallet_balance=wallet_balance,
                    meshpay_balance=meshpay_balance,
                    total_balance=total_balance,
                    decimals=decimals,
                )

            authority.state.accounts[client.name] = AccountOffchainState(
                address=client.name,
                balances=balances_map,
                sequence_number=0,
                last_update=time.time(),
                pending_confirmation=None,  # type: ignore[arg-type]
                confirmed_transfers={},
            )

        info(f"   ✅ {authority.name}: Setup {len(clients)} client accounts\n")


def configure_internet_access(
    net: Mininet_wifi, 
    authorities: List[WiFiAuthority], 
    gateway: Optional[Node_wifi],
    bridge: Optional[Bridge]
) -> None:
    """Configure internet access for mesh nodes via NAT gateway.
    
    Args:
        net: Mininet-wifi network instance
        authorities: List of mesh authorities
        clients: List of mesh clients
        bridge: Mesh internet bridge instance
    """
    if not bridge:
        return
        
    info("*** Configuring internet access for mesh nodes\n")
    
    # Register authorities with bridge for HTTP API access
    for auth in authorities:
        bridge.register_authority(auth)
        gateway.register_authority(auth)
    
    # The NAT routing is automatically configured by mn-wifi's addNAT method
    # which sets default routes for all stations in the network
    info("*** NAT routing configured automatically by mn-wifi\n")
    
    # Enable IP forwarding on gateway host if needed
    gateway.cmd('sysctl -w net.ipv4.ip_forward=1')
    info(f"*** IP forwarding enabled on {gateway.name}\n")
    
    # Start bridge service on gateway host
    bridge.start_bridge_server(gateway)

def main() -> None:
    """Main entry point for the enhanced mesh demo."""
    args = parse_mesh_internet_args()
    setLogLevel("info")
    
    info(f"🚀 Enhanced FastPay IEEE 802.11s Mesh Network Demo\n")
    info(f"   Authorities: {args.authorities}\n")
    info(f"   Clients: {args.clients}\n")
    info(f"   Mesh ID: {args.mesh_id}\n")
    info(f"   Internet Gateway: {'Enabled' if args.internet else 'Disabled'}\n")
    info(f"   Gateway Port: {args.gateway_port}\n")
    info(f"   Mobility: {'Enabled' if args.mobility else 'Disabled'}\n")
    
    net = None
    bridge = None
    qos_mgr = None
    link_stats = None
    fallback = None
    try:
        # Create enhanced mesh network with internet gateway
        net, authorities, clients, gateway, bridge = create_mesh_network_with_internet(
            num_authorities=args.authorities,
            num_clients=args.clients,
            mesh_id=args.mesh_id,
            enable_mobility=args.mobility,
            enable_plot=args.plot,
            enable_internet=args.internet,
            gateway_port=args.gateway_port
        )
        
        # Build and start network
        info("*** Building enhanced mesh network\n")
        net.build()
        
        # info("*** Assigning IPs to wired interfaces for auth-gateway link\n")
        # for i, auth in enumerate(authorities, start=1):
        #     auth_ip = f"192.168.100.{i}"
        #     gw_ip = f"192.168.100.{10 + i}"  

        #     auth.setIP(auth_ip + '/24', intf=f'{auth.name}-eth1')
        #     gateway.setIP(gw_ip + '/24', intf=f'gw-eth{i}')

        # Configure internet access if enabled
        if args.internet and bridge:
            configure_internet_access(net, authorities, gateway, bridge)
        
        # Start FastPay services on all nodes
        info("*** Starting FastPay services on all nodes\n")
        for auth in authorities:
            auth.start_fastpay_services(args.internet)
        
        setup_test_accounts(authorities, clients)

        for client in clients:
            client.start_fastpay_services()
        
        if gateway:
            gateway.start_gateway_services()
        
        # Wait for mesh to stabilize
        info("*** Waiting for mesh network to stabilize\n")
        time.sleep(5)

        # Flash-Mesh D-SDN controller (optional)
        if args.flashmesh:
            info("*** Enabling Flash-Mesh D-SDN controller\n")
            qos_mgr = QoSManager()
            all_nodes = list(authorities) + list(clients)
            for node in all_nodes:
                qos_mgr.install_priority(node)
            info(f"   ✅ QoS installed on {len(all_nodes)} nodes\n")

            link_stats = LinkStatsCollector(all_nodes, interval_ms=500)
            link_stats.start()
            info("   ✅ Link stats collector started (500ms interval)\n")

            fallback = FallbackProfile()
            fallback.set_managed_nodes(all_nodes)
            info("   ✅ Fallback profile configured\n")
        
        # Open xterms if requested
        if args.logs:
            _open_xterms(authorities, clients)
        
        # Start interactive CLI using standard FastPay CLI
        info("*** Starting Enhanced FastPay CLI\n")
        cli = MeshPayCLI(
            net, authorities, clients, gateway,
            link_stats=link_stats, qos_mgr=qos_mgr,
        )
        print("\n" + "=" * 70)
        print("🕸️  Enhanced FastPay IEEE 802.11s Mesh Network Ready!")
        if args.internet:
            print(f"🌐 Internet Gateway Bridge: http://10.0.0.254:{args.gateway_port}")
        print("=" * 70)
        print("Available commands:")
        print("  help_fastpay             - Show all FastPay commands")
        print("  help                     - Show all commands")
        if args.internet:
            print("  Test gateway bridge:")
            print(f"    curl http://10.0.0.254:{args.gateway_port}/authorities")
            print(f"    curl http://10.0.0.254:{args.gateway_port}/health")
            print(f"    curl http://10.0.0.254:{args.gateway_port}/shards")
        print("=" * 70)
        print("Type 'help' for more commands or Ctrl-D to exit.\n")
        
        cli.cmdloop()
        
    except KeyboardInterrupt:
        info("\n*** Interrupted by user\n")
    except Exception as e:
        info(f"*** Error: {e}\n")
        import traceback
        traceback.print_exc()
    finally:
        if link_stats:
            link_stats.stop()
        if bridge:
            bridge.stop_bridge_server()
        if net is not None:
            info("*** Stopping enhanced mesh network\n")
            net.stop()
            if args.logs:
                _close_xterms(authorities, clients)
        info("*** Enhanced mesh demo completed\n")


if __name__ == "__main__":
    main() 