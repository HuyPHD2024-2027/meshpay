"""
sdn_frl — Opportunistic Wireless Mesh Network with SDN + FRL
=============================================================
Subpackages:
  core        — TelemetryEngine, PerformanceLogger
  network     — Mininet-WiFi topology builder
  controller  — Ryu SD-QoS controller app
  fl          — Flower FRL server, client, RL agent
  scripts     — Helper scripts and plotting tools
"""
__version__ = "2.0.0"

from sdn_frl.core import TelemetryEngine, TelemetryPushClient  # noqa: F401
