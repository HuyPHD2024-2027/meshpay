from __future__ import annotations

"""Common helper functions shared by FastPay demo scripts.

The module centralises CLI argument parsing and the optional management of
*xterm* windows used to tail per-node log files.  Importing demo scripts keep
their source code tidy and avoid duplication.
"""

import argparse
from typing import List

from mininet.log import info

from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client import Client

__all__ = [
    "parse_common_args",
    "parse_mesh_internet_args",
    "open_xterms",
    "close_xterms",
]


# ---------------------------------------------------------------------------
# 1. Base/common CLI arguments ---------------------------------------------
# ---------------------------------------------------------------------------


def parse_common_args(description: str = "FastPay Wi-Fi demo") -> argparse.Namespace:
    """Options shared by every FastPay demo script.

    Provides *authorities*, *clients*, *logs*, and *plot* flags.
    """

    parser = argparse.ArgumentParser(description=description, add_help=False)

    parser.add_argument("-a", "--authorities", type=int, default=3, help="number of authorities")
    parser.add_argument("-c", "--clients", type=int, default=3, help="number of client stations")
    parser.add_argument(
        "-l",
        "--logs",
        action="store_true",
        help="open an xterm per authority and client and tail their logs",
    )
    parser.add_argument(
        "-p",
        "--plot",
        action="store_true",
        help="disable plotting the network graph (plotting is enabled by default)",
    )

    return parser


# ---------------------------------------------------------------------------
# 2. Mesh-with-Internet specific arguments ---------------------------------
# ---------------------------------------------------------------------------


def parse_mesh_internet_args(description: str = "FastPay IEEE 802.11s Mesh Demo") -> argparse.Namespace:  # noqa: D401
    """Return `argparse.Namespace` with both common and mesh-internet flags."""

    # Start with the shared options ---------------------------------------
    common_parser = parse_common_args(description)

    # Create a parent to inherit help formatting --------------------------
    full_parser = argparse.ArgumentParser(
        description=description,
        parents=[common_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Mesh/internet-specific flags ----------------------------------------
    full_parser.add_argument("--internet", "-i", action="store_true", help="enable internet gateway bridge")
    full_parser.add_argument("--gateway-port", "-g", type=int, default=8080, help="gateway HTTP bridge port (default: 8080)")
    full_parser.add_argument("--mesh-id", "-m", type=str, default="fastpay-mesh", help="mesh network identifier")
    full_parser.add_argument("--mobility", action="store_true", help="enable advanced mobility models")
    full_parser.add_argument("--no-security", action="store_true", help="disable mesh security (not recommended)")
    full_parser.add_argument("--flashmesh", action="store_true", help="enable Flash-Mesh D-SDN controller (QoS + link stats)")

    return full_parser.parse_args()


def open_xterms(authorities: List[WiFiAuthority], clients: List[Client]) -> None:
    """Spawn an *xterm* window per node so that live logs are visible.

    The function relies on each node exposing a ``logger`` attribute with
    ``start_xterm()`` and ``close_xterm()`` helpers (see *AuthorityLogger* and
    *ClientLogger*).
    """
    info("*** Opening xterms for authority and client logs\n")
    for auth in authorities:
        if hasattr(auth, "logger"):
            auth.logger.start_xterm()  # type: ignore[attr-defined]
            info(f"   → xterm for {auth.name}\n")
    for client in clients:
        if hasattr(client, "logger"):
            client.logger.start_xterm()  # type: ignore[attr-defined]
            info(f"   → xterm for {client.name}\n")


def close_xterms(authorities: List[WiFiAuthority], clients: List[Client]) -> None:
    """Close the xterm windows previously opened by :func:`open_xterms`."""
    for auth in authorities:
        if hasattr(auth, "logger"):
            auth.logger.close_xterm()  # type: ignore[attr-defined]
    for client in clients:
        if hasattr(client, "logger"):
            client.logger.close_xterm()  # type: ignore[attr-defined] 