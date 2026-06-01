"""Mininet-WiFi topology construction, placement scenarios, and cleanup helpers for MeshPay."""

from __future__ import annotations

import os
import random
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from mininet.log import info
from mn_wifi.link import wmediumd
from mn_wifi.net import Mininet_wifi
from mn_wifi.wmediumdConnector import interference

from meshpay.examples.emulation.config import EmulationConfig
from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client import Client
from meshpay.oppnet.interfaces import InterfaceProfile, add_oppnet_link, get_interface_profile

Position = Tuple[float, float, float]


@dataclass(frozen=True)
class MobilityProfile:
    """Client mobility speed bounds used by a campaign scenario."""

    min_v: int
    max_v: int

    @property
    def label(self) -> str:
        return f"{self.min_v}-{self.max_v}"


PLACEMENT_SCENARIOS = ("uniform", "clustered", "corridor", "edge_authorities")


@dataclass
class EmulationContext:
    """Live Mininet objects for one benchmark run."""

    net: Mininet_wifi
    authorities: List[WiFiAuthority]
    clients: List[Client]
    committee: Set[str]
    client_map: Dict[str, Client]
    interface_profile: InterfaceProfile


def cleanup_environment() -> None:
    """Kill lingering node processes, wmediumd, and clean Mininet interfaces."""

    if os.getuid() != 0:
        return

    info("\n🧹 Cleaning up Mininet and wmediumd environment...\n")
    subprocess.run(
        "pkill -9 -f 'python3 -m meshpay'",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        "pkill -9 wmediumd",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        "mn -c",
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)


def _jitter(rng: random.Random, amount: float) -> float:
    return rng.uniform(-amount, amount)


def deterministic_positions(
    count: int,
    *,
    layout: str,
    seed: int,
    role: str,
    max_x: int = 200,
    max_y: int = 150,
) -> List[Position]:
    """Return stable station positions for a role/layout pair."""

    rng = random.Random(f"{seed}:{role}:{layout}:{count}:{max_x}:{max_y}")
    if count <= 0:
        return []

    layout = layout or "uniform"
    positions: List[Position] = []

    if layout == "clustered":
        centers = [(max_x * 0.32, max_y * 0.42), (max_x * 0.68, max_y * 0.58)]
        for index in range(count):
            cx, cy = centers[index % len(centers)]
            positions.append((
                max(5.0, min(max_x - 5.0, cx + _jitter(rng, 18))),
                max(5.0, min(max_y - 5.0, cy + _jitter(rng, 14))),
                0.0,
            ))
        return positions

    if layout == "corridor":
        y = max_y * 0.5
        step = max_x / float(count + 1)
        for index in range(count):
            positions.append((
                (index + 1) * step,
                max(5.0, min(max_y - 5.0, y + _jitter(rng, 10))),
                0.0,
            ))
        return positions

    if layout == "edge_authorities" and role == "authority":
        anchors = [(12.0, 12.0), (max_x - 12.0, 12.0), (12.0, max_y - 12.0), (max_x - 12.0, max_y - 12.0)]
        for index in range(count):
            ax, ay = anchors[index % len(anchors)]
            positions.append((
                max(5.0, min(max_x - 5.0, ax + _jitter(rng, 6))),
                max(5.0, min(max_y - 5.0, ay + _jitter(rng, 6))),
                0.0,
            ))
        return positions

    if layout == "edge_authorities" and role == "client":
        layout = "uniform"

    cols = max(1, int(count**0.5))
    rows = (count + cols - 1) // cols
    for index in range(count):
        col = index % cols
        row = index // cols
        x = (col + 1) * max_x / float(cols + 1)
        y = (row + 1) * max_y / float(rows + 1)
        positions.append((
            max(5.0, min(max_x - 5.0, x + _jitter(rng, 8))),
            max(5.0, min(max_y - 5.0, y + _jitter(rng, 8))),
            0.0,
        ))
    return positions


def mininet_position(position: Position) -> str:
    """Format a position tuple for Mininet-WiFi."""
    return f"{position[0]:.2f},{position[1]:.2f},{position[2]:.2f}"


def create_emulation_context(config: EmulationConfig) -> EmulationContext:
    """Configure stations, links, propagation, mobility, and optional plotting."""

    # Automatically set MESHPAY_LOG_DIR to tmp/logs/ under the project root
    workspace_root = "/home/huydq/PHD2024-2027/meshpay"
    log_dir = os.path.join(workspace_root, "tmp", "logs")
    os.makedirs(log_dir, exist_ok=True)
    os.environ["MESHPAY_LOG_DIR"] = log_dir

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
            # Mobility bounds for authority nodes — slower than clients
            min_x=config.mobility_min_x,
            max_x=config.mobility_max_x,
            min_y=config.mobility_min_y,
            max_y=config.mobility_max_y,
            min_v=1,
            max_v=2,
        )
        # Mark as mobile (same mechanism as clients) so Mininet-WiFi puts
        # authority nodes in mob_nodes rather than stat_nodes
        auth.params['initPos'] = authority_positions[i - 1]
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
        # Explicitly set initPos in params to ensure Mininet-WiFi classifies the node
        # as a mobile station ('mob_nodes') rather than a static one ('stat_nodes')
        client.params['initPos'] = client_positions[i - 1]
        clients.append(client)

    info("*** Setting up wireless propagation model\n")
    kwargs = {"model": config.propagation_model}
    if config.propagation_model == "logNormalShadowing":
        kwargs["exp"] = config.propagation_exp
        kwargs["sL"] = config.propagation_sL
    elif config.propagation_model == "logDistance":
        kwargs["exp"] = config.propagation_exp
    net.setPropagationModel(**kwargs)

    net.configureNodes()

    info(f"*** Creating {profile.name} links\n")
    for i, auth in enumerate(authorities, start=1):
        add_oppnet_link(net, auth, profile.name, intf=f"auth{i}-wlan0")
    for i, client in enumerate(clients, start=1):
        add_oppnet_link(net, client, profile.name, intf=f"user{i}-wlan0")

    if config.plot:
        info("*** Plotting mesh network\n")
        net.plotGraph(max_x=config.mobility_max_x, max_y=config.mobility_max_y)
        # plotGraph sets net.draw = True; the mobility thread picks this up
        # during build() and drives update_2d() / PlotGraph.pause() itself.

    info(f"*** Assigning mobility model ({config.mobility_model})\n")
    net.setMobilityModel(
        time=0,
        model=config.mobility_model,
        velocity_mean=config.mobility_velocity_mean,
        alpha=config.mobility_alpha,
        variance=config.mobility_variance,
        seed=config.random_seed,
        # Pass arena bounds explicitly — setMobilityModel stores them on self.*
        # (max_x/max_y/min_v/max_v) so get_mobility_params() picks them up.
        max_x=float(config.mobility_max_x),
        max_y=float(config.mobility_max_y),
        min_x=float(config.mobility_min_x),
        min_y=float(config.mobility_min_y),
        min_v=float(config.mobility_min_v),
        max_v=float(config.mobility_max_v),
    )

    for client in clients:
        client.state.committee = authorities

    return EmulationContext(
        net=net,
        authorities=authorities,
        clients=clients,
        committee=committee,
        client_map={client.name: client for client in clients},
        interface_profile=profile,
    )
