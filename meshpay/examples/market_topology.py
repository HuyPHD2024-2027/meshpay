#!/usr/bin/env python3
"""Market Mesh Topology for MeshPay.

This demo creates a market simulation where:
- Sellers (Authorities) are positioned statically in a pentagon formation
- Each Seller has two wireless interfaces:
  - Interface 1: 802.11s Mesh mode (SSID 'market-backbone') for inter-seller communication
  - Interface 2: Access Point mode (SSID 'seller-N') for buyers
- Buyers (Users) wander around the market area with GaussMarkov mobility
- LogDistance propagation model simulates real signal loss

Run with root privileges:
    sudo python3 -m meshpay.meshpay.examples.market_topology --sellers 5 --buyers 4
    sudo python3 -m meshpay.meshpay.examples.market_topology --sellers 5 --buyers 4 --plot --mobility

Options:
    --sellers N         Number of seller nodes (default: 5)
    --buyers N          Number of buyer nodes (default: 4)
    --mobility          Enable GaussMarkov mobility for buyers
    --plot              Enable network visualization
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from typing import Dict, List, Optional, Tuple

from mininet.log import info, setLogLevel
from mininet.link import TCLink
from mn_wifi.link import wmediumd, mesh
from mn_wifi.wmediumdConnector import interference
from mn_wifi.net import Mininet_wifi

from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client import Client
from meshpay.cli_fastpay import FastPayCLI


# Market area configuration
MARKET_WIDTH = 1000
MARKET_HEIGHT = 1000
MARKET_CENTER_X = MARKET_WIDTH // 2
MARKET_CENTER_Y = MARKET_HEIGHT // 2


def calculate_pentagon_positions(
    center_x: float,
    center_y: float,
    radius: float,
    n_points: int = 5
) -> List[Tuple[float, float]]:
    """Calculate positions for sellers in a pentagon (or N-gon) formation.
    
    Args:
        center_x: X coordinate of center
        center_y: Y coordinate of center
        radius: Distance from center to each point
        n_points: Number of points (5 for pentagon)
    
    Returns:
        List of (x, y) positions
    """
    positions = []
    for i in range(n_points):
        # Start from top (90 degrees) and go clockwise
        angle = math.pi / 2 - (2 * math.pi * i / n_points)
        x = center_x + radius * math.cos(angle)
        y = center_y + radius * math.sin(angle)
        positions.append((int(x), int(y)))
    return positions


def create_market_topology(
    num_sellers: int = 5,
    num_buyers: int = 4,
    enable_mobility: bool = True,
    enable_plot: bool = False,
) -> Tuple[Mininet_wifi, List[WiFiAuthority], List[Client]]:
    """Create market mesh topology with dual-interface sellers.
    
    Args:
        num_sellers: Number of seller authority nodes
        num_buyers: Number of buyer client nodes
        enable_mobility: Enable GaussMarkov mobility for buyers
        enable_plot: Enable network visualization
    
    Returns:
        Tuple of (network, sellers, buyers)
    """
    info("🏪 Creating Market Mesh Topology\n")
    info(f"   Sellers: {num_sellers} (pentagon formation)\n")
    info(f"   Buyers: {num_buyers} (mobile)\n")
    info(f"   Market area: {MARKET_WIDTH}x{MARKET_HEIGHT}\n\n")
    
    # Create network with wmediumd for realistic wireless
    net = Mininet_wifi(
        link=wmediumd,
        wmediumd_mode=interference,
    )
    
    # Configure propagation model for realistic signal loss
    net.setPropagationModel(model="logDistance", exp=3.5)
    
    # Calculate seller positions (pentagon at market center)
    pentagon_radius = 150  # Distance from center
    seller_positions = calculate_pentagon_positions(
        MARKET_CENTER_X, MARKET_CENTER_Y, pentagon_radius, num_sellers
    )
    
    # Create sellers (authorities) with dual interfaces
    sellers: List[WiFiAuthority] = []
    committee = {f"seller{i}" for i in range(1, num_sellers + 1)}
    
    info("*** Creating seller nodes (dual interface)\n")
    for i in range(1, num_sellers + 1):
        name = f"seller{i}"
        x, y = seller_positions[i - 1]
        
        # Create seller with 2 wireless interfaces
        seller = net.addStation(
            name,
            cls=WiFiAuthority,
            wlans=2,  # Two wireless interfaces
            position=f"{x},{y},0",
            range=100,
            txpower=20,
            antennaGain=5,
            ip=f"10.0.0.{i}/8",
            port=5000 + i,
            committee=committee,
        )
        sellers.append(seller)
        info(f"   Created {name} at ({x}, {y}) with 2 wlans\n")
    
    # Create buyers (clients) - positioned randomly in market area
    buyers: List[Client] = []
    
    info("\n*** Creating buyer nodes (mobile)\n")
    for i in range(1, num_buyers + 1):
        name = f"buyer{i}"
        
        # Random initial position
        import random
        x = random.randint(100, MARKET_WIDTH - 100)
        y = random.randint(100, MARKET_HEIGHT - 100)
        
        buyer = net.addStation(
            name,
            cls=Client,
            position=f"{x},{y},0",
            range=50,
            txpower=15,
            antennaGain=3,
            ip=f"10.0.1.{i}/8",
            port=9000 + i,
            # Mobility parameters (for GaussMarkov)
            min_x=0,
            max_x=MARKET_WIDTH,
            min_y=0,
            max_y=MARKET_HEIGHT,
            min_v=1,
            max_v=3,
        )
        buyers.append(buyer)
        info(f"   Created {name} at ({x}, {y})\n")
    
    # Configure nodes
    info("\n*** Configuring nodes\n")
    net.configureNodes()
    
    # Create mesh backbone between sellers
    info("\n*** Creating mesh backbone (802.11s)\n")
    for seller in sellers:
        # First interface: mesh backbone
        net.addLink(
            seller,
            intf=f"{seller.name}-wlan0",
            cls=mesh,
            ssid="market-backbone",
            channel=1,
        )
        info(f"   {seller.name}-wlan0 -> mesh 'market-backbone'\n")
    
    # Configure plot if enabled
    if enable_plot:
        info("\n*** Configuring plot\n")
        net.plotGraph(
            max_x=MARKET_WIDTH,
            max_y=MARKET_HEIGHT,
        )
    
    # Configure mobility if enabled
    if enable_mobility:
        info("\n*** Configuring GaussMarkov mobility for buyers\n")
        net.setMobilityModel(
            time=0,
            model='GaussMarkov',
            velocity_mean=2,    # 2 m/s walking speed
            alpha=0.7,          # Strong correlation (smooth paths)
            variance=0.5,       # Moderate randomness
            seed=42,
        )
    
    return net, sellers, buyers


def start_services(
    sellers: List[WiFiAuthority],
    buyers: List[Client],
) -> None:
    """Start FastPay services on all nodes."""
    info("\n*** Starting FastPay services\n")
    
    # Start seller services
    for seller in sellers:
        seller.start_fastpay_services()
        info(f"   ✓ {seller.name} services started\n")
    
    # Configure buyer committees and start services
    for buyer in buyers:
        # Set committee to all sellers
        buyer.state.committee = [s.state for s in sellers]
        buyer.start_fastpay_services()
        info(f"   ✓ {buyer.name} services started\n")


def stop_services(
    sellers: List[WiFiAuthority], 
    buyers: List[Client],
) -> None:
    """Stop FastPay services on all nodes."""
    info("\n*** Stopping FastPay services\n")
    
    for buyer in buyers:
        buyer.stop_fastpay_services()
    
    for seller in sellers:
        seller.stop_fastpay_services()


def print_banner() -> None:
    """Print startup banner."""
    print("\n" + "=" * 70)
    print("               🏪  MESHPAY MARKET MESH TOPOLOGY  🏪")
    print("=" * 70)
    print("""
    This simulation creates a market with:
    
    🏪 SELLERS (Authorities):
       - Positioned in pentagon formation
       - Interface 1: Mesh backbone (market-backbone)
       - Interface 2: Access Point for buyers
    
    👤 BUYERS (Users):
       - Mobile with GaussMarkov model
       - Connect to nearest seller AP
       - Can initiate transfers
    
    💰 PAYMENT FEATURES:
       - Transactions broadcast to all sellers
       - Quorum-based consensus
       - Buffered retry when partition occurs
    
    Common Commands:
       status           - Network status
       balances         - All account balances
       transfer <from> <to> <token> <amt> - Execute transfer
       buffered         - Show buffered transactions
       seller1 ping seller3  - Test mesh connectivity
       help_fastpay     - All FastPay commands
    """)
    print("=" * 70 + "\n")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Market Mesh Topology for MeshPay",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        "--sellers", "-s",
        type=int,
        default=5,
        help="Number of seller nodes (default: 5)",
    )
    parser.add_argument(
        "--buyers", "-b",
        type=int,
        default=4,
        help="Number of buyer nodes (default: 4)",
    )
    parser.add_argument(
        "--mobility",
        action="store_true",
        help="Enable GaussMarkov mobility for buyers",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Enable network visualization",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()
    
    # Set log level
    setLogLevel("info" if not args.debug else "debug")
    
    # Print banner
    print_banner()
    
    # Create topology
    net, sellers, buyers = create_market_topology(
        num_sellers=args.sellers,
        num_buyers=args.buyers,
        enable_mobility=args.mobility,
        enable_plot=args.plot,
    )
    
    try:
        # Build and start network
        info("\n*** Building network\n")
        net.build()
        
        # Start FastPay services
        start_services(sellers, buyers)
        
        # Give time for mesh to establish
        info("\n*** Waiting for mesh to stabilize...\n")
        time.sleep(2)
        
        # Run CLI
        info("\n*** Starting FastPay CLI\n")
        FastPayCLI(
            mn_wifi=net,
            authorities=sellers,
            clients=buyers,
            gateway=None,
        )
        
    except KeyboardInterrupt:
        info("\n*** Interrupted\n")
    finally:
        # Cleanup
        stop_services(sellers, buyers)
        net.stop()


if __name__ == "__main__":
    main()
