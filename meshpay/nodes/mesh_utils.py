import socket
import subprocess
import json
import math
import traceback
from typing import Any, Dict, List, Optional

def get_link_stats(node: Any) -> Dict[str, Any]:
    """Expose wireless link metrics for the SDN controller layer."""
    try:
        intf_names = get_wireless_interfaces(node)
        stats: Dict[str, Any] = {}
        
        for intf in intf_names:
            cmd = ["iw", "dev", intf, "station", "dump"]
            # Use node.popen to ensure it runs in the proper namespace
            proc = node.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            raw, _ = proc.communicate(timeout=1.0)
            
            if raw:
                parsed = parse_iw_station_dump(raw)
                if parsed and parsed.get("neighbor_count", 0) > 0:
                    stats.update(parsed)
                    break

        # Metrics refinement / Fallbacks
        if not stats.get("signal") or stats["signal"] == -100.0:
            stats["signal"] = get_best_rssi_fallback(node)

        if not stats.get("sinr"):
            stats["sinr"] = get_sinr_from_interfaces(node)
                
        return stats
    except Exception as e:
        if hasattr(node, "_log"):
            node._log(f"get_link_stats failed: {repr(e)}\n{traceback.format_exc()}", "error")
        return {}

def get_wireless_interfaces(node: Any) -> List[str]:
    """Discovers available wireless interfaces."""
    # 1. Check wintfs
    if hasattr(node, "wintfs"):
        if isinstance(node.wintfs, dict):
            return [intf.name for intf in node.wintfs.values()]
        return [intf.name for intf in node.wintfs]
    
    # 2. Check params
    if hasattr(node, "params") and "wlan" in node.params:
        wlans = node.params["wlan"]
        if isinstance(wlans, list):
            return [f"{node.name}-wlan{i}" for i in range(len(wlans))]
        return [f"{node.name}-wlan0"]
    
    # 3. Defaults
    name = getattr(node, "name", "unknown")
    return [f"{name}-wlan0", f"{name}-mp0", "wlan0", "mp0"]

def get_best_rssi_fallback(node: Any) -> float:
    """Fallback mechanisms for RSSI estimation."""
    # 1. Simulation layer
    if hasattr(node, "wintfs"):
        w_intfs = node.wintfs.values() if isinstance(node.wintfs, dict) else node.wintfs
        for w_intf in w_intfs:
            if getattr(w_intf, "rssi", 0) != 0:
                return float(w_intf.rssi)

    # 2. Geospatial estimation
    geo_rssi = estimate_rssi_geospatial(node)
    if geo_rssi > -100.0:
        return geo_rssi
        
    # 3. Params floor
    return float(getValue(node.params, "rssi", -100.0))

def getValue(params: Dict, key: str, default: Any) -> Any:
    """Helper for params access."""
    return params.get(key, default)

def estimate_rssi_geospatial(node: Any) -> float:
    """Geospatial Log-Distance path loss estimation."""
    my_pos = getattr(node, "position", (0, 0, 0))
    best_rssi = -100.0
    # Access p2p_connections from MeshMixin instance
    p2p = getattr(node, "p2p_connections", {})
    for peer in p2p.values():
        if peer.position:
            dist = math.sqrt((float(my_pos[0]) - float(peer.position[0]))**2 + 
                             (float(my_pos[1]) - float(peer.position[1]))**2)
            if dist < 1.0: dist = 1.0
            rssi_est = 15 - 40 * math.log10(dist) # exp=4.0, Ptx=15
            best_rssi = max(best_rssi, rssi_est)
    return best_rssi

def get_sinr_from_interfaces(node: Any) -> Optional[float]:
    """Extract SINR/SNR from interface objects."""
    if hasattr(node, "wintfs"):
        w_intfs = node.wintfs.values() if isinstance(node.wintfs, dict) else node.wintfs
        for w_intf in w_intfs:
            if hasattr(w_intf, "snr"):
                return getattr(w_intf, "snr")
    return None

def parse_iw_station_dump(raw: str) -> Dict[str, Any]:
    """Parse iw station dump into metrics dictionary."""
    aggregated = {"neighbor_count": 0, "signal": -100.0, "tx_bytes": 0, "rx_bytes": 0, "tx_retries": 0, "tx_failed": 0}
    stations = 0
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("Station"): stations += 1; continue
        if ":" not in line: continue
        
        key, _, val = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        val = val.strip().split()[0] # Strip units
        
        try:
            num = float(val) if "." in val else int(val)
            if key == "signal": aggregated["signal"] = max(aggregated["signal"], num)
            elif key in ["tx_bytes", "rx_bytes", "tx_retries", "tx_failed"]: aggregated[key] += num
            else: aggregated[key] = num
        except (ValueError, TypeError): continue
        
    if stations > 0:
        aggregated["neighbor_count"] = stations
        return aggregated
    return {}

def get_buffer_occupancy(node: Any) -> int:
    """Return the current size of the DTN message buffer."""
    return len(getattr(node, "message_buffer", {}))

def get_encounter_history(node: Any) -> List[str]:
    """Return a list of currently active neighbors."""
    if hasattr(node, "get_neighbors"):
        return list(node.get_neighbors().keys())
    return []

def update_telemetry_aggregator(node: Any, telemetry_dict: Optional[Dict[str, Any]]) -> None:
    """Update telemetry aggregator with piggybacked data."""
    if not telemetry_dict: return
    try:
        agg_ip = getattr(node, "telemetry_aggregator_ip", "127.0.0.1")
        # Default port 5005 for telemetry
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(json.dumps(telemetry_dict).encode(), (agg_ip, 5005))
    except Exception: pass
