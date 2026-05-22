"""Wireless interface registry for MeshPay opportunistic experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable


@dataclass(frozen=True)
class InterfaceProfile:
    """Mininet-WiFi link profile used by the MeshPay benchmark layer."""

    name: str
    link_class_path: str
    description: str
    default_params: Dict[str, Any] = field(default_factory=dict)
    requires_config_wifi_direct: bool = False
    emulation_note: str = ""

    def link_class(self) -> Any:
        module_name, class_name = self.link_class_path.rsplit(".", 1)
        module = __import__(module_name, fromlist=[class_name])
        return getattr(module, class_name)


_INTERFACE_ALIASES = {
    "mesh": "mesh_80211s",
    "80211s": "mesh_80211s",
    "adhoc": "adhoc_wifi",
    "wifi-direct": "wifi_direct",
    "wifi_direct_link": "wifi_direct",
    "wwan": "wwan_d2d",
}


_INTERFACE_PROFILES: Dict[str, InterfaceProfile] = {
    "mesh_80211s": InterfaceProfile(
        name="mesh_80211s",
        link_class_path="mn_wifi.link.mesh",
        description="IEEE 802.11s wireless mesh link",
        default_params={"ssid": "meshNet", "channel": 5, "ht_cap": "HT40+"},
    ),
    "adhoc_wifi": InterfaceProfile(
        name="adhoc_wifi",
        link_class_path="mn_wifi.link.adhoc",
        description="MANET/ad hoc Wi-Fi link",
        default_params={"ssid": "adhocNet", "mode": "g", "channel": 5, "ht_cap": "HT40+"},
    ),
    "wifi_direct": InterfaceProfile(
        name="wifi_direct",
        link_class_path="mn_wifi.link.WifiDirectLink",
        description="Mininet-WiFi Wi-Fi Direct emulation link",
        requires_config_wifi_direct=True,
    ),
    "physical_wifi_direct": InterfaceProfile(
        name="physical_wifi_direct",
        link_class_path="mn_wifi.link.PhysicalWifiDirectLink",
        description="Mininet-WiFi physical Wi-Fi Direct link",
        requires_config_wifi_direct=True,
    ),
    "wwan_d2d": InterfaceProfile(
        name="wwan_d2d",
        link_class_path="mn_wifi.wwan.link.WWANLink",
        description="MeshPay cellular/WWAN contact abstraction",
        default_params={"wwan": 0},
        emulation_note=(
            "Emulated cellular/WWAN contact layer backed by Mininet-WiFi WWAN "
            "support; this is not native 5G sidelink modeling."
        ),
    ),
}


def normalize_interface_name(name: str) -> str:
    """Normalize CLI and policy interface names."""

    value = str(name or "mesh_80211s").strip().lower().replace("-", "_")
    return _INTERFACE_ALIASES.get(value, value)


def supported_wireless_interfaces() -> Iterable[str]:
    """Return supported policy interface names."""

    return tuple(_INTERFACE_PROFILES.keys())


def get_interface_profile(name: str) -> InterfaceProfile:
    """Resolve an interface profile by policy or CLI key."""

    normalized = normalize_interface_name(name)
    profile = _INTERFACE_PROFILES.get(normalized)
    if not profile:
        supported = ", ".join(sorted(_INTERFACE_PROFILES))
        raise ValueError(f"Unsupported wireless interface: {name!r}. Supported: {supported}")
    return profile


def add_oppnet_link(net: Any, node: Any, interface_name: str, *, intf: str, **params: Any) -> Any:
    """Add a node link using a registered opportunistic interface profile."""

    profile = get_interface_profile(interface_name)
    link_params = dict(profile.default_params)
    link_params.update(params)
    cls = profile.link_class()

    if profile.name == "wwan_d2d":
        wwan_id = int(link_params.pop("wwan", 0))
        return net.addLink(node, cls=cls, wwan=wwan_id, intf=intf, **link_params)

    return net.addLink(node, cls=cls, intf=intf, **link_params)
