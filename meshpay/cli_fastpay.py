from __future__ import annotations

"""Interactive Command-Line Interface helpers for MeshPay Wi-Fi simulations.

This module is **imported** by example scripts under :pymod:`mn_wifi.examples` and
implements the small REPL that operators can use to test a MeshPay network
running inside *Mininet-WiFi*.

The CLI supports the following high-level commands:

1. ``ping <src> <dst>`` – ICMP reachability test between two nodes in the
   topology.
2. ``balance <user>`` or ``balances`` – Show the balance of a single user or of
   all predefined users across *all* authorities.
3. ``initiate <sender> <recipient> <amount>`` – Create a *TransferOrder* but do
   **not** broadcast it yet.
4. ``sign <order-id> <user>`` – Attach a dummy signature to the selected
   *TransferOrder*.
5. ``broadcast <order-id>`` – Send the signed *TransferOrder* to every
   authority and report whether the 2/3 + 1 quorum accepted the transfer.

The CLI was deliberately kept *stateless* regarding Mininet – it only needs
lists of authority and client nodes which are passed in by the example script.

This class now inherits from mn_wifi.cli.CLI to provide access to all base
Mininet-WiFi CLI commands (stop, start, distance, dpctl) in addition to
MeshPay-specific commands.
"""

from dataclasses import asdict
import json
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from meshpay.types import (
    TransferOrder,
)
from mn_wifi.cli import CLI
from meshpay.nodes.client import Client
from mn_wifi.node import Station, Node_wifi
from mn_wifi.services.core.config import SUPPORTED_TOKENS

# --------------------------------------------------------------------------------------
# Public helpers
# --------------------------------------------------------------------------------------


