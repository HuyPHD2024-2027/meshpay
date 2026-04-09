import socket
import json
import time
import argparse
import subprocess
import select

class CRDTMap:
    """
    A simple Last-Writer-Wins (LWW) Map CRDT for the Data Mule.
    """
    def __init__(self):
        self.state = {}
        
    def merge(self, incoming_state):
        updated = False
        for k, v in incoming_state.items():
            incoming_ts = v.get("timestamp", 0)
            local_ts = self.state.get(k, {}).get("timestamp", -1)
            if incoming_ts > local_ts:
                self.state[k] = v
                updated = True
            elif incoming_ts == local_ts:
                incoming_val_str = str(v.get("value"))
                local_val_str = str(self.state.get(k, {}).get("value"))
                if incoming_val_str > local_val_str:
                    self.state[k] = v
                    updated = True
        return updated
        
    def get_state(self):
        return self.state

def get_neighbor_density():
    """
    Simulate density-aware routing by checking how many peers are visible.
    In a real scenario, this would ping broadcast or check 'iw dev xxx station dump'.
    For Mininet-WiFi, we can check the arp table or use iw.
    We'll do a simple broadcast ping and count responses.
    """
    # Assuming subnet is 10.0.0.0/8 and broadcast is 10.255.255.255
    # or just use mininet's 10.0.0.255 if /24. Let's assume /8 for simplicity (default Mininet).
    # Ping 10.255.255.255 with -c 1 and -b for broadcast
    try:
        # We use a shortcut to check number of stations via iw command since ping -b can be flaky
        # iw dev wlan0 station dump | grep Station | wc -l
        output = subprocess.check_output(
            "iw dev $(ip -o -4 route show to default | awk '{print $5}' || echo wlan0) station dump | grep Station | wc -l", 
            shell=True, stderr=subprocess.DEVNULL)
        peers = int(output.decode('utf-8').strip())
        return peers
    except Exception:
        return 0

def main():
    parser = argparse.ArgumentParser(description="TEMPO Mule App")
    parser.add_argument("--node", type=str, required=True, help="Mule node name (e.g. sta1)")
    parser.add_argument("--ap-ips", type=str, required=True, help="Comma separated AP controller IPs")
    parser.add_argument("--port", type=int, default=9000, help="Controller UDP port")
    args = parser.parse_args()
    
    ap_ips = args.ap_ips.split(',')
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    # Allows receiving broadcast if needed later
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    
    # We will bind to a local port to receive UDP messages from APs
    sock.bind(("0.0.0.0", 0))
    
    crdt = CRDTMap()
    
    print(f"[{args.node}] Started TEMPO Data Mule. Targeting APs: {ap_ips}")
    
    dissemination_ttl = "LOW"
    
    # Add some initial local data just to have something to ferry
    crdt.merge({
        f"mule_{args.node}_status": {
            "value": "online",
            "timestamp": time.time()
        }
    })
    
    while True:
        payload_str = json.dumps(crdt.get_state())
        payload_bytes = payload_str.encode('utf-8')
        
        connected = False
        
        # 1. Attempt upload to all known APs
        for ap_ip in ap_ips:
            try:
                # Fire and forget UDP
                sock.sendto(payload_bytes, (ap_ip, args.port))
            except Exception:
                pass
                
        # 2. Wait for any response (timeout 1s)
        ready = select.select([sock], [], [], 1.0)
        if ready[0]:
            try:
                data, addr = sock.recvfrom(65535)
                # If we got a response from an AP, we are connected
                if addr[0] in ap_ips:
                    connected = True
                    incoming_payload = data.decode('utf-8')
                    try:
                        incoming_crdt = json.loads(incoming_payload)
                        updated = crdt.merge(incoming_crdt)
                        if updated:
                            print(f"[{args.node}] Synced CRDT from AP {addr[0]}.")
                    except json.JSONDecodeError:
                        pass
            except BlockingIOError:
                pass
                
        # 3. Density-Aware Fallback if isolated
        if not connected:
            density = get_neighbor_density()
            if density < 3:
                dissemination_ttl = "HIGH"
            else:
                dissemination_ttl = "LOW"
                
            print(f"[{args.node}] ISOLATED MODE. Neighbor density: {density} -> Setting TTL to {dissemination_ttl}")
        else:
            print(f"[{args.node}] CONNECTED MODE. CRDT synced.")
            
        time.sleep(2)

if __name__ == "__main__":
    main()
