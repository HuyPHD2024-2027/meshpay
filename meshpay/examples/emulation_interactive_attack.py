#!/usr/bin/env python3
"""MeshPay Emulation Interactive Attack Playground.

Allows manual transaction execution and dynamic attack injection with real mobility.
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Dict, Type, Union, Optional
from mininet.log import setLogLevel, info

from meshpay.examples.emulation.config import parse_args, EmulationConfig
from meshpay.examples.emulation.topology import (
    EmulationContext,
    cleanup_environment,
    create_emulation_context,
)
from meshpay.examples.emulation.runner import wait_for_peer_discovery
from meshpay.examples.meshpay_demo import setup_test_accounts
from meshpay.cli_fastpay import MeshPayCLI
from meshpay.attack import ATTACK_REGISTRY


class InteractiveAttackCLI(MeshPayCLI):
    """Subclass of MeshPayCLI that enables dynamic attack injection and stops."""

    prompt = 'MeshPay> '

    def __init__(self, context: EmulationContext, *args, **kwargs) -> None:
        self.context = context
        self.active_attack = None          # last injected handler (compat)
        self.active_attacks = []            # all currently active handlers
        super().__init__(
            context.net,
            context.authorities,
            context.clients,
            gateway=None,
            *args,
            **kwargs
        )

    def do_attack(self, line: str) -> None:
        """Inject an attack dynamically.
        Usage: attack <type> <target> <intensity>
        Example: attack packet_loss auth1 1.0
                 attack stopping auth2 1.0
        """
        args = line.split()
        if len(args) < 3:
            print("Usage: attack <type> <target> <intensity>")
            print(f"Supported attacks: {list(ATTACK_REGISTRY.keys())}")
            return

        attack_type = args[0]
        target = args[1]   # supports comma-separated e.g. "auth1,auth2,auth3"
        try:
            intensity = float(args[2])
        except ValueError:
            print("⚠️  Intensity must be a float number between 0.0 and 1.0")
            return

        if attack_type not in ATTACK_REGISTRY:
            print(f"❌ Unknown attack type '{attack_type}'. Supported: {list(ATTACK_REGISTRY.keys())}")
            return

        if self.active_attack:
            print("🧹 Stopping existing active attack first...")
            self.do_stop_attack("")

        # For comma-separated targets inject one handler per target,
        # accumulating attacks so previous nodes stay affected.
        targets = [t.strip() for t in target.split(",")]
        print(f"💥 Injecting '{attack_type}' attack on '{target}' with intensity {intensity}...")
        try:
            for t in targets:
                handler_cls = ATTACK_REGISTRY[attack_type]
                handler = handler_cls()
                handler.setup(self.context, intensity, t)
                self.active_attacks.append(handler)
            self.active_attack = self.active_attacks[-1]  # keep compat
            # Log the attack to tmp/logs/attack.log
            workspace_root = "/home/huydq/PHD2024-2027/meshpay"
            log_dir = os.path.join(workspace_root, "tmp", "logs")
            os.makedirs(log_dir, exist_ok=True)
            attack_log_path = os.path.join(log_dir, "attack.log")
            with open(attack_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}.000] [ATTACK_INJECT] Type: {attack_type}, Intensity: {intensity}, Target: {target}\n")
            print("✅ Attack injected successfully. Monitor logs using 'log <node>'.")
        except Exception as e:
            print(f"❌ Error setting up attack: {e}")

    def do_stop_attack(self, line: str) -> None:
        """Stop ALL active attacks and restore every affected node."""
        if not self.active_attack and not self.active_attacks:
            print("⚠️  No active attack is currently running.")
            return

        print("🧹 Tearing down active attack...")
        try:
            for handler in list(self.active_attacks):
                handler.teardown(self.context)
            self.active_attacks.clear()
            self.active_attack = None

            workspace_root = "/home/huydq/PHD2024-2027/meshpay"
            attack_log_path = os.path.join(workspace_root, "tmp", "logs", "attack.log")
            with open(attack_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}.000] [ATTACK_STOP]\n")
            print("✅ Active attack stopped and network restored to normal.")
        except Exception as e:
            print(f"❌ Error stopping attack: {e}")

    def do_log(self, line: str) -> None:
        """Show log history for a specific node from tmp/logs/.

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

        # Read from local workspace directory
        workspace_root = "/home/huydq/PHD2024-2027/meshpay"
        log_dir = os.path.join(workspace_root, "tmp", "logs")
        if node_name.startswith('auth'):
            log_path = os.path.join(log_dir, f"{node_name}_authority.log")
        elif node_name.startswith('user'):
            log_path = os.path.join(log_dir, f"{node_name}_client.log")
        else:
            log_path = os.path.join(log_dir, f"{node_name}.log")

        if not os.path.exists(log_path):
            print(f"⚠️  No log file found at {log_path}")
            return

        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
        except IOError as e:
            print(f"❌ Error reading log file: {e}")
            return

        print(f"--- Showing last {num_lines} lines of {node_name} log ---")
        for l in lines[-num_lines:]:
            print(l.rstrip('\n'))

    def do_exit(self, line: str) -> bool:
        """Exit the MeshPay CLI."""
        if self.active_attacks:
            print("🧹 Restoring network before exit...")
            for handler in list(self.active_attacks):
                try:
                    handler.teardown(self.context)
                except Exception:
                    pass
            self.active_attacks.clear()
            self.active_attack = None
        return True

    def do_quit(self, line: str) -> bool:
        """Exit the MeshPay CLI."""
        return self.do_exit(line)

    def do_EOF(self, line: str) -> bool:
        """Exit the MeshPay CLI on Ctrl-D."""
        print("")
        return self.do_exit(line)


