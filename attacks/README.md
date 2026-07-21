# MeshPay Benchmark — Attack Scenarios

All commands run from the **repo root**. Requires `sudo` (Mininet-WiFi).

---

## Common parameters

| Parameter | Value | Notes |
|---|---|---|
| `--clients` | 6 | Physical station nodes |
| `--authorities` | 4 | Authority nodes |
| `--ranges` | 1000 | TX range in metres (mesh, all nodes connected) |
| `--payment-rate` | 10 | Payments per second (open-loop) |
| `--medium` | mesh | Use mesh (802.11s) medium |
| `--duration` | `auto` | Computed from `tpre + tatk + tpost + settle-time` |
| `--warmup` | 2 | Seconds before traffic starts |
| `--settle-time` | 10 | Drain window after traffic ends |
| `--attack-tpre` | 10 | Seconds before attack starts |
| `--attack-tatk` | 20 | Seconds attack is active |
| `--attack-tpost` | 10 | Seconds after attack ends |

---

## 1. Packet-loss attack — reproducibility sweep (seeds 20–24)

Replace `<ROUTING>` with `epidemic`, `spray-and-wait`, or `prophet`.  
Replace `<LABEL>` with a matching short name (e.g. `epidemic`, `saw`, `prophet`).

```bash
for seed in 20 21 22 23 24; do
  sudo python3 scripts/run_meshpay_benchmark_matrix.py \
    --clients 6 --authorities 4 --ranges 1000 \
    --payment-rate 10 --duration auto \
    --warmup 2 --settle-time 10 \
    --medium mesh \
    --routing <ROUTING> \
    --attack packetloss \
    --attack-loss-probability 0,0.25,0.5,0.8 \
    --attack-tpre 10 --attack-tatk 20 --attack-tpost 10 \
    --attack-target-count auto \
    --execute \
    --seed "$seed" \
    --output-root "logs/benchmarks/<LABEL>_seed_${seed}"
done
```

**Quick one-liners per routing:**

```bash
# Epidemic
for seed in 20 21 22 23 24; do sudo python3 scripts/run_meshpay_benchmark_matrix.py --clients 6 --authorities 4 --ranges 1000 --payment-rate 10 --duration auto --warmup 2 --settle-time 10 --medium mesh --routing epidemic --attack packetloss --attack-loss-probability 0,0.25,0.5,0.8 --attack-tpre 10 --attack-tatk 20 --attack-tpost 10 --attack-target-count auto --execute --seed "$seed" --output-root "logs/benchmarks/loss_epidemic_seed_${seed}"; done

# Spray-and-Wait
for seed in 20 21 22 23 24; do sudo python3 scripts/run_meshpay_benchmark_matrix.py --clients 6 --authorities 4 --ranges 1000 --payment-rate 10 --duration auto --warmup 2 --settle-time 10 --medium mesh --routing spray-and-wait --attack packetloss --attack-loss-probability 0,0.25,0.5,0.8 --attack-tpre 10 --attack-tatk 20 --attack-tpost 10 --attack-target-count auto --execute --seed "$seed" --output-root "logs/benchmarks/loss_saw_seed_${seed}"; done

# Prophet
for seed in 20 21 22 23 24; do sudo python3 scripts/run_meshpay_benchmark_matrix.py --clients 6 --authorities 4 --ranges 1000 --payment-rate 10 --duration auto --warmup 2 --settle-time 10 --medium mesh --routing prophet --attack packetloss --attack-loss-probability 0,0.25,0.5,0.8 --attack-tpre 10 --attack-tatk 20 --attack-tpost 10 --attack-target-count auto --execute --seed "$seed" --output-root "logs/benchmarks/loss_prophet_seed_${seed}"; done
```

---

## 2. Targeted load attack

Submits real MeshPay payments to targeted nodes at high rate.  
⚠️ **Never pass `--attack-load-rate 0`** — it defaults to 200 TPS silently.

```bash
# Seed sweep template (load-only, no packet drop)
for seed in 20 21 22 23 24; do
  sudo python3 scripts/run_meshpay_benchmark_matrix.py \
    --clients 6 --authorities 4 --ranges 1000 \
    --payment-rate 10 --duration auto \
    --warmup 2 --settle-time 10 \
    --medium mesh \
    --routing <ROUTING> \
    --attack load \
    --attack-load-rate 50 \
    --attack-tpre 10 --attack-tatk 20 --attack-tpost 10 \
    --attack-target-count auto \
    --execute \
    --seed "$seed" \
    --output-root "logs/benchmarks/targeted_load_<LABEL>_seed_${seed}"
done
```

**Quick one-liners per routing:**

```bash
# Epidemic
for seed in 20 21 22 23 24; do sudo python3 scripts/run_meshpay_benchmark_matrix.py --clients 6 --authorities 4 --ranges 1000 --payment-rate 10 --duration auto --warmup 2 --settle-time 10 --medium mesh --routing epidemic --attack load --attack-load-rate 50 --attack-tpre 10 --attack-tatk 20 --attack-tpost 10 --attack-target-count auto --execute --seed "$seed" --output-root "logs/benchmarks/targeted_load_epidemic_seed_${seed}"; done

# Spray-and-Wait
for seed in 20 21 22 23 24; do sudo python3 scripts/run_meshpay_benchmark_matrix.py --clients 6 --authorities 4 --ranges 1000 --payment-rate 10 --duration auto --warmup 2 --settle-time 10 --medium mesh --routing spray-and-wait --attack load --attack-load-rate 50 --attack-tpre 10 --attack-tatk 20 --attack-tpost 10 --attack-target-count auto --execute --seed "$seed" --output-root "logs/benchmarks/targeted_load_saw_seed_${seed}"; done

# Prophet
for seed in 20 21 22 23 24; do sudo python3 scripts/run_meshpay_benchmark_matrix.py --clients 6 --authorities 4 --ranges 1000 --payment-rate 10 --duration auto --warmup 2 --settle-time 10 --medium mesh --routing prophet --attack load --attack-load-rate 50 --attack-tpre 10 --attack-tatk 20 --attack-tpost 10 --attack-target-count auto --execute --seed "$seed" --output-root "logs/benchmarks/targeted_load_prophet_seed_${seed}"; done
```

---

## 3. No attack (baseline)

```bash
sudo python3 scripts/run_meshpay_benchmark_matrix.py \
  --clients 6 --authorities 4 --ranges 1000 \
  --payment-rate 10 --duration 50 \
  --warmup 2 --settle-time 10 \
  --medium mesh \
  --routing epidemic,spray-and-wait,prophet \
  --attack none \
  --execute \
  --output-root logs/benchmarks/baseline
```

---

## 4. Plot results

```bash
# Single seed
sudo python3 scripts/plot_loss_impact.py \
  logs/benchmarks/loss_<LABEL>_seed_<N>/summary.json \
  -o logs/benchmarks/loss_<LABEL>_seed_<N>/figures/

# Loop over seeds
for seed in 20 21 22 23 24; do
  sudo python3 scripts/plot_loss_impact.py \
    "logs/benchmarks/loss_<LABEL>_seed_${seed}/summary.json" \
    -o "logs/benchmarks/loss_<LABEL>_seed_${seed}/figures/"
done
```

---

## Attack module reference

| File | Class | `--attack` value |
|---|---|---|
| `packet_loss.py` | `PacketLossAttack` | `packetloss` |
| `targeted_load.py` | `SyntheticLoadInjector` | `load` |
| `controller.py` | `AttackController` | orchestrates both |
