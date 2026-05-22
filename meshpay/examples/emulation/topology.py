"""Mininet-WiFi topology construction for MeshPay emulation benchmarks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Set

from mininet.log import info
from mn_wifi.link import wmediumd
from mn_wifi.net import Mininet_wifi
from mn_wifi.wmediumdConnector import interference

from meshpay.examples.emulation.config import EmulationConfig
from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client import Client
from meshpay.examples.emulation.scenarios import deterministic_positions, mininet_position
from meshpay.oppnet.interfaces import InterfaceProfile, add_oppnet_link, get_interface_profile


@dataclass
class EmulationContext:
    """Live Mininet objects for one benchmark run."""

    net: Mininet_wifi
    authorities: List[WiFiAuthority]
    clients: List[Client]
    committee: Set[str]
    client_map: Dict[str, Client]
    interface_profile: InterfaceProfile


def create_emulation_context(config: EmulationConfig) -> EmulationContext:
    """Configure stations, links, propagation, mobility, and optional plotting."""

    profile = get_interface_profile(config.wireless_interface)
    info(
        f"\n🚀 Booting {config.network_mode.upper()} simulation with "
        f"{config.routing.upper()} routing over {profile.name}...\n"
    )
    if profile.emulation_note:
        info(f"*** {profile.emulation_note}\n")

    net = Mininet_wifi(
        link=wmediumd,
        wmediumd_mode=interference,
        configWiFiDirect=profile.requires_config_wifi_direct,
    )

    authorities: List[WiFiAuthority] = []
    committee = {f"auth{i}" for i in range(1, config.authorities + 1)}
    authority_positions = deterministic_positions(
        config.authorities,
        layout=config.authority_layout,
        seed=config.random_seed,
        role="authority",
        max_x=config.mobility_max_x,
        max_y=config.mobility_max_y,
    )
    client_positions = deterministic_positions(
        config.clients,
        layout=config.client_layout,
        seed=config.random_seed,
        role="client",
        max_x=config.mobility_max_x,
        max_y=config.mobility_max_y,
    )

    for i in range(1, config.authorities + 1):
        name = f"auth{i}"
        auth = net.addStation(
            name,
            cls=WiFiAuthority,
            committee_members=committee - {name},
            ip=f"10.0.0.{10 + i}/8",
            port=8000 + i,
            position=mininet_position(authority_positions[i - 1]),
            range=config.wireless_range,
            txpower=10,
            routing_protocol_name=config.routing,
            policy_file=config.policy_file or None,
            network_mode=config.network_mode,
            wireless_interface=profile.name,
        )
        authorities.append(auth)

    clients: List[Client] = []
    for i in range(1, config.clients + 1):
        name = f"user{i}"
        client = net.addStation(
            name,
            cls=Client,
            ip=f"10.0.0.{20 + i}/8",
            port=9000 + i,
            min_x=config.mobility_min_x,
            max_x=config.mobility_max_x,
            min_y=config.mobility_min_y,
            max_y=config.mobility_max_y,
            min_v=config.mobility_min_v,
            max_v=config.mobility_max_v,
            position=mininet_position(client_positions[i - 1]),
            range=config.wireless_range,
            txpower=10,
            routing_protocol_name=config.routing,
            policy_file=config.policy_file or None,
            network_mode=config.network_mode,
            wireless_interface=profile.name,
        )
        clients.append(client)

    info("*** Setting up wireless propagation model\n")
    net.setPropagationModel(model="logNormalShadowing", exp=3.5, sL=6.0)

    net.configureNodes()

    info(f"*** Creating {profile.name} links\n")
    for i, auth in enumerate(authorities, start=1):
        add_oppnet_link(net, auth, profile.name, intf=f"auth{i}-wlan0")
    for i, client in enumerate(clients, start=1):
        add_oppnet_link(net, client, profile.name, intf=f"user{i}-wlan0")

    info("*** Assigning mobility model (GaussMarkov)\n")
    net.setMobilityModel(
        time=0,
        model="GaussMarkov",
        velocity_mean=config.mobility_velocity_mean,
        alpha=config.mobility_alpha,
        variance=config.mobility_variance,
        seed=config.random_seed,
    )

    for client in clients:
        client.state.committee = authorities

    if config.plot:
        info("*** Plotting mesh network\n")
        net.plotGraph(max_x=config.mobility_max_x, max_y=config.mobility_max_y)

    return EmulationContext(
        net=net,
        authorities=authorities,
        clients=clients,
        committee=committee,
        client_map={client.name: client for client in clients},
        interface_profile=profile,
    )

