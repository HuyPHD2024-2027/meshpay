# MeshPay Routing Performance Comparison
Generated from: `summary.json`

## Configuration: Offered Load = 10.0 TPS, Packet Loss = 0%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 4.17% | 13.79% | 99.37s | 58.35s | 21.89 KB/s |
| Spray-and-Wait | 35.04% | 51.96% | 71.48s | 58.50s | 43.44 KB/s |
| PRoPHET | 22.33% | 27.38% | 109.84s | 96.21s | 28.88 KB/s |

## Configuration: Offered Load = 10.0 TPS, Packet Loss = 25%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 8.00% | 17.71% | 65.23s | 47.03s | 24.45 KB/s |
| Spray-and-Wait | 30.38% | 44.88% | 86.58s | 69.09s | 40.21 KB/s |
| PRoPHET | 27.92% | 34.58% | 122.28s | 97.45s | 35.99 KB/s |

## Configuration: Offered Load = 10.0 TPS, Packet Loss = 50%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 9.00% | 21.75% | 53.49s | 50.87s | 27.06 KB/s |
| Spray-and-Wait | 33.62% | 49.58% | 80.40s | 62.12s | 43.79 KB/s |
| PRoPHET | 23.88% | 33.67% | 112.44s | 106.57s | 36.50 KB/s |

## Configuration: Offered Load = 10.0 TPS, Packet Loss = 80%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 4.33% | 17.25% | 49.16s | 36.95s | 16.29 KB/s |
| Spray-and-Wait | 33.12% | 46.71% | 76.64s | 55.49s | 42.66 KB/s |
| PRoPHET | 23.29% | 30.67% | 83.70s | 71.98s | 38.64 KB/s |

## Configuration: Offered Load = 20.0 TPS, Packet Loss = 0%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 1.19% | 5.69% | 91.66s | 56.33s | 33.16 KB/s |
| Spray-and-Wait | 11.21% | 18.40% | 96.68s | 70.64s | 49.30 KB/s |
| PRoPHET | 9.90% | 12.58% | 110.56s | 99.66s | 40.45 KB/s |

## Configuration: Offered Load = 20.0 TPS, Packet Loss = 25%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.77% | 5.94% | 81.72s | 59.30s | 32.21 KB/s |
| Spray-and-Wait | 13.02% | 18.19% | 90.32s | 66.21s | 51.48 KB/s |
| PRoPHET | 11.96% | 18.12% | 123.04s | 131.95s | 52.38 KB/s |

## Configuration: Offered Load = 20.0 TPS, Packet Loss = 50%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 2.02% | 7.06% | 73.86s | 48.79s | 32.80 KB/s |
| Spray-and-Wait | 12.33% | 18.77% | 108.31s | 83.47s | 49.39 KB/s |
| PRoPHET | 11.35% | 13.94% | 99.59s | 77.84s | 50.49 KB/s |

## Configuration: Offered Load = 20.0 TPS, Packet Loss = 80%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.85% | 6.27% | 73.45s | 56.22s | 32.85 KB/s |
| Spray-and-Wait | 12.31% | 19.56% | 102.02s | 83.72s | 51.86 KB/s |
| PRoPHET | 13.35% | 18.56% | 128.84s | 119.32s | 53.73 KB/s |

## Configuration: Offered Load = 50.0 TPS, Packet Loss = 0%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.04% | 1.81% | 135.27s | 50.62s | 70.31 KB/s |
| Spray-and-Wait | 2.12% | 4.02% | 92.24s | 52.58s | 78.11 KB/s |
| PRoPHET | 4.23% | 5.16% | 124.30s | 102.33s | 78.81 KB/s |

## Configuration: Offered Load = 50.0 TPS, Packet Loss = 25%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.00% | 1.79% | N/A | 43.93s | 72.20 KB/s |
| Spray-and-Wait | 2.56% | 4.17% | 95.75s | 54.55s | 77.93 KB/s |
| PRoPHET | 3.98% | 5.07% | 126.33s | 106.09s | 81.94 KB/s |

## Configuration: Offered Load = 50.0 TPS, Packet Loss = 50%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.00% | 2.05% | N/A | 53.46s | 69.64 KB/s |
| Spray-and-Wait | 2.38% | 4.08% | 93.77s | 51.04s | 76.99 KB/s |
| PRoPHET | 4.38% | 5.27% | 116.26s | 89.45s | 86.36 KB/s |

## Configuration: Offered Load = 50.0 TPS, Packet Loss = 80%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.39% | 2.16% | 85.37s | 54.97s | 69.26 KB/s |
| Spray-and-Wait | 1.93% | 3.63% | 123.08s | 65.12s | 76.17 KB/s |
| PRoPHET | 4.00% | 5.59% | 125.40s | 108.52s | 85.84 KB/s |

## Configuration: Offered Load = 100.0 TPS, Packet Loss = 0%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.06% | 0.86% | 128.18s | 63.12s | 124.44 KB/s |
| Spray-and-Wait | 0.72% | 1.68% | 69.33s | 46.47s | 132.68 KB/s |
| PRoPHET | 1.54% | 2.03% | 101.41s | 92.60s | 134.81 KB/s |

## Configuration: Offered Load = 100.0 TPS, Packet Loss = 25%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.00% | 0.80% | N/A | 45.50s | 124.92 KB/s |
| Spray-and-Wait | 0.75% | 1.50% | 82.79s | 41.11s | 130.64 KB/s |
| PRoPHET | 1.59% | 2.43% | 114.31s | 115.57s | 139.37 KB/s |

## Configuration: Offered Load = 100.0 TPS, Packet Loss = 50%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.01% | 0.89% | 66.57s | 48.99s | 125.19 KB/s |
| Spray-and-Wait | 0.78% | 1.52% | 85.80s | 36.68s | 130.98 KB/s |
| PRoPHET | 1.76% | 2.16% | 119.77s | 82.81s | 138.48 KB/s |

## Configuration: Offered Load = 100.0 TPS, Packet Loss = 80%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.01% | 0.89% | 21.95s | 56.67s | 127.11 KB/s |
| Spray-and-Wait | 0.81% | 1.80% | 97.40s | 53.74s | 131.63 KB/s |
| PRoPHET | 2.40% | 2.83% | 128.51s | 57.16s | 136.36 KB/s |
