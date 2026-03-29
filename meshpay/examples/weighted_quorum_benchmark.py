#!/usr/bin/env python3
from __future__ import annotations

"""Weighted quorum benchmark for MeshPay offline payments.

This experiment compares equal-weight vs weighted membership quorums in an
IEEE 802.11s mesh using Mininet-WiFi. It measures end-to-end latency to
finality (from transfer broadcast to confirmation quorum) and throughput in
confirmed TPS.

Key features:
- Build a parameterized mesh with N authorities and M clients
- Toggle between equal-weight and weighted quorum modes
- Periodically recompute dynamic voting power from authority performance stats
- Record latency to finality and throughput (confirmed TPS)
- Export summary JSON/CSV and raw latency samples for plotting

Usage (requires root privileges):
    sudo python3 -m meshpay.examples.weighted_quorum_benchmark \
        --authorities 5 --clients 8 --duration 30 --rate 1.0 \
        --weight-mode weighted --range 60 \
        --json results/weighted.json --csv results/weighted.csv \
        --latencies results/weighted_latencies.csv
"""

import argparse
import os
import random
import threading
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple
from uuid import UUID, uuid4

from mininet.log import info, setLogLevel
from mn_wifi.link import mesh
from mn_wifi.net import Mininet_wifi

from mn_wifi.committee import Committee
from mn_wifi.mesh_metrics import MeshMetrics

from meshpay.consensus.weighted_quorum import (
    has_equal_quorum,
    has_weighted_quorum,
    required_equal_quorum_count,
)
from meshpay.messages import TransferResponseMessage
from meshpay.nodes.authority import WiFiAuthority
from meshpay.nodes.client import Client
from meshpay.transport import TransportKind
from mn_wifi.services.core.config import SUPPORTED_TOKENS


class WeightedBenchClient(Client):
    """Client that measures finality using equal or weighted quorum.

    The client records the start time for each broadcasted transfer and only
    marks it successful once a quorum of authority certificates is gathered.
    """

    def __init__(
        self,
        *args,
        metrics: MeshMetrics,
        committee: Committee,
        weight_mode: str = "weighted",
        quorum_ratio: float = 2.0 / 3.0,
        **kwargs,
    ) -> None:
        self._metrics = metrics
        self._committee = committee
        self._weight_mode = weight_mode
        self._quorum_ratio = float(quorum_ratio)

        # Per-transaction tracking (one outstanding per client recommended)
        self._inflight_tx: Optional[UUID] = None
        self._signers: Set[str] = set()

        super().__init__(*args, **kwargs)

    def _authority_name_from_signature(self, authority_signature: Optional[str]) -> Optional[str]:
        """Derive authority name from a demo signature format.

        The reference authority signs as ``"signed_by_authority_<name>"``.
        """
        if not authority_signature:
            return None
        prefix = "signed_by_authority_"
        if authority_signature.startswith(prefix):
            return authority_signature[len(prefix) :]
        return None

    def transfer(self, recipient: str, token_address: str, amount: int) -> bool:  # type: ignore[override]
        # New transaction context
        tx_id = uuid4()
        self._inflight_tx = tx_id
        self._signers = set()
        # Approximate payload size (request)
        self._metrics.record_tx_start(tx_id, bytes_sent=180)
        return super().transfer(recipient, token_address, amount)

    def _quorum_reached(self, signers: Iterable[str]) -> bool:
        if self._weight_mode == "equal":
            return has_equal_quorum(signers, num_authorities=len(self.state.committee), ratio=self._quorum_ratio)
        return has_weighted_quorum(signers, committee=self._committee, ratio=self._quorum_ratio)

    def handle_transfer_response(self, transfer_response: TransferResponseMessage) -> bool:  # type: ignore[override]
        # Let base class do validation and bookkeeping (stores certificate)
        ok = super().handle_transfer_response(transfer_response)
        if not ok:
            return False

        # Identify signer and update signer set
        signer_name = self._authority_name_from_signature(transfer_response.authority_signature)
        if signer_name:
            self._signers.add(signer_name)

        # If quorum achieved, broadcast confirmation and mark latency to finality
        if self._inflight_tx and self._quorum_reached(self._signers):
            try:
                # Approximate response bytes (confirmation path)
                self.broadcast_confirmation()
                self._metrics.record_tx_success(self._inflight_tx, bytes_received=220)
            finally:
                # Reset inflight tracking so subsequent responses do not double-count
                self._inflight_tx = None
                self._signers.clear()
        return True


