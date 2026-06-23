# MeshPay Routing Performance Comparison
Generated from: `summary.json`

## Configuration: Offered Load = 10.0 TPS, Packet Loss = 0%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 1.92% | 4.00% | 59.09s | 30.65s | 22.21 KB/s |
| Spray-and-Wait | 17.69% | 28.03% | 72.41s | 74.56s | 42.76 KB/s |
| PRoPHET | 6.56% | 10.06% | 111.35s | 101.97s | 27.28 KB/s |

## Configuration: Offered Load = 10.0 TPS, Packet Loss = 25%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 4.47% | 9.53% | 48.58s | 39.19s | 26.48 KB/s |
| Spray-and-Wait | 7.67% | 12.25% | 63.15s | 44.64s | 27.84 KB/s |
| PRoPHET | 17.47% | 23.42% | 123.29s | 118.42s | 39.26 KB/s |

## Configuration: Offered Load = 10.0 TPS, Packet Loss = 50%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 1.33% | 3.17% | 45.07s | 38.24s | 20.71 KB/s |
| Spray-and-Wait | 19.39% | 31.03% | 78.42s | 83.91s | 47.36 KB/s |
| PRoPHET | 17.64% | 22.94% | 114.57s | 97.17s | 37.50 KB/s |

## Configuration: Offered Load = 10.0 TPS, Packet Loss = 80%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 1.08% | 4.56% | 95.85s | 66.69s | 22.32 KB/s |
| Spray-and-Wait | 18.06% | 23.61% | 89.02s | 48.75s | 41.49 KB/s |
| PRoPHET | 19.94% | 25.39% | 106.95s | 91.64s | 41.57 KB/s |

## Configuration: Offered Load = 20.0 TPS, Packet Loss = 0%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.35% | 2.78% | 88.70s | 55.94s | 38.79 KB/s |
| Spray-and-Wait | 0.32% | 1.58% | 86.97s | 73.55s | 39.01 KB/s |

## Configuration: Offered Load = 20.0 TPS, Packet Loss = 25%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.08% | 1.40% | 284.31s | 128.47s | 38.68 KB/s |
| Spray-and-Wait | 4.11% | 8.26% | 78.65s | 64.37s | 47.17 KB/s |

## Configuration: Offered Load = 20.0 TPS, Packet Loss = 50%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.04% | 1.31% | 118.33s | 74.39s | 37.69 KB/s |
| Spray-and-Wait | 3.96% | 9.21% | 77.57s | 87.02s | 47.45 KB/s |

## Configuration: Offered Load = 20.0 TPS, Packet Loss = 80%

| Routing Protocol | Acceptance Rate | Offered Confirmation Yield | Avg Acceptance Latency | Avg Quorum Latency | Throughput (Tx+Rx) |
|---|---|---|---|---|---|
| Epidemic | 0.21% | 1.74% | 117.80s | 54.34s | 37.32 KB/s |
| Spray-and-Wait | 3.31% | 6.75% | 80.01s | 75.91s | 45.90 KB/s |
