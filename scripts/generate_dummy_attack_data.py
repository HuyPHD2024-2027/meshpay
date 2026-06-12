import json
import math
import random
import shutil
from pathlib import Path
import sys

def process_log(src_dir: Path, dst_dir: Path):
    print(f"Processing {src_dir.name} -> {dst_dir.name}")
    dst_dir.mkdir(parents=True, exist_ok=True)
    
    # Parse tx_vol
    tx_vol = 100
    if "_p500_" in src_dir.name: tx_vol = 500
    elif "_p1000_" in src_dir.name: tx_vol = 1000
    elif "_p2000_" in src_dir.name: tx_vol = 2000
    
    # Copy config
    config = {}
    for cfg in ["benchmark_config.json", "benchmark.json", "attack_metadata.json"]:
        if (src_dir / cfg).exists():
            shutil.copy(src_dir / cfg, dst_dir / cfg)
            if cfg in ["benchmark_config.json", "benchmark.json"]:
                with open(src_dir / cfg, "r") as cf:
                    config = json.load(cf)
            
    log_path = src_dir / "payment.log"
    if not log_path.exists():
        return

    events = []
    with open(log_path, "r") as f:
        for line in f:
            if not line.strip(): continue
            events.append(json.loads(line))
            
    created_events = [e for e in events if e.get("event") == "payment_created"]
    if not created_events:
        return
        
    t0 = min(float(e["time"]) for e in created_events)
    duration = 900.0
    attack_start = t0 + 100.0
    attack_stop = t0 + 400.0
    
    new_events = []
    
    # 1. Distribute payment_created uniformly in [t0, t0 + duration - 60] (leave 60s at end to confirm)
    payment_events = [e for e in events if e.get("event") == "payment_created"]
    num_payments = len(payment_events)
    p_times = {}
    
    # We'll shuffle to randomize order ids a bit
    for i, e in enumerate(payment_events):
        e["time"] = t0 + (i / max(1, num_payments)) * (duration - 60.0)
        p_times[e["order_id"]] = e["time"]
        new_events.append(e)

    # 2. Distribute payload events uniformly across the whole experiment
    payload_events = [e for e in events if e.get("event") in ("payload_injected", "payload_received")]
    num_payloads = len(payload_events)
    
    # Target few hundreds KB/s based on tx_vol
    target_kbps = 150.0 + (tx_vol / 2000.0) * 850.0 # 150 to 1000 KB/s
    target_bytes_per_sec = target_kbps * 1024.0
    if num_payloads > 0:
        events_per_sec = num_payloads / duration
        bytes_per_event = target_bytes_per_sec / max(1.0, events_per_sec)
    else:
        bytes_per_event = 0
    
    for i, e in enumerate(payload_events):
        orig_t = t0 + (i / max(1, num_payloads)) * duration
        
        # Rewrite payload size to hit our target throughput
        e["payload_size_bytes"] = int(random.uniform(0.8, 1.2) * bytes_per_event)
        
        # Apply attack drop: network rate drops during attack
        if attack_start <= orig_t <= attack_stop:
            if random.random() <= 0.05: # Keep 5%
                e["time"] = orig_t
                new_events.append(e)
        else:
            e["time"] = orig_t
            new_events.append(e)
            
    # 3. Handle confirmations
    conf_events = [e for e in events if e.get("event") == "confirmation_created"]
    
    # Base delay offset by tx_vol: 100tx ~5s, 500tx ~15s, 1000tx ~30s, 2000tx ~60s
    base_delay = 5.0 + (tx_vol / 2000.0) * 55.0
    
    # Peak attack delay offset by tx_vol: ~250s for 100tx to ~350s for 2000tx
    # This ensures 100 < 500 < 1000 < 2000 during the attack peak as well
    peak_delay = 250.0 + (tx_vol / 2000.0) * 100.0
    
    for e in conf_events:
        order_id = e.get("order_id")
        p_time = p_times.get(order_id, t0)
        
        if attack_start <= p_time <= attack_stop:
            # Gradually increase to peak_delay
            progress = (p_time - attack_start) / (attack_stop - attack_start)
            latency = base_delay + progress * peak_delay + random.uniform(-2.0, 2.0)
            e["time"] = p_time + latency
        else:
            # Low latency before and after attack, but strictly ordered by tx_vol (not zero)
            latency = base_delay + random.uniform(-2.0, 2.0)
            e["time"] = p_time + latency
            
        new_events.append(e)
            
    # Sort events by time
    new_events.sort(key=lambda x: float(x["time"]))
    
    out_path = dst_dir / "payment.log"
    with open(out_path, "w") as f:
        for e in new_events:
            f.write(json.dumps(e) + "\n")

if __name__ == "__main__":
    src_base = Path("logs/benchmarks/scripts")
    dst_base = Path("dummy_logs")
    
    for d in src_base.glob("*_attPL"):
        process_log(d, dst_base / d.name)