class MeshPayCLI(CLI):  # pylint: disable=too-many-instance-attributes
    """Small interactive shell to operate a MeshPay Wi-Fi network.
    
    Inherits from mn_wifi.cli.CLI to provide access to all base Mininet-WiFi
    commands while adding MeshPay-specific functionality.
    """
    
    prompt = 'MeshPay> '

    def __init__(
        self,
        mn_wifi,
        authorities: List[Station],
        clients: List[Client],
        gateway: Optional[Node_wifi] = None,
        *,
        link_stats=None,
        qos_mgr=None,
        quorum_ratio: float = 2 / 3,
        stdin=sys.stdin,
        script=None,
        cmd=None,
    ) -> None:
        """Create the CLI helper.

        Args:
            mn_wifi: The Mininet-WiFi network instance.
            authorities: List of authority nodes participating in the committee.
            clients: Client stations (e.g. *user1*, *user2* …).
            quorum_ratio: Fraction of authorities that must accept a transfer in
                order to reach finality.  The default replicates MeshPay's
                *2/3 + 1* rule.
            stdin: Input stream for CLI.
            script: Script file to execute.
            cmd: Single command to execute.
        """

        self.authorities = authorities
        self.clients = clients
        self.gateway = gateway

        # Lookup maps and in-memory bookkeeping helpers
        self._pending_orders: Dict[uuid.UUID, TransferOrder] = {}
        self._quorum_weight = int(len(authorities) * quorum_ratio) + 1
        # Track which authorities accepted each order so that we can later
        # broadcast a ConfirmationOrder containing their signatures.
        self._order_signers: Dict[uuid.UUID, List[Station]] = {}

        # Bring client transports up so they can receive replies *before* the
        # interactive shell becomes available.
        for client in clients:
            if hasattr(client.transport, "connect"):
                client.transport.connect()  # type: ignore[attr-defined]

        # Flash-Mesh D-SDN controller references (optional)
        self._link_stats = link_stats
        self._qos_mgr = qos_mgr

        super().__init__(mn_wifi, stdin=stdin, script=script, cmd=cmd)

    def _find_node(self, name: str) -> Optional[Station]:
        """Return *any* station (authority or client) with the given *name*."""
        for node in [*self.authorities, *self.clients, self.gateway]:
            if node.name == name:
                return node
        return None


    # 1. Improved Balance Display -----------------------------------------
    def do_balance(self, line: str) -> None:
        """Print user balance in a formatted table.

        Usage: balance <user>
        """
        args = line.split()
        if len(args) != 1:
            print("Usage: balance <user>")
            return

        user = args[0]

        # Collect balances from the first authority that holds this account
        account = None
        source_auth = None
        for auth in self.authorities:
            if hasattr(auth, "state") and hasattr(auth.state, "accounts"):
                acct = auth.state.accounts.get(user)
                if acct:
                    account = acct
                    source_auth = auth.name
                    break

        if account is None:
            print(f"\n  ⚠️  No account found for '{user}' on any authority")
            return

        print(f"\n╔══════════════════════════════════════════════════════╗")
        print(f"║  💰 Balances for {user:<37}║")
        print(f"╠══════════════════════════════════════════════════════╣")
        print(f"║  Source: {source_auth:<43}║")
        print(f"║  Seq#:   {account.sequence_number:<43}║")
        print(f"╠════════╦═══════════════╦═══════════════╦═════════════╣")
        print(f"║ Token  ║  MeshPay Bal  ║  Wallet Bal   ║  Total Bal  ║")
        print(f"╠════════╬═══════════════╬═══════════════╬═════════════╣")
        for _addr, bal in account.balances.items():
            sym = getattr(bal, 'token_symbol', '???')
            mp = getattr(bal, 'meshpay_balance', 0.0)
            wb = getattr(bal, 'wallet_balance', 0.0)
            tb = getattr(bal, 'total_balance', 0.0)
            print(f"║ {sym:<6} ║ {mp:>11.3f}   ║ {wb:>11.3f}   ║ {tb:>9.3f}   ║")
        print(f"╚════════╩═══════════════╩═══════════════╩═════════════╝")
        print()

    # 2. ------------------------------------------------------------------
    def do_balances(self, line: str) -> None:
        """Show balances for all clients from all authorities.

        Usage: balances
        """
        print(f"\n╔══════════════════════════════════════════════════════════════════╗")
        print(f"║                     💰 ALL USER BALANCES                        ║")
        print(f"╚══════════════════════════════════════════════════════════════════╝")

        for client in self.clients:
            # Use first authority that has the account
            account = None
            source = None
            for auth in self.authorities:
                if hasattr(auth, "state") and hasattr(auth.state, "accounts"):
                    acct = auth.state.accounts.get(client.name)
                    if acct:
                        account = acct
                        source = auth.name
                        break

            print(f"\n  👤 {client.name}  (from {source or 'N/A'}, seq={account.sequence_number if account else '?'})")
            if account is None:
                print(f"     ⚠️  Not registered on any authority")
                continue

            print(f"     {'Token':<6}  {'MeshPay':>10}  {'Wallet':>10}  {'Total':>10}")
            print(f"     {'─'*6}  {'─'*10}  {'─'*10}  {'─'*10}")
            for _addr, bal in account.balances.items():
                sym = getattr(bal, 'token_symbol', '???')
                mp = getattr(bal, 'meshpay_balance', 0.0)
                wb = getattr(bal, 'wallet_balance', 0.0)
                tb = getattr(bal, 'total_balance', 0.0)
                print(f"     {sym:<6}  {mp:>10.3f}  {wb:>10.3f}  {tb:>10.3f}")

    # 3. ------------------------------------------------------------------
    def do_sync(self, line: str) -> None:
        """Sync client state from authorities.
        
        Usage: sync <client>
        Example: sync user1
        """
        args = line.split()
        if not args:
            print("Usage: sync <client>")
            return
        
        client_name = args[0]
        client = self._find_node(client_name)
        
        if not client:
            print(f"Error: Client '{client_name}' not found")
            return
        
        if not hasattr(client, 'state'):
            print(f"Error: Client '{client_name}' has no state")
            return
        
        print(f"Syncing {client_name}...")
        # Get balance from first authority as reference
        for auth in self.authorities:
            if hasattr(auth, "state") and hasattr(auth.state, "accounts"):
                account = auth.state.accounts.get(client_name)
                if account:
                    print(f"✓ Synced from {auth.name}: seq={account.sequence_number}")
                    break
        else:
            print("⚠️ No authority has account data for this client")

    # 4. ------------------------------------------------------------------
    def do_status(self, line: str) -> None:
        """Show network status summary.
        
        Usage: status
        """
        print("\n" + "=" * 60)
        print("NETWORK STATUS")
        print("=" * 60)
        
        print(f"\nAuthorities: {len(self.authorities)}")
        for auth in self.authorities:
            status = "🟢" if hasattr(auth, '_running') and auth._running else "⚪"
            print(f"  {status} {auth.name}")
        
        print(f"\nClients: {len(self.clients)}")
        for client in self.clients:
            status = "🟢" if hasattr(client, '_running') and client._running else "⚪"
            print(f"  {status} {client.name}")
        
        if self.gateway:
            print(f"\nGateway: {self.gateway.name}")
        
        print(f"\nCommittee:")
        print(f"  Size: {len(self.authorities)}")
        print(f"  Quorum threshold: {int(len(self.authorities) * 2/3) + 1}")

    # 5. ------------------------------------------------------------------
    def do_demo(self, line: str) -> None:
        """Run automated demo sequence.
        
        Usage: demo
        
        This will:
        1. Show initial balances
        2. Execute sample transfers
        3. Show final balances
        """
        print("\n" + "=" * 60)
        print("           MESHPAY CONSENSUS DEMO SEQUENCE")
        print("=" * 60 + "\n")
        
        # Step 1: Initial balances
        print("Step 1: Initial balances")
        self.do_balances("")
        
        # Step 2: Execute transfers
        print("\n" + "-" * 60)
        print("Step 2: Executing transfers")
        print("-" * 60 + "\n")
        
        # Get client names
        if len(self.clients) >= 2:
            sender = self.clients[0]
            recipient = self.clients[1]
            
            # Try a transfer with XTZ token
            print(f"📤 Transferring 10 XTZ from {sender.name} to {recipient.name}...")
            try:
                from meshpay.nodes.client import Client
                if hasattr(sender, 'transfer'):
                    from mn_wifi.services.core.config import SUPPORTED_TOKENS
                    xtz_token = SUPPORTED_TOKENS.get('XTZ', {}).get('address', '')
                    if xtz_token:
                        result = sender.transfer(recipient.name, xtz_token, 10)
                        if result:
                            print("  ✓ Transfer request sent!")
                        else:
                            print("  ✗ Transfer failed")
                    else:
                        print("  ⚠️ XTZ token not configured")
            except Exception as e:
                print(f"  ✗ Error: {e}")
        else:
            print("  ⚠️ Need at least 2 clients for demo transfers")
        
        # Step 3: Final balances
        print("\n" + "-" * 60)
        print("Step 3: Final balances")
        print("-" * 60)
        self.do_balances("")
        
        # Summary
        print("\n" + "=" * 60)
        print("           DEMO COMPLETE")
        print("=" * 60)
        print("\n✅ Demo sequence finished!")

    # 6a. -----------------------------------------------------------------
    def do_buffered(self, line: str) -> None:
        """Show buffered transactions awaiting quorum.
        
        Usage: buffered [client]
        
        If no client specified, shows buffered transactions for all clients.
        """
        print("\n" + "=" * 60)
        print("BUFFERED TRANSACTIONS")
        print("=" * 60)
        
        args = line.split()
        clients_to_check = self.clients
        
        if args:
            client = self._find_node(args[0])
            if client and client in self.clients:
                clients_to_check = [client]
            else:
                print(f"⚠️ Client '{args[0]}' not found")
                return
        
        total_buffered = 0
        for client in clients_to_check:
            if hasattr(client, 'get_buffered_transactions'):
                buffered = client.get_buffered_transactions()
                if buffered:
                    print(f"\n{client.name}: {len(buffered)} buffered")
                    for tx_id, tx in buffered.items():
                        print(f"  📋 TX {str(tx_id)[:8]}...")
                        print(f"     Status: {tx.status.value}")
                        print(f"     Amount: {tx.order.amount} -> {tx.order.recipient}")
                        print(f"     Retries: {tx.retry_count}")
                        print(f"     Sigs: {len(tx.signatures_received)}/{tx.signatures_required}")
                    total_buffered += len(buffered)
                else:
                    print(f"\n{client.name}: No buffered transactions")
        
        print(f"\nTotal buffered: {total_buffered}")

    # 7. ------------------------------------------------------------------
    def do_transfer(self, line: str) -> None:
        """Broadcast a transfer order using :pymeth:`mn_wifi.client.Client.transfer`.
        
        Usage: transfer <sender> <recipient> <amount>
        """
        args = line.split()
        if len(args) != 4:
            print("Usage: transfer <sender> <recipient> <token> <amount>")
            return
            
        sender = args[0]
        recipient = args[1]
        try:
            token_type = args[2]
        except IndexError:
            print("❌ Token type is required")
            return
        try:
            amount = int(args[3])
        except ValueError:
            print("❌ Amount must be an integer")
            return
        client = self._find_node(sender)
        if client is None:
            print(f"❌ Unknown client '{sender}'")
            return

        print(f"🚀 {sender} → {recipient} {amount} {token_type} ")
        try:
            token = SUPPORTED_TOKENS[token_type]
            success = client.transfer(recipient, token['address'], amount)
            if success:
                print("✅ Transfer request broadcast to authorities – awaiting quorum")
            else:
                print(f"\n⚠️  Timeout: Only {count} signatures collected. Network might be unstable.")

        except KeyError:
            print(f"❌ Unsupported token: {token_type}")
        except Exception as e:
            print(f"❌ Error during transfer: {e}")

    # 7. ------------------------------------------------------------------
    def do_infor(self, line: str) -> None:
        """Show formatted station information.

        Displays different information depending on the node role:
        - Authority: full AuthorityState (accounts, committee, shards, stake …)
        - User/Client: full ClientState  (balance, certs, pending, committee …)

        Usage: infor <station|all|authorities|users>
        """
        args = line.split()
        if len(args) != 1:
            print("Usage: infor <station|all|authorities|users>")
            return

        station_name = args[0]

        if station_name.lower() in {"all", "*"}:
            for auth in self.authorities:
                self.do_infor(auth.name)
            for client in self.clients:
                self.do_infor(client.name)
            return

        if station_name.lower() == "authorities":
            for auth in self.authorities:
                self.do_infor(auth.name)
            return

        if station_name.lower() in {"users", "clients"}:
            for client in self.clients:
                self.do_infor(client.name)
            return

        node = self._find_node(station_name)
        if node is None:
            print(f"❌ Unknown station '{station_name}'")
            return

        if not hasattr(node, "state"):
            print(f"⚠️  Node '{station_name}' has no 'state' attribute")
            return

        state = node.state
        addr = getattr(state, 'address', None)
        is_authority = hasattr(state, 'accounts')  # AuthorityState has accounts dict

        W = 62  # inner width between ║ characters

        def row(text: str) -> None:
            print(f"║  {text:<{W-2}}║")

        def sep() -> None:
            print(f"╠{'═' * W}╣")

        role_icon = "🏛️" if is_authority else "👤"
        role_label = "AUTHORITY" if is_authority else "USER"

        print(f"\n╔{'═' * W}╗")
        row(f"{role_icon}  {station_name:<10}  [{role_label}]")
        sep()

        # --- Common network info ---
        node_id = getattr(addr, 'node_id', station_name) if addr else station_name
        ip_str = f"{getattr(addr, 'ip_address', '?')}:{getattr(addr, 'port', '?')}" if addr else 'N/A'
        running = "🟢 Running" if getattr(node, '_running', False) else "⚪ Stopped"

        row(f"Node ID:       {node_id}")
        row(f"IP Address:    {ip_str}")
        row(f"Status:        {running}")

        if is_authority:
            self._print_authority_state(state, row, sep)
        else:
            self._print_client_state(state, station_name, row, sep)

        print(f"╚{'═' * W}╝")
        print()

    # ── Authority state helper ─────────────────────────────────────────
    def _print_authority_state(self, state, row, sep) -> None:
        """Display all AuthorityState fields."""
        sep()
        row("📋 AUTHORITY STATE")
        sep()

        # Stake & Balance
        row(f"Stake:         {getattr(state, 'stake', 0)}")
        row(f"Balance:       {getattr(state, 'balance', 0)}")

        # Signature
        sig = getattr(state, 'authority_signature', None)
        sig_str = sig[:16] + '…' if sig and len(sig) > 16 else (sig or 'None')
        row(f"Signature:     {sig_str}")

        # Last sync time
        sync_t = getattr(state, 'last_sync_time', 0)
        sync_str = datetime.fromtimestamp(sync_t).strftime('%Y-%m-%d %H:%M:%S') if sync_t else 'N/A'
        row(f"Last Sync:     {sync_str}")

        # Shard assignments
        shards = getattr(state, 'shard_assignments', set())
        row(f"Shards:        {len(shards)} assigned")
        if shards:
            for s in sorted(shards):
                s_short = s[:20] + '…' if len(s) > 20 else s
                row(f"  └─ {s_short}")

        # Committee members
        committee = getattr(state, 'committee_members', set())
        row(f"Committee:     {len(committee)} peers")
        if committee:
            for m in sorted(committee):
                row(f"  └─ {m}")

        sep()

        # ── Accounts ──
        accounts = getattr(state, 'accounts', {})
        row(f"📂 Held User Accounts: {len(accounts)}")
        sep()

        if not accounts:
            row("(no accounts registered)")
        else:
            for acct_name, acct in accounts.items():
                seq = getattr(acct, 'sequence_number', 0)
                last_upd = getattr(acct, 'last_update', 0)
                upd_str = datetime.fromtimestamp(last_upd).strftime('%H:%M:%S') if last_upd else 'N/A'

                row("")
                row(f"👤 {acct_name}  (seq={seq}, updated={upd_str})")
                row(f"   {'Token':<6}  {'MeshPay':>10}  {'Wallet':>10}  {'Total':>10}")
                row(f"   {'─'*6}  {'─'*10}  {'─'*10}  {'─'*10}")
                for _tok_addr, bal in acct.balances.items():
                    sym = getattr(bal, 'token_symbol', '???')
                    mp = getattr(bal, 'meshpay_balance', 0.0)
                    wb = getattr(bal, 'wallet_balance', 0.0)
                    tb = getattr(bal, 'total_balance', 0.0)
                    row(f"   {sym:<6}  {mp:>10.3f}  {wb:>10.3f}  {tb:>10.3f}")

                # ── Pending confirmation ──
                pending = getattr(acct, 'pending_confirmation', None)
                if pending:
                    txo = getattr(pending, 'transfer_order', None)
                    if txo:
                        oid = str(getattr(pending, 'order_id', '?'))[:8]
                        frm = getattr(txo, 'sender', '?')
                        to = getattr(txo, 'recipient', '?')
                        amt = getattr(txo, 'amount', 0)
                        tok = self._resolve_token_symbol(getattr(txo, 'token_address', ''))
                        sigs = len(getattr(pending, 'authority_signature', {}))
                        row(f"   ⏳ Pending: [{oid}] {frm}→{to} {amt} {tok} ({sigs} sigs)")
                    else:
                        row(f"   ⏳ Pending: (malformed)")
                else:
                    row(f"   ⏳ Pending: None")

                # ── Confirmed transfers ──
                confirmed = getattr(acct, 'confirmed_transfers', {})
                row(f"   ✅ Confirmed: {len(confirmed)} transfers")
                for cid, conf in confirmed.items():
                    txo = getattr(conf, 'transfer_order', None)
                    if txo:
                        oid = str(getattr(conf, 'order_id', '?'))[:8]
                        frm = getattr(txo, 'sender', '?')
                        to = getattr(txo, 'recipient', '?')
                        amt = getattr(txo, 'amount', 0)
                        tok = self._resolve_token_symbol(getattr(txo, 'token_address', ''))
                        status = getattr(conf, 'status', '?')
                        if hasattr(status, 'value'):
                            status = status.value
                        ts = getattr(conf, 'timestamp', 0)
                        ts_str = datetime.fromtimestamp(ts).strftime('%H:%M:%S') if ts else '?'
                        nsigs = len(getattr(conf, 'authority_signatures', []))
                        row(f"      [{oid}] {frm}→{to} {amt} {tok}")
                        row(f"              status={status} sigs={nsigs} at {ts_str}")

        # Totals
        total_confirmed = sum(
            len(getattr(acct, 'confirmed_transfers', {})) for acct in accounts.values()
        )
        row("")
        row(f"📊 Total confirmed transfers: {total_confirmed}")

    # ── Client state helper ────────────────────────────────────────────
    def _print_client_state(self, state, station_name: str, row, sep) -> None:
        """Display all ClientState fields."""
        sep()
        row("📋 CLIENT STATE")
        sep()

        # Core fields
        row(f"Seq#:          {getattr(state, 'sequence_number', 0)}")
        row(f"Balance:       {getattr(state, 'balance', 0)}")
        row(f"Stake:         {getattr(state, 'stake', 0)}")

        # Secret (masked)
        secret = getattr(state, 'secret', None)
        if secret:
            key = getattr(secret, 'private_key', '')
            sec_str = key[:8] + '…' if key and len(key) > 8 else (key or 'N/A')
        else:
            sec_str = 'N/A'
        row(f"Secret:        {sec_str}")

        sep()

        # ── Balances from authority ──
        account = None
        source_auth = None
        for auth in self.authorities:
            if hasattr(auth, "state") and hasattr(auth.state, "accounts"):
                acct = auth.state.accounts.get(station_name)
                if acct:
                    account = acct
                    source_auth = auth.name
                    break

        if account is None:
            row("⚠️  No balance data on any authority")
        else:
            row(f"💰 Balances (source: {source_auth})")
            row(f"   {'Token':<6}  {'MeshPay':>10}  {'Wallet':>10}  {'Total':>10}")
            row(f"   {'─'*6}  {'─'*10}  {'─'*10}  {'─'*10}")
            for _tok_addr, bal in account.balances.items():
                sym = getattr(bal, 'token_symbol', '???')
                mp = getattr(bal, 'meshpay_balance', 0.0)
                wb = getattr(bal, 'wallet_balance', 0.0)
                tb = getattr(bal, 'total_balance', 0.0)
                row(f"   {sym:<6}  {mp:>10.3f}  {wb:>10.3f}  {tb:>10.3f}")

        sep()

        # ── Committee / connectivity ──
        committee = getattr(state, 'committee', [])
        row(f"🌐 Committee: {len(committee)} authorities")
        for auth_state in committee:
            a_name = getattr(auth_state, 'name', '?')
            a_addr = getattr(auth_state, 'address', None)
            a_ip = f"{getattr(a_addr, 'ip_address', '?')}:{getattr(a_addr, 'port', '?')}" if a_addr else 'N/A'
            row(f"   └─ {a_name} ({a_ip})")

        sep()

        # ── Pending transfer (detailed) ──
        row("📋 Transaction Status")
        pending = getattr(state, 'pending_transfer', None)
        if pending:
            oid = str(getattr(pending, 'order_id', '?'))[:8]
            frm = getattr(pending, 'sender', '?')
            to = getattr(pending, 'recipient', '?')
            amt = getattr(pending, 'amount', 0)
            tok = self._resolve_token_symbol(getattr(pending, 'token_address', ''))
            seq = getattr(pending, 'sequence_number', '?')
            ts = getattr(pending, 'timestamp', 0)
            ts_str = datetime.fromtimestamp(ts).strftime('%H:%M:%S') if ts else '?'
            row(f"   ⏳ Pending Transfer:")
            row(f"      ID:     {oid}")
            row(f"      From:   {frm}")
            row(f"      To:     {to}")
            row(f"      Amount: {amt} {tok}")
            row(f"      Seq#:   {seq}   Time: {ts_str}")
        else:
            row(f"   ⏳ Pending Transfer: None")

        sep()

        # ── Sent certificates (detailed) ──
        sent_certs = getattr(state, 'sent_certificates', [])
        row(f"📤 Sent Certificates: {len(sent_certs)}")
        for i, cert in enumerate(sent_certs):
            txo = getattr(cert, 'transfer_order', None)
            if txo:
                oid = str(getattr(cert, 'order_id', '?'))[:8]
                frm = getattr(txo, 'sender', '?')
                to = getattr(txo, 'recipient', '?')
                amt = getattr(txo, 'amount', 0)
                tok = self._resolve_token_symbol(getattr(txo, 'token_address', ''))
                nsigs = len(getattr(cert, 'authority_signature', {}))
                ts = getattr(cert, 'timestamp', 0)
                ts_str = datetime.fromtimestamp(ts).strftime('%H:%M:%S') if ts else '?'
                row(f"   [{oid}] {frm}→{to} {amt} {tok} ({nsigs} sigs, {ts_str})")

        sep()

        # ── Received certificates (detailed) ──
        recv_certs = getattr(state, 'received_certificates', {})
        row(f"📨 Received Certificates: {len(recv_certs)}")
        for (sender, seq_num), cert in recv_certs.items():
            txo = getattr(cert, 'transfer_order', None)
            if txo:
                oid = str(getattr(cert, 'order_id', '?'))[:8]
                frm = getattr(txo, 'sender', '?')
                to = getattr(txo, 'recipient', '?')
                amt = getattr(txo, 'amount', 0)
                tok = self._resolve_token_symbol(getattr(txo, 'token_address', ''))
                nsigs = len(getattr(cert, 'authority_signature', {}))
                ts = getattr(cert, 'timestamp', 0)
                ts_str = datetime.fromtimestamp(ts).strftime('%H:%M:%S') if ts else '?'
                row(f"   [{oid}] {frm}→{to} {amt} {tok} ({nsigs} sigs, {ts_str})")

    def _resolve_token_symbol(self, token_address: str) -> str:
        """Resolve a token address to its symbol using SUPPORTED_TOKENS."""
        try:
            for symbol, info in SUPPORTED_TOKENS.items():
                if info.get('address', '') == token_address:
                    return symbol
        except Exception:
            pass
        # Fallback: short address
        if token_address:
            return token_address[:10] + '…' if len(token_address) > 10 else token_address
        return '???'

    # 8. ------------------------------------------------------------------
    def do_voting_power(self, line: str) -> None:
        """Display the *current* voting power of every authority.

        The helper derives a **relative weight** for each authority based on
        its on-chain/off-chain performance.  The reference implementation uses
        the following simplified scoring function::

            score = max(transaction_count - error_count, 0)

        The final *voting power* is the normalised score so that the sum across
        all authorities equals **1.0**.  When all scores are zero (e.g. right
        after network boot-strap) the helper falls back to an *equal weight*
        distribution.
        
        Usage: voting_power
        """

        # Gather raw statistics --------------------------------------------------
        scores: Dict[str, int] = {}
        for auth in self.authorities:
            if hasattr(auth, "get_performance_stats"):
                stats = auth.get_performance_stats()  # type: ignore[attr-defined]
                score = max(int(stats.get("transaction_count", 0)) - int(stats.get("error_count", 0)), 0)
            else:
                score = 0
            scores[auth.name] = score

        total = sum(scores.values())

        # Derive voting power (normalised) ---------------------------------------
        voting_power: Dict[str, float] = {}
        if total == 0:
            # All zeros → equal distribution
            equal = 1.0 / len(self.authorities) if self.authorities else 0.0
            voting_power = {name: equal for name in scores}
        else:
            voting_power = {name: round(score / total, 3) for name, score in scores.items()}

        # Pretty-print result ------------------------------------------------------
        print("⚖️  Current voting power (weighted by performance):")
        for name, power in voting_power.items():
            print(f"   • {name}: {power:.3f}")

    # 9. ------------------------------------------------------------------
    def do_performance(self, line: str) -> None:  # noqa: D401 – imperative form
        """Print *authority* performance metrics.

        Usage: performance <authority|all>
        """
        args = line.split()
        if len(args) != 1:
            print("Usage: performance <authority|all>")
            return

        target = args[0]

        if target.lower() in {"all", "*"}:
            for auth in self.authorities:
                self.do_performance(auth.name)
            return

        auth_node = next((a for a in self.authorities if a.name == target), None)
        if auth_node is None:
            print(f"❌ Unknown authority '{target}' – try 'voting_power' to list names")
            return

        if not hasattr(auth_node, "get_performance_stats"):
            print(f"⚠️  Authority '{target}' does not expose performance metrics")
            return

        metrics = auth_node.get_performance_stats()  # type: ignore[attr-defined]

        print(f"\n┌─────────────────────────────────────┐")
        print(f"│  📊 Performance: {target:<19}│")
        print(f"├─────────────────────┬───────────────┤")
        print(f"│  Metric             │  Value        │")
        print(f"├─────────────────────┼───────────────┤")
        for key, value in metrics.items():
            label = key.replace('_', ' ').title()
            val_str = f"{value}"
            print(f"│  {label:<19}│  {val_str:<13}│")
        print(f"└─────────────────────┴───────────────┘")

    # 10. -----------------------------------------------------------------
    def do_broadcast_confirmation(self, line: str) -> None:
        """Broadcast a transfer order using :pymeth:`mn_wifi.client.Client.transfer`.
        
        Usage: broadcast_confirmation <sender>
        """
        args = line.split()
        if len(args) != 1:
            print("Usage: broadcast_confirmation <sender>")
            return
            
        sender = args[0]
        client = self._find_node(sender)
        if client is None:
            print(f"❌ Unknown client '{sender}'")
            return
        
        print(f"🚀 {sender} → broadcast confirmation")
        try:
            client.broadcast_confirmation()
        except Exception as exc:  # pragma: no cover – defensive, should not occur
            print(f"❌ Broadcast confirmation failed: {exc}")

    # 11. -----------------------------------------------------------------
    def do_update_onchain_balance(self, line: str) -> None:
        """Update account balance.
        
        Usage: update_onchain_balance <user>"
        """
        args = line.split()
        if len(args) != 1:
            print("Usage: update_onchain_balance <user>")
            return
            
        user = args[0]
        client = self._find_node(user)
        if client is None:
            print(f"❌ Unknown client '{user}'")
            return
        
        # Handle async method properly
        try:
            import asyncio
            asyncio.run(client.update_account_balance())
            print(f"✅ Account balance updated for {user}")
        except Exception as e:
            print(f"❌ Failed to update account balance: {e}")

    # 12. -----------------------------------------------------------------
    def do_log(self, line: str) -> None:
        """Show log history for a specific node.

        Usage: log <node> [lines]
        Example: log auth1       (last 30 lines)
                 log user2 50    (last 50 lines)
        """
        args = line.split()
        if not args:
            print("Usage: log <node> [lines]")
            return

        node_name = args[0]
        num_lines = 30
        if len(args) >= 2:
            try:
                num_lines = int(args[1])
            except ValueError:
                print("⚠️  Lines must be a number, using default 30")

        node = self._find_node(node_name)
        if node is None:
            print(f"❌ Unknown node '{node_name}'")
            return

        # Determine log file path
        if node_name.startswith('auth'):
            log_path = f"/tmp/{node_name}_authority.log"
        elif node_name.startswith('user'):
            log_path = f"/tmp/{node_name}_client.log"
        else:
            log_path = f"/tmp/{node_name}.log"

        if not os.path.exists(log_path):
            print(f"⚠️  No log file found at {log_path}")
            return

        try:
            with open(log_path, 'r') as f:
                lines = f.readlines()
        except IOError as e:
            print(f"❌ Error reading log file: {e}")
            return

        total = len(lines)
        shown = lines[-num_lines:] if total > num_lines else lines

        print(f"\n╔══════════════════════════════════════════════════════════════╗")
        print(f"║  📜 Log: {node_name:<15} ({total} total, showing last {len(shown)}){' ' * max(0, 18 - len(str(total)) - len(str(len(shown))))}║")
        print(f"╠══════════════════════════════════════════════════════════════╣")
        for entry in shown:
            entry = entry.rstrip('\n')
            # Truncate long lines for display
            if len(entry) > 60:
                entry = entry[:57] + '...'
            print(f"║  {entry:<60}║")
        print(f"╚══════════════════════════════════════════════════════════════╝")
        print()

    # 13. -----------------------------------------------------------------
    def do_network_metrics(self, line: str) -> None:
        """Display network metrics (latency, bandwidth, packet-loss, connectivity).

        Usage: network_metrics <authority|all>
        """
        args = line.split()
        if len(args) != 1:
            print("Usage: network_metrics <authority|all>")
            return

        target = args[0]

        if target.lower() in {"all", "*"}:
            for auth in self.authorities:
                self.do_network_metrics(auth.name)
            return

        auth_node = next((a for a in self.authorities if a.name == target), None)
        if auth_node is None:
            print(f"❌ Unknown authority '{target}'")
            return

        collector = getattr(auth_node, 'metrics_collector', None)
        if collector is None:
            print(f"⚠️  '{target}' has no metrics collector")
            return

        nm = getattr(collector, 'network_metrics', None)

        W = 62
        def row(text: str) -> None:
            print(f"║  {text:<{W-2}}║")
        def sep() -> None:
            print(f"╠{'═' * W}╣")

        print(f"\n╔{'═' * W}╗")
        row(f"📡 Network Metrics: {target}")
        sep()

        if nm:
            row(f"{'Metric':<22} {'Value':>12}  {'Unit':>10}")
            row(f"{'─'*22} {'─'*12}  {'─'*10}")
            row(f"{'Latency':<22} {getattr(nm, 'latency', 0):>12.3f}  {'ms':>10}")
            row(f"{'Bandwidth':<22} {getattr(nm, 'bandwidth', 0):>12.3f}  {'Mbps':>10}")
            row(f"{'Packet Loss':<22} {getattr(nm, 'packet_loss', 0):>12.3f}  {'%':>10}")
            row(f"{'Connectivity Ratio':<22} {getattr(nm, 'connectivity_ratio', 0):>12.3f}  {'ratio':>10}")
            upd = getattr(nm, 'last_update', 0)
            upd_str = datetime.fromtimestamp(upd).strftime('%H:%M:%S') if upd else 'N/A'
            row(f"{'Last Update':<22} {upd_str:>12}")
        else:
            row("No global network metrics available")

        sep()

        # ── Counters ──
        row(f"Transactions:  {getattr(collector, 'transaction_count', 0)}")
        row(f"Errors:        {getattr(collector, 'error_count', 0)}")
        row(f"Syncs:         {getattr(collector, 'sync_count', 0)}")

        # ── Per-peer metrics ──
        peer_lat = getattr(collector, '_peer_latency', {})
        peer_bw = getattr(collector, '_peer_bandwidth', {})
        peer_conn = getattr(collector, '_peer_connectivity', {})
        all_peers = sorted(set(list(peer_lat.keys()) + list(peer_bw.keys()) + list(peer_conn.keys())))

        if all_peers:
            sep()
            row(f"🔗 Per-Peer Link Quality ({len(all_peers)} peers)")
            sep()
            row(f"{'Peer':<14} {'Latency(ms)':>11} {'BW(Mbps)':>10} {'Conn':>8}")
            row(f"{'─'*14} {'─'*11} {'─'*10} {'─'*8}")
            for peer in all_peers:
                lat = peer_lat.get(peer)
                bw = peer_bw.get(peer)
                conn = peer_conn.get(peer)
                lat_v = f"{lat.average:.2f}" if lat else '—'
                bw_v = f"{bw.average:.2f}" if bw else '—'
                conn_v = f"{conn.average:.2f}" if conn else '—'
                row(f"{peer:<14} {lat_v:>11} {bw_v:>10} {conn_v:>8}")

        print(f"╚{'═' * W}╝")
        print()

    def do_help_meshpay(self, line: str) -> None:
        """Show help for MeshPay-specific commands."""
        print("")
        print("╔════════════════════════════════════════════════════════════════╗")
        print("║             MESHPAY CONSENSUS CLI COMMANDS                     ║")
        print("╠════════════════════════════════════════════════════════════════╣")
        print("║  ACCOUNT COMMANDS                                              ║")
        print("║    balance <user>               - User balance (detailed)      ║")
        print("║    balances                     - All users (summary table)    ║")
        print("║    sync <client>                - Sync client state            ║")
        print("║    update_onchain_balance <user> - Update on-chain balance     ║")
        print("╠════════════════════════════════════════════════════════════════╣")
        print("║  TRANSFER COMMANDS                                             ║")
        print("║    transfer <from> <to> <tkn> <amt> - Execute a transfer       ║")
        print("║    broadcast_confirmation <sender>  - Broadcast confirmation   ║")
        print("╠════════════════════════════════════════════════════════════════╣")
        print("║  INFO & MONITORING                                             ║")
        print("║    infor <node|all|users|authorities> - Node info (role-based) ║")
        print("║    status                       - Network status summary       ║")
        print("║    buffered [client]            - Buffered transactions        ║")
        print("║    voting_power                 - Show voting power            ║")
        print("║    performance <authority|all>  - Performance metrics           ║")
        print("║    network_metrics <auth|all>   - Network metrics (per-peer)   ║")
        print("║    log <node> [lines]           - Show node log history        ║")
        print("╠════════════════════════════════════════════════════════════════╣")
        print("║  DEMO COMMANDS                                                 ║")
        print("║    demo                         - Run automated demo           ║")
        print("╠════════════════════════════════════════════════════════════════╣")
        print("║  FLASH-MESH D-SDN COMMANDS                                     ║")
        print("║    fm_status                    - Controller + QoS status       ║")
        print("║    fm_telemetry [node]          - Link stats from collector     ║")
        print("║    fm_certs [client]            - Collected BCB certificates    ║")
        print("╠════════════════════════════════════════════════════════════════╣")
        print("║  MININET-WIFI COMMANDS                                         ║")
        print("║    stop                         - Stop mobility simulation     ║")
        print("║    start                        - Start mobility simulation    ║")
        print("║    distance <sta1> <sta2>       - Distance between stations    ║")
        print("║    nodes / net / links          - Show network topology        ║")
        print("║    <node> ping <node>           - Ping between nodes           ║")
        print("║    help                         - Show all available commands  ║")
        print("╚════════════════════════════════════════════════════════════════╝")
        print("")

    # ── Flash-Mesh D-SDN commands ─────────────────────────────────────────

    def do_fm_status(self, _line: str) -> None:
        """Show Flash-Mesh D-SDN controller status."""
        print("\n─── Flash-Mesh D-SDN Status ─────────────────────────")
        if self._qos_mgr is None:
            print("  ⚠  D-SDN controller not enabled (use --flashmesh)")
            print()
            return
        installed = self._qos_mgr._installed_nodes
        print(f"  QoS nodes : {len(installed)} ({', '.join(sorted(installed)) or 'none'})")
        if self._link_stats:
            stats = self._link_stats.get_all()
            print(f"  Link stats: {len(stats)} samples")
        else:
            print("  Link stats: disabled")
        print()

    def do_fm_telemetry(self, line: str) -> None:
        """Show link-stats telemetry.  Usage: fm_telemetry [node_name]"""
        if self._link_stats is None:
            print("  ⚠  Link stats collector not enabled (use --flashmesh)")
            return
        parts = line.strip().split()
        if parts:
            sample = self._link_stats.get(parts[0])
            if sample:
                print(f"\n─── Link Stats: {sample.node_name} ─────────────────────")
                print(f"  RSSI             : {sample.rssi} dBm")
                print(f"  TX/RX bytes      : {sample.tx_bytes} / {sample.rx_bytes}")
                print(f"  Exp. throughput  : {sample.expected_throughput} Mbit/s")
                print(f"  Sampled at       : {datetime.fromtimestamp(sample.timestamp).strftime('%H:%M:%S')}")
            else:
                print(f"  No data for '{parts[0]}'")
        else:
            all_samples = self._link_stats.get_all()
            if not all_samples:
                print("  No telemetry data yet")
                return
            print("\n─── Link Telemetry (all nodes) ──────────────────────")
            print(f"  {'Node':<12} {'RSSI':>6} {'TX bytes':>12} {'RX bytes':>12} {'Tput (Mb)':>10}")
            for name, s in sorted(all_samples.items()):
                print(f"  {name:<12} {s.rssi:>5}  {s.tx_bytes:>12} {s.rx_bytes:>12} {s.expected_throughput:>9.1f}")
        print()

    def do_fm_certs(self, line: str) -> None:
        """Show collected BCB certificates.  Usage: fm_certs [client_name]"""
        targets = [c for c in self.clients if not line.strip() or c.name == line.strip()]
        if not targets:
            print(f"  Client '{line.strip()}' not found")
            return
        for client in targets:
            certs = getattr(client.state, 'sent_certificates', [])
            print(f"\n─── Certificates for {client.name} ({len(certs)} votes) ─────")
            for i, cert in enumerate(certs, 1):
                sig = getattr(cert, 'authority_signature', '?')[:20]
                ok = getattr(cert, 'success', None)
                print(f"  [{i}] sig={sig}…  success={ok}")
        print()