def build_mesh(
    *,
    num_authorities: int,
    num_clients: int,
    node_range: int,
    transport: TransportKind,
    weight_mode: str,
) -> Tuple[Mininet_wifi, Committee, List[WiFiAuthority], List[WeightedBenchClient]]:
    """Create a simple mesh topology and attach committee-aware clients."""
    net = Mininet_wifi()

    # Authorities ---------------------------------------------------------------------------------
    authorities: List[WiFiAuthority] = []
    committee_names = {f"auth{i}" for i in range(1, num_authorities + 1)}
    for i in range(1, num_authorities + 1):
        name = f"auth{i}"
        auth = net.addStation(
            name,
            cls=WiFiAuthority,
            committee_members=committee_names - {name},
            ip=f"10.0.0.{10 + i}/8",
            port=8000 + i,
            range=node_range,
            txpower=20,
        )
        authorities.append(auth)

    # Committee with equal base rights; dynamic scores filled during runtime
    base_rights = {a.name: 1 for a in authorities}
    committee = Committee(base_rights)

    # Clients -------------------------------------------------------------------------------------
    metrics = MeshMetrics(run_label=f"weighted_mode={weight_mode}")
    clients: List[WeightedBenchClient] = []
    for i in range(1, num_clients + 1):
        name = f"user{i}"
        client = net.addStation(  # type: ignore[assignment]
            name,
            cls=WeightedBenchClient,
            metrics=metrics,
            committee=committee,
            weight_mode=weight_mode,
            transport_kind=transport,
            ip=f"10.0.0.{20 + i}/8",
            port=9000 + i,
            range=node_range,
            txpower=15,
        )
        clients.append(client)

    # Configure nodes and mesh links --------------------------------------------------------------
    net.configureNodes()
    for i in range(1, num_authorities + 1):
        net.addLink(authorities[i - 1], cls=mesh, ssid="meshNet", intf=f"auth{i}-wlan0", channel=5, ht_cap="HT40+")
    for i in range(1, num_clients + 1):
        net.addLink(clients[i - 1], cls=mesh, ssid="meshNet", intf=f"user{i}-wlan0", channel=5, ht_cap="HT40+")

    # Assign committee (all authorities) to each client ------------------------------------------
    for client in clients:
        client.state.committee = authorities

    return net, committee, authorities, clients


def pick_token_address() -> str:
    """Return a token address from the configured SUPPORTED_TOKENS mapping."""
    for symbol in ("USDC", "USDT"):
        if symbol in SUPPORTED_TOKENS:
            return SUPPORTED_TOKENS[symbol]["address"]
    return next(iter(SUPPORTED_TOKENS.values()))["address"]


def run_load(
    *,
    clients: List[WeightedBenchClient],
    duration_s: int,
    rate_per_client: float,
    token_address: str,
    amount: int,
) -> None:
    """Generate transfer load from each client for a fixed duration.

    Note: For highest fidelity finality latency, configure each client to
    send at rates that avoid too many concurrent in-flight transfers per
    client (e.g., <= 1–2 tx/s per client).
    """
    stop_at = time.time() + duration_s

    def worker(me: WeightedBenchClient) -> None:
        interval = 1.0 / max(rate_per_client, 1e-9)
        idx = int(me.name.replace("user", ""))
        rng = random.Random(idx * 1337)
        while time.time() < stop_at:
            # Choose a random recipient distinct from the sender
            candidates = [c for c in clients if c.name != me.name]
            if not candidates:
                break
            recipient = rng.choice(candidates).name
            me.transfer(recipient, token_address, amount)
            time.sleep(interval)

    threads: List[threading.Thread] = [threading.Thread(target=worker, args=(c,), daemon=True) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=duration_s + 5)


