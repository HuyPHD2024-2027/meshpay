#!/usr/bin/env bash
# ==============================================================================
# Packet-loss benchmark: all routing protocols, seeds 20-24
# ==============================================================================

# Ensure script exits on error
set -e

# Path to the experimental results
EXP_ROOT="logs/benchmarks/packetloss_3routing_seeds20_24"
mkdir -p "$EXP_ROOT"

for routing in epidemic spray-and-wait prophet; do
  for seed in 20 21 22 23 24; do
    echo "========================================================================"
    echo "Running benchmark for routing=$routing, seed=$seed"
    echo "========================================================================"

    sudo python3 scripts/run_meshpay_benchmark_matrix.py \
      --clients 6 --authorities 4 --ranges 1000 \
      --payment-rate 10 --duration auto \
      --warmup 2 --settle-time 10 \
      --medium mesh \
      --routing "$routing" \
      --attack packetloss \
      --attack-loss-probability 0,0.25,0.5,0.8 \
      --attack-tpre 30 --attack-tatk 60 --attack-tpost 60 \
      --attack-target-count auto \
      --execute \
      --seed "$seed" \
      --output-root "${EXP_ROOT}/${routing}_loss_seed_${seed}"
  done
done

# Make logs readable by your user
sudo chown -R "$USER:$USER" "$EXP_ROOT"

echo "========================================================================"
echo "All benchmark runs completed. Results stored in $EXP_ROOT."
echo "========================================================================"
