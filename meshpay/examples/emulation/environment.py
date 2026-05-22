"""Environment cleanup helpers for Mininet-WiFi emulation runs."""

from __future__ import annotations

import subprocess
import time

from mininet.log import info


def cleanup_environment() -> None:
    """Kill lingering node processes, wmediumd, and clean Mininet interfaces."""

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
