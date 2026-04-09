import socket
import json
import hashlib
import time
import argparse
import sys
import os

# Dummy public key for Merkle-DAG crypto simulation
DUMMY_PUB_KEY_HASH = "mock_verified"

def simulate_crypto_verify(payload_str):
    """
    Simulate a Merkle-DAG cryptographical verification step to prevent Byzantine attacks.
    """
    # Simple mock: hash the payload and verify it against a dummy signature.
    # In a real system, you would check a signature provided in the payload.
    m = hashlib.sha256()
    m.update(payload_str.encode('utf-8'))
    digest = m.hexdigest()
    
    # We will just accept it and log the simulated verification.
    return True

class CRDTMap:
    """
    A simple Last-Writer-Wins (LWW) Map CRDT.
    State is stored as a dictionary of key -> {"value": val, "timestamp": ts}
    """
    def __init__(self):
        self.state = {}
        
    def merge(self, incoming_state):
        """
        Merges incoming delta-CRDT state with local state,
        favoring the highest timestamp for each key.
        Returns True if the local state was updated.
        """
        updated = False
        for k, v in incoming_state.items():
            incoming_ts = v.get("timestamp", 0)
            local_ts = self.state.get(k, {}).get("timestamp", -1)
            
            if incoming_ts > local_ts:
                self.state[k] = v
                updated = True
            elif incoming_ts == local_ts:
                # Resolve ties deterministically (e.g., by value string comparison)
                incoming_val_str = str(v.get("value"))
                local_val_str = str(self.state.get(k, {}).get("value"))
                if incoming_val_str > local_val_str:
                    self.state[k] = v
                    updated = True
        return updated
        
    def get_state(self):
        return self.state

def main():
    parser = argparse.ArgumentParser(description="TEMPO Controller App")
    parser.add_argument("--port", type=int, default=9000, help="UDP port to listen on")
    parser.add_argument("--node", type=str, required=True, help="Controller node name (e.g. ap1)")
    args = parser.parse_args()
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", args.port))
    sock.setblocking(False)
    
    print(f"[{args.node}] Controller Active. Listening on UDP port {args.port}...")
    
    crdt = CRDTMap()
    
    # Initialize some dummy data for the controller
    crdt.merge({
        f"origin_{args.node}": {
            "value": "Initial configuration",
            "timestamp": time.time()
        }
    })
    
    last_print = time.time()
    
    try:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
                payload_str = data.decode('utf-8')
                
                # Check empty packets
                if not payload_str.strip():
                    continue
                    
                print(f"[{args.node}] Received payload from {addr}")
                
                # 1. Verification phase
                if simulate_crypto_verify(payload_str):
                    print(f"[{args.node}] Payload verified crytographically.")
                else:
                    print(f"[{args.node}] Payload verification FAILED! Discarding.")
                    continue
                    
                # 2. Reconcile CRDT phase
                try:
                    incoming_crdt = json.loads(payload_str)
                    updated = crdt.merge(incoming_crdt)
                    if updated:
                        print(f"[{args.node}] Local CRDT updated via delta-CRDT from {addr}.")
                    else:
                        print(f"[{args.node}] CRDT state already up-to-date.")
                except json.JSONDecodeError:
                    print(f"[{args.node}] Invalid JSON payload received.")
                    continue
                    
                # 3. Hand back the new delta-CRDT to the Mule
                response_str = json.dumps(crdt.get_state())
                sock.sendto(response_str.encode('utf-8'), addr)
                
            except BlockingIOError:
                pass
                
            # Periodically print state
            if time.time() - last_print > 10:
                print(f"[{args.node}] Current CRDT state: {json.dumps(crdt.get_state(), indent=2)}")
                last_print = time.time()
                
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        print(f"[{args.node}] Shutting down.")
    finally:
        sock.close()

if __name__ == "__main__":
    main()