def start_committee_updater(*, committee: Committee, authorities: List[WiFiAuthority], period_s: float) -> threading.Thread:
    """Start a background thread that updates committee voting power periodically."""
    def loop() -> None:
        while True:
            try:
                for auth in authorities:
                    if hasattr(auth, "get_performance_stats"):
                        stats = auth.get_performance_stats()  # type: ignore[attr-defined]
                        committee.update_performance(auth.name, stats)
                time.sleep(max(period_s, 0.2))
            except Exception:
                # Best-effort updater; never crash the benchmark
                time.sleep(0.5)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def write_outputs(
    metrics: MeshMetrics,
    *,
    json_path: Optional[str],
    csv_path: Optional[str],
    latencies_path: Optional[str],
    explicit_duration_s: Optional[float],
) -> None:
    """Persist metrics to optional JSON/CSV outputs and raw latencies CSV."""
    if json_path:
        os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(metrics.to_json(explicit_duration_s=explicit_duration_s))
    if csv_path:
        os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
        header = ",".join(MeshMetrics.csv_header())
        row = ",".join(metrics.to_csv_row(explicit_duration_s=explicit_duration_s))
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(header + "\n")
            f.write(row + "\n")
    if latencies_path:
        os.makedirs(os.path.dirname(latencies_path) or ".", exist_ok=True)
        with open(latencies_path, "w", encoding="utf-8") as f:
            f.write("latency_ms\n")
            for v in metrics.get_latency_samples_ms():
                f.write(f"{v:.3f}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Weighted quorum MeshPay benchmark")
    parser.add_argument("--authorities", type=int, default=5, help="number of authorities")
    parser.add_argument("--clients", type=int, default=5, help="number of clients")
    parser.add_argument("--duration", type=int, default=30, help="benchmark duration (seconds)")
    parser.add_argument("--rate", type=float, default=1.0, help="transactions per second, per client")
    parser.add_argument("--range", type=int, default=58, help="transmission range for all nodes (meters)")
    parser.add_argument("--transport", type=str, choices=["tcp", "udp", "wifi_direct"], default="tcp")
    parser.add_argument("--amount", type=int, default=1, help="transfer amount per tx")
    parser.add_argument("--weight-mode", type=str, choices=["equal", "weighted"], default="weighted", help="quorum mode")
    parser.add_argument("--quorum", type=float, default=2.0 / 3.0, help="quorum ratio threshold (default 2/3)")
    parser.add_argument("--update-period", type=float, default=1.0, help="seconds between voting-power recompute")
    parser.add_argument("--json", type=str, default="results/weighted.json", help="output JSON path")
    parser.add_argument("--csv", type=str, default="results/weighted.csv", help="output CSV summary path")
    parser.add_argument("--latencies", type=str, default="results/weighted_latencies.csv", help="output raw latencies CSV path")
    parser.add_argument("--loglevel", type=str, default="info", help="mininet log level")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setLogLevel(args.loglevel)

    info("\n🚀 MeshPay Weighted Quorum Benchmark\n")
    info(f"   Authorities: {args.authorities}\n")
    info(f"   Clients: {args.clients}\n")
    info(f"   Duration: {args.duration}s\n")
    info(f"   Rate: {args.rate} tx/s/client\n")
    info(f"   Range: {args.range} m\n")
    info(f"   Transport: {args.transport}\n")
    info(f"   Weight mode: {args.weight_mode}\n")

    transport = {
        "tcp": TransportKind.TCP,
        "udp": TransportKind.UDP,
        "wifi_direct": TransportKind.WIFI_DIRECT,
    }[args.transport]

    json_out = args.json
    csv_out = args.csv
    latencies_out = args.latencies

    net = None
    try:
        net, committee, authorities, clients = build_mesh(
            num_authorities=args.authorities,
            num_clients=args.clients,
            node_range=args.range,
            transport=transport,
            weight_mode=args.weight_mode,
        )

        # Build network and start services
        info("*** Building mesh network\n")
        net.build()

        info("*** Starting authority services\n")
        for a in authorities:
            a.start_fastpay_services(False)

        info("*** Starting committee updater\n")
        start_committee_updater(committee=committee, authorities=authorities, period_s=args.update_period)

        info("*** Seeding accounts on authorities\n")
        # Import lazily to avoid heavy dependency at import-time
        from meshpay.examples.meshpay_demo import setup_test_accounts  # type: ignore[attr-defined]

        setup_test_accounts(authorities, clients)

        info("*** Starting client services\n")
        for c in clients:
            c.start_fastpay_services()

        info("*** Stabilizing mesh\n")
        time.sleep(3)

        token_address = pick_token_address()

        # Metrics are stored on the first client's metrics instance (shared)
        metrics: MeshMetrics = clients[0]._metrics  # type: ignore[attr-defined]

        t_start = time.time()
        run_load(
            clients=clients,
            duration_s=args.duration,
            rate_per_client=args.rate,
            token_address=token_address,
            amount=args.amount,
        )
        duration_s = time.time() - t_start

        info("*** Benchmark complete – computing metrics\n")
        print(metrics.to_json(explicit_duration_s=duration_s))
        write_outputs(
            metrics,
            json_path=json_out,
            csv_path=csv_out,
            latencies_path=latencies_out,
            explicit_duration_s=duration_s,
        )

    finally:
        if net is not None:
            info("*** Stopping mesh network\n")
            try:
                net.stop()
            except Exception:  # pragma: no cover – defensive cleanup
                pass


if __name__ == "__main__":
    main()
