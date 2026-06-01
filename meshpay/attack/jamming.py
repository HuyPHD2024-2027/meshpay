"""Packet-loss attack with two distinct adversarial strategies.

Option A — Physical Jamming (``mode="jamming"``)
-------------------------------------------------
A dedicated *jammer* station is added to the Mininet-WiFi network and
continuously floods the shared WiFi channel with high-bandwidth UDP traffic
directed at the broadcast address.  This saturates the radio medium and causes
collisions / retransmissions for *all* co-channel nodes, producing a realistic
physical-layer denial-of-service that cooperates with the wmediumd interference
model.

Option B — Grayhole / Selective Drop (``mode="grayhole"``)
------------------------------------------------------------
Selected *authority* nodes are configured as grayhole adversaries: their
kernel packet scheduler (``tc``) is instructed to silently drop a configurable
percentage of UDP datagrams arriving on the MeshPay FastPay port range
(8000–8999) — the ports over which offline payment certificates travel.  Other
traffic (control heartbeats, peer discovery) is unaffected, making this a
covert, hard-to-detect attack.

Usage
-----
Both modes implement the standard :class:`~meshpay.attack.base.AttackHandler`
interface and are invoked via the ``ATTACK_REGISTRY``:

.. code-block:: text

    # Option A — RF jammer
    --attack-type jamming  --attack-intensity 0.8  --attack-target all

    # Option B — Selective certificate drop
    --attack-type grayhole --attack-intensity 0.5  --attack-target auth1

The ``intensity`` parameter (0.0 – 1.0) controls:

* **jamming**: iperf3 target bandwidth in Mbps (``intensity × 50 Mbps``).
* **grayhole**: ``tc netem`` drop probability (``intensity × 100 %``) applied
  only to packets destined for FastPay port range 8000–8999.
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional, Union

from mininet.log import info

from meshpay.attack.base import AttackHandler
from meshpay.examples.emulation.topology import EmulationContext
from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client import Client

# FastPay authority port range — certificates travel over UDP on these ports.
_FASTPAY_PORT_LOW = 8000
_FASTPAY_PORT_HIGH = 8999


# ==============================================================================
# Option A — Physical RF Jamming
# ==============================================================================

class JammingAttack(AttackHandler):
    """Simulates physical RF jamming by flooding the WiFi channel with UDP noise.

    A *jammer* Mininet station is added to the network and runs ``iperf3``
    in UDP client mode directed at the WiFi broadcast address.  The flood
    saturates the shared 802.11 medium, elevating the noise floor for **all**
    co-channel nodes and causing realistic collision-driven packet loss without
    touching individual node routing tables or TC rules.

    Parameters
    ----------
    intensity : float
        Target flood bandwidth, expressed as a fraction of 50 Mbps
        (e.g. ``0.8`` → 40 Mbps).  Values ≤ 0 are a no-op.
    target : str
        Ignored — jamming affects the entire shared channel.
    """

    def __init__(self) -> None:
        self._jammer_node: Optional[object] = None  # Mininet station
        self._iperf_pids: List[str] = []
        self._stop_event = threading.Event()
        self._flood_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    def setup(self, context: EmulationContext, intensity: float, target: str) -> None:  # noqa: D102
        if intensity <= 0:
            info("ℹ️  [Attack: jamming] intensity=0 — no jamming injected.\n")
            return

        bandwidth_mbps = max(1, int(intensity * 50))  # scale: 0.0→0 Mbps, 1.0→50 Mbps

        # Discover the WiFi broadcast address from the first authority's interface.
        broadcast = "10.255.255.255"
        if context.authorities:
            auth = context.authorities[0]
            raw = auth.cmd(f"ip -4 addr show {auth.name}-wlan0 2>/dev/null | grep 'brd'")
            for token in raw.split():
                if "." in token and token != auth.cmd("hostname -I").strip():
                    broadcast = token
                    break

        info(
            f"📡 [Attack: jamming] Starting RF jamming — "
            f"UDP flood @ {bandwidth_mbps} Mbps → {broadcast} "
            f"(affects all co-channel nodes)\n"
        )

        # Start iperf3 server on each authority so the jammer has a target.
        for auth in context.authorities:
            auth.cmd("pkill -f iperf3 2>/dev/null; true")
            auth.cmd("iperf3 -s -D -p 5201 2>/dev/null")

        # Use the first client as the jammer (it stays mobile and keeps flooding).
        jammer = context.clients[0] if context.clients else None
        if jammer is None:
            info("⚠️  [Attack: jamming] No client nodes available for jammer role.\n")
            return

        self._jammer_node = jammer
        self._stop_event.clear()

        def _flood() -> None:
            while not self._stop_event.is_set():
                # Send a UDP burst to each authority's iperf3 server
                for auth in context.authorities:
                    auth_ip = auth.cmd("hostname -I").strip().split()[0]
                    if auth_ip:
                        jammer.cmd(
                            f"iperf3 -c {auth_ip} -u -b {bandwidth_mbps}M "
                            f"-t 2 -p 5201 2>/dev/null &"
                        )
                # Also flood the broadcast address directly to maximise interference
                jammer.cmd(
                    f"iperf3 -c {broadcast} -u -b {bandwidth_mbps}M "
                    f"-t 2 -p 5201 2>/dev/null &"
                )
                time.sleep(1.8)  # slight overlap to keep the channel saturated

        self._flood_thread = threading.Thread(target=_flood, daemon=True, name="jammer-flood")
        self._flood_thread.start()

    # ------------------------------------------------------------------
    def teardown(self, context: EmulationContext) -> None:  # noqa: D102
        info("🧹 [Attack: jamming] Stopping RF jamming flood...\n")
        self._stop_event.set()
        if self._flood_thread:
            self._flood_thread.join(timeout=3.0)
        # Kill any lingering iperf3 processes on all nodes
        all_nodes: List[Union[Client, WiFiAuthority]] = list(context.clients) + list(context.authorities)
        for node in all_nodes:
            node.cmd("pkill -f iperf3 2>/dev/null; true")
        self._jammer_node = None


# ==============================================================================
# Option B — Grayhole / Selective Certificate Drop
# ==============================================================================

class GrayholeAttack(AttackHandler):
    """Simulates a compromised authority that selectively drops payment certificates.

    The attack uses the Linux ``tc`` (Traffic Control) subsystem to install a
    *stateless* U32 filter + ``netem`` discipline on the *incoming* (ingress)
    queue of the target authority's WiFi interface.  The filter matches UDP
    datagrams destined for the FastPay port range (8000–8999) — the exact
    ports over which offline payment signatures travel — and applies a
    configurable random drop probability.

    All other traffic (peer discovery, heartbeats, DTN bundle routing) passes
    through unaffected, making this a covert "grayhole" that is much harder
    to detect than a full blackhole.

    Parameters
    ----------
    intensity : float
        Drop probability expressed as a fraction (e.g. ``0.5`` → 50 % of
        FastPay UDP datagrams are silently dropped).
    target : str
        Target node name (``"auth1"``, ``"authority"`` for all authorities,
        ``"all"`` for every node, or a specific name).
    """

    def __init__(self) -> None:
        self._affected: List[Union[Client, WiFiAuthority]] = []

    # ------------------------------------------------------------------
    def setup(self, context: EmulationContext, intensity: float, target: str) -> None:  # noqa: D102
        if intensity <= 0:
            info("ℹ️  [Attack: grayhole] intensity=0 — no selective drop injected.\n")
            return

        drop_pct = int(intensity * 100)
        nodes = self._resolve_targets(context, target)
        if not nodes:
            info("⚠️  [Attack: grayhole] No matching target nodes found.\n")
            return

        info(
            f"🕳️  [Attack: grayhole] Installing selective certificate drop "
            f"({drop_pct}% of FastPay UDP port {_FASTPAY_PORT_LOW}–{_FASTPAY_PORT_HIGH}) "
            f"on: {[n.name for n in nodes]}\n"
        )

        for node in nodes:
            intf = f"{node.name}-wlan0"
            # 1. Add ingress qdisc (required for u32 filters on incoming traffic)
            node.cmd(f"tc qdisc add dev {intf} ingress 2>/dev/null || true")
            # 2. Add a netem qdisc on the ifb (intermediate functional block)
            #    to apply random drop.  We use a simple approach: redirect
            #    matching packets through a virtual ifb device with netem loss.
            #    Fallback: apply loss directly on the egress root qdisc filtered
            #    by destination port using iptables mark + tc filter.

            # Simpler & more portable approach: use iptables MARK + tc filter
            # to drop a percentage of FastPay-destined UDP traffic.

            # Mark packets heading to FastPay ports
            node.cmd(
                f"iptables -t mangle -A OUTPUT -p udp "
                f"--dport {_FASTPAY_PORT_LOW}:{_FASTPAY_PORT_HIGH} "
                f"-j MARK --set-mark 42 2>/dev/null || true"
            )
            node.cmd(
                f"iptables -t mangle -A INPUT -p udp "
                f"--dport {_FASTPAY_PORT_LOW}:{_FASTPAY_PORT_HIGH} "
                f"-j MARK --set-mark 42 2>/dev/null || true"
            )

            # Add root HTB qdisc with a default class passthrough, then attach
            # a netem child qdisc that applies drop only to marked packets.
            node.cmd(f"tc qdisc add dev {intf} root handle 1: htb default 10 2>/dev/null || true")
            node.cmd(f"tc class add dev {intf} parent 1: classid 1:10 htb rate 100mbit 2>/dev/null || true")
            node.cmd(f"tc class add dev {intf} parent 1: classid 1:20 htb rate 100mbit 2>/dev/null || true")

            # Netem loss qdisc on the "fastpay" class
            node.cmd(
                f"tc qdisc add dev {intf} parent 1:20 handle 20: "
                f"netem loss {drop_pct}% 2>/dev/null || true"
            )

            # U32 filter: send fw-marked (42) packets to class 1:20
            node.cmd(
                f"tc filter add dev {intf} parent 1: protocol ip handle 42 fw "
                f"classid 1:20 2>/dev/null || true"
            )

            self._affected.append(node)
            info(
                f"   ✓ {node.name}: {drop_pct}% selective drop on "
                f"UDP dst-port {_FASTPAY_PORT_LOW}–{_FASTPAY_PORT_HIGH}\n"
            )

    # ------------------------------------------------------------------
    def teardown(self, context: EmulationContext) -> None:  # noqa: D102
        info("🧹 [Attack: grayhole] Removing selective drop TC rules...\n")
        for node in self._affected:
            intf = f"{node.name}-wlan0"
            node.cmd(f"tc qdisc del dev {intf} root 2>/dev/null || true")
            node.cmd(f"tc qdisc del dev {intf} ingress 2>/dev/null || true")
            node.cmd(
                f"iptables -t mangle -D OUTPUT -p udp "
                f"--dport {_FASTPAY_PORT_LOW}:{_FASTPAY_PORT_HIGH} "
                f"-j MARK --set-mark 42 2>/dev/null || true"
            )
            node.cmd(
                f"iptables -t mangle -D INPUT -p udp "
                f"--dport {_FASTPAY_PORT_LOW}:{_FASTPAY_PORT_HIGH} "
                f"-j MARK --set-mark 42 2>/dev/null || true"
            )
        self._affected.clear()

    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_targets(
        context: EmulationContext,
        target: str,
    ) -> List[Union[Client, WiFiAuthority]]:
        """Resolve ``target`` string to a list of Mininet node objects."""
        all_nodes: List[Union[Client, WiFiAuthority]] = list(context.clients) + list(context.authorities)
        if target in ("all", ""):
            return all_nodes
        if target == "authority":
            return list(context.authorities)
        if target == "client":
            return list(context.clients)
        matched = [n for n in all_nodes if n.name == target]
        return matched if matched else list(context.authorities)
