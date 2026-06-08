#!/usr/bin/env python3

from __future__ import annotations

import shlex
import time
from pathlib import Path

from mininet.log import error, info
from mn_wifi.cli import CLI


class OppNetCLI(CLI):
    """Custom CLI for interactive opportunistic-network experiments.

    Supported command:

        sta1 send sta3 "Hello World"

    Utility commands:

        delivered
        delivered sta3
        dtnlog
        dtnlog sta1
    """

    def __init__(
        self,
        mininet,
        routing: str,
        router_file: str | Path,
        log_dir: str | Path,
        *args,
        **kwargs,
    ):
        self.routing = routing
        self.router_file = Path(router_file)
        self.log_dir = Path(log_dir)
        super().__init__(mininet, *args, **kwargs)

    def default(self, line: str):
        try:
            args = shlex.split(line)
        except ValueError as exc:
            error(f"*** Parse error: {exc}\n")
            return

        if len(args) >= 4 and args[1] == "send":
            src = args[0]
            dst = args[2]
            payload = " ".join(args[3:])
            return self.send_bundle(src, dst, payload)

        return super().default(line)

    def send_bundle(self, src: str, dst: str, payload: str) -> None:
        if src not in self.mn:
            error(f"*** Unknown source node: {src}\n")
            return

        if dst not in self.mn:
            error(f"*** Unknown destination node: {dst}\n")
            return

        src_node = self.mn.get(src)

        store = self.log_dir / "stores" / self.routing / src
        send_log = self.log_dir / "send.log"

        cmd = (
            f"python3 {shlex.quote(str(self.router_file))} "
            f"--inject "
            f"--node {shlex.quote(src)} "
            f"--dst {shlex.quote(dst)} "
            f"--payload {shlex.quote(payload)} "
            f"--store {shlex.quote(str(store))}"
        )

        output = src_node.cmd(cmd)

        with send_log.open("a", encoding="utf-8") as f:
            f.write(
                f"time={time.time():.3f} "
                f"src={src} dst={dst} payload={payload!r}\n"
            )

        if output.strip():
            info(output)
        else:
            info(f"*** Sent DTN bundle: {src} -> {dst}: {payload}\n")

    def do_delivered(self, line: str) -> None:
        """Show delivered bundles.

        Usage:
            delivered
            delivered sta3
        """

        try:
            args = shlex.split(line)
        except ValueError as exc:
            error(f"*** Parse error: {exc}\n")
            return

        if args:
            node_names = args
        else:
            node_names = [node.name for node in self.mn.stations]

        for node_name in node_names:
            if node_name not in self.mn:
                error(f"*** Unknown node: {node_name}\n")
                continue

            node = self.mn.get(node_name)
            delivered_log = (
                self.log_dir
                / "stores"
                / self.routing
                / node_name
                / "delivered.log"
            )

            info(f"\n===== {node_name} delivered.log =====\n")

            output = node.cmd(
                f"test -f {shlex.quote(str(delivered_log))} "
                f"&& cat {shlex.quote(str(delivered_log))} "
                f"|| true"
            )

            if output.strip():
                info(output)
            else:
                info("No delivered bundles\n")

    def do_dtnlog(self, line: str) -> None:
        """Show DTN daemon log tail.

        Usage:
            dtnlog
            dtnlog sta1
        """

        try:
            args = shlex.split(line)
        except ValueError as exc:
            error(f"*** Parse error: {exc}\n")
            return

        if args:
            node_names = args
        else:
            node_names = [node.name for node in self.mn.stations]

        for node_name in node_names:
            if node_name not in self.mn:
                error(f"*** Unknown node: {node_name}\n")
                continue

            log_file = self.log_dir / f"{node_name}-{self.routing}.log"

            info(f"\n===== {node_name} {self.routing} log =====\n")

            output = self.mn.get(node_name).cmd(
                f"test -f {shlex.quote(str(log_file))} "
                f"&& tail -n 40 {shlex.quote(str(log_file))} "
                f"|| true"
            )

            if output.strip():
                info(output)
            else:
                info("No daemon log\n")