def print_welcome_banner(config: EmulationConfig) -> None:
    """Print premium welcome banner."""
    print("=" * 80)
    print("🛡️   MESHPAY SD-DTN INTERACTIVE SECURITY & ATTACK TESTBED")
    print("=" * 80)
    print("🚀 Topology Spun Up Successfully under Mininet-WiFi:")
    print(f"  • Routing Protocol:   \033[92m{config.routing.upper()}\033[0m")
    print(f"  • WiFi Authorities:   {config.authorities} nodes ({config.authority_layout} layout)")
    print(f"  • WiFi Clients:       {config.clients} stations ({config.client_layout} layout)")
    print(f"  • Wireless Range:     {config.wireless_range} meters")
    print(f"  • Mobility Model:     GaussMarkov (real physical node coordinates moving)")
    print("=" * 80)
    print("Available Interactive Attack Commands:")
    print("  • \033[93mattack <type> <target> <intensity>\033[0m  - Inject a live attack scenario")
    print("  • \033[92mstop_attack\033[0m                        - Restore normal operation")
    print("  • \033[96mlog <node> [lines]\033[0m                 - View real-time node logs from local workspace")
    print("  • \033[94mhelp_fastpay\033[0m                       - See all baseline MeshPay transaction commands")
    print("=" * 80)
    print("Type 'help' for standard CLI options or 'exit' to clean up and exit.\n")


def main() -> None:
    setLogLevel("info")
    config = parse_args()
    
    if config.routing == "both" or not config.routing:
        config = config.with_routing("sdn_dtn")

    if os.getuid() != 0:
        print("❌ CRITICAL ERROR: Mininet-WiFi interactive security testbed must be run as root (sudo).")
        print("Please run:")
        print("   sudo PYTHONPATH=. python3 meshpay/examples/emulation_interactive_attack.py --routing sdn_dtn --plot")
        sys.exit(1)

    cleanup_environment()

    log_dir = "/home/huydq/PHD2024-2027/meshpay/tmp/logs"
    os.makedirs(log_dir, exist_ok=True)
    os.environ["MESHPAY_LOG_DIR"] = log_dir

    attack_log_path = os.path.join(log_dir, "attack.log")
    try:
        with open(attack_log_path, "w", encoding="utf-8") as f:
            f.write(f"=== MeshPay Emulation Interactive Attack Log ===\n")
            f.write(f"Started at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 50 + "\n\n")
    except Exception:
        pass

    context = create_emulation_context(config)

    try:
        info("*** Starting network and initializing nodes...\n")
        context.net.build()


        for auth in context.authorities:
            auth.start_fastpay_services(enable_internet=False)

        setup_test_accounts(context.authorities, context.clients)

        for client in context.clients:
            client.start_fastpay_services()

        info("*** Waiting for mesh network to stabilize and discover peers...\n")
        time.sleep(2)
        wait_for_peer_discovery(context.clients, context.authorities, config.peer_discovery_timeout)

        # Print banner
        print_welcome_banner(config)

        # Start the interactive loop in a background thread
        import threading
        cli = InteractiveAttackCLI(context)
        
        info("*** Starting interactive CLI thread...\n")
        cli_thread = threading.Thread(target=cli.cmdloop, daemon=True)
        cli_thread.start()

        # Run Matplotlib event loop on the main thread to keep the window
        # responsive. The mobility background thread drives update_2d() and
        # PlotGraph.pause() directly (draw=True captured at build time).
        if config.plot:
            import matplotlib.pyplot as plt
            info("*** Plot window is active. Close window or type 'exit' in CLI to stop.\n")
            while cli_thread.is_alive():
                try:
                    plt.pause(0.1)
                except Exception:
                    time.sleep(0.1)
        else:
            while cli_thread.is_alive():
                time.sleep(0.1)

    except KeyboardInterrupt:
        info("\n*** Interrupted by user\n")
    except Exception as e:
        info(f"*** Error: {e}\n")
    finally:
        cleanup_environment()
        info("*** Interactive Emulation testbed teardown completed successfully.\n")


if __name__ == "__main__":
    main()
