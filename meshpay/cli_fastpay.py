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
import sys
import time
import uuid
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

        super().__init__(mn_wifi, stdin=stdin, script=script, cmd=cmd)

    def _find_node(self, name: str) -> Optional[Station]:
        """Return *any* station (authority or client) with the given *name*."""
        for node in [*self.authorities, *self.clients, self.gateway]:
            if node.name == name:
                return node
        return None


    # 1. Improved Balance Display -----------------------------------------
    def do_balance(self, line: str) -> None:
        """Print user balance in a formatted table."""
        args = line.split()
        if len(args) != 1:
            print("Usage: balance <user>")
            return
            
        user = args[0]
        print(f"\nListing balances for: {user}")
        print(f"{'Token':<6} | {'MeshPay Bal':<12} | {'Total':<12}")
        print("-" * 50)

        for auth in self.authorities:
            if hasattr(auth, "state") and hasattr(auth.state, "accounts"):
                account = auth.state.accounts.get(user)
                if account:
                    for addr, bal in account.balances.items():
                        # Format: auth | symbol | mesh_balance | total_balance
                        print(f"{bal.token_symbol:<6} | {bal.meshpay_balance:<12.3f} | {bal.total_balance:<12.3f}")
                else:
                    print(f"(No account found)")
        print("")

    # 2. ------------------------------------------------------------------
    def do_balances(self, line: str) -> None:
        """Show balances for all clients from all authorities.
        
        Usage: balances
        """
        print("\n" + "=" * 60)
        print("ACCOUNT BALANCES")
        print("=" * 60)
        
        for client in self.clients:
            print(f"\n{client.name}:")
            
            for auth in self.authorities:
                if hasattr(auth, "state") and hasattr(auth.state, "accounts"):
                    account = auth.state.accounts.get(client.name)
                    if account:
                        # Show summary of balances per token
                        balance_info = []
                        for token_addr, token_bal in account.balances.items():
                            if hasattr(token_bal, 'token_symbol') and hasattr(token_bal, 'meshpay_balance'):
                                balance_info.append(f"{token_bal.token_symbol}={token_bal.meshpay_balance:.2f}")
                        balance_str = ", ".join(balance_info) if balance_info else "N/A"
                        print(f"  {auth.name}: {balance_str}, seq={account.sequence_number}")
                    else:
                        print(f"  {auth.name}: Not registered")
                else:
                    print(f"  {auth.name}: No state")

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
        """Show formatted station information and committee status."""
        args = line.split()
        if len(args) != 1:
            print("Usage: infor <station|all>")
            return
            
        station_name = args[0]

        if station_name.lower() in {"all", "authorities", "*"}:
            for auth in self.authorities:
                print(f"\n{'='*10} {auth.name} {'='*10}")
                self.do_infor(auth.name)
            return

        node = self._find_node(station_name)
        if node is None:
            print(f"❌ Unknown station '{station_name}'")
            return

        if not hasattr(node, "state"):
            print(f"⚠️  Node '{station_name}' has no 'state' attribute")
            return

        state = node.state
        
        # --- Header Section ---
        print(f"\nInformation for: {station_name}")
        print(f"{'Field':<20} | {'Value'}")
        print("-" * 50)
        
        # Displaying basic attributes from ClientState
        # Using getattr to safely handle different node types (Client vs Authority)
        addr = getattr(state, 'address', 'N/A')
        print(f"{'Node ID':<20} | {getattr(addr, 'node_id', station_name)}")
        print(f"{'IP Address':<20} | {getattr(addr, 'ip_address', 'N/A')}:{getattr(addr, 'port', 'N/A')}")
        print(f"{'Sequence Number':<20} | {getattr(state, 'sequence_number', 'N/A')}")
        print(f"{'Balance':<20} | {getattr(state, 'balance', 0)}")
        print(f"{'Stake':<20} | {getattr(state, 'stake', 0)}")
        
        # --- Committee Section ---
        committee = getattr(state, 'committee', [])
        if committee:
            print(f"\nCommittee Authorities ({len(committee)}):")
            for auth in committee:
                # Extracts "auth1" and "10.0.0.11" from the WiFiAuthority string
                print(f"  • {auth}")

        # --- Certificate/Transfer Status ---
        pending = getattr(state, 'pending_transfer', None)
        sent_certs = len(getattr(state, 'sent_certificates', []))
        recv_certs = len(getattr(state, 'received_certificates', {}))
        
        print(f"\nTransaction Status:")
        print(f"  Pending Transfer: {pending if pending else 'None'}")
        print(f"  Sent Certs:       {sent_certs}")
        print(f"  Received Certs:   {recv_certs}")
        print("")

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
        """Print *authority* performance metrics in JSON form.

        Usage: performance <authority>
        """
        args = line.split()
        if len(args) != 1:
            print("Usage: performance <authority>")
            return
            
        authority = args[0]

        # Locate authority --------------------------------------------------------
        auth_node = next((a for a in self.authorities if a.name == authority), None)
        if auth_node is None:
            print(f"❌ Unknown authority '{authority}' – try 'voting_power' to list names")
            return

        if not hasattr(auth_node, "get_performance_stats"):
            print(f"⚠️  Authority '{authority}' does not expose performance metrics")
            return

        metrics = auth_node.get_performance_stats()  # type: ignore[attr-defined]
        print(json.dumps(metrics, indent=2, default=str))

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

    def do_help_meshpay(self, line: str) -> None:
        """Show help for MeshPay-specific commands."""
        print("")
        print("╔════════════════════════════════════════════════════════════════╗")
        print("║             MESHPAY CONSENSUS CLI COMMANDS                     ║")
        print("╠════════════════════════════════════════════════════════════════╣")
        print("║  ACCOUNT COMMANDS                                              ║")
        print("║    balance <user>               - Show user balance            ║")
        print("║    balances                     - Show all balances            ║")
        print("║    sync <client>                - Sync client state            ║")
        print("║    update_onchain_balance <user>- Update on-chain balance      ║")
        print("╠════════════════════════════════════════════════════════════════╣")
        print("║  TRANSFER COMMANDS                                             ║")
        print("║    transfer <from> <to> <token> <amt> - Execute a transfer     ║")
        print("║    broadcast_confirmation <sender>    - Broadcast confirmation ║")
        print("╠════════════════════════════════════════════════════════════════╣")
        print("║  STATUS COMMANDS                                               ║")
        print("║    status                       - Network status summary       ║")
        print("║    buffered [client]            - Buffered transactions        ║")
        print("║    infor <station|all>          - Show station state (JSON)    ║")
        print("║    voting_power                 - Show voting power            ║")
        print("║    performance <authority>      - Show performance metrics     ║")
        print("╠════════════════════════════════════════════════════════════════╣")
        print("║  DEMO COMMANDS                                                 ║")
        print("║    demo                         - Run automated demo           ║")
        print("╠════════════════════════════════════════════════════════════════╣")
        print("║  MININET-WIFI COMMANDS                                         ║")
        print("║    stop                         - Stop mobility simulation     ║")
        print("║    start                        - Start mobility simulation    ║")
        print("║    distance <sta1> <sta2>       - Distance between stations    ║")
        print("║    nodes                        - List all nodes               ║")
        print("║    net                          - Show network info            ║")
        print("║    links                        - Show all links               ║")
        print("║    <node> ping <node>           - Ping between nodes           ║")
        print("║    help                         - Show all available commands  ║")
        print("╚════════════════════════════════════════════════════════════════╝")
        print("")

