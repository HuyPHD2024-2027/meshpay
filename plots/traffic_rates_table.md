# Peer Traffic Rates Before, During, and After Attack

This table shows the **Average Peer TX and RX Rates (KiB/s)** computed across three distinct phases of each benchmark run:
1. **Before Attack**: Pre-attack baseline phase.
2. **During Attack**: Jamming/attack phase.
3. **After Attack**: Recovery phase after attack stops.

| Routing Protocol | Workload (TPS) | Loss Probability | Pre-Attack TX / RX (KiB/s) | During-Attack TX / RX (KiB/s) | Post-Attack TX / RX (KiB/s) |
|:---|:---:|:---:|:---:|:---:|:---:|
| Epidemic | 10 | 0.00 | 9.514 / 9.834 | 11.724 / 11.942 | 4.817 / 5.211 |
| Epidemic | 10 | 0.25 | 22.632 / 22.543 | 21.207 / 20.197 | 17.138 / 16.751 |
| Epidemic | 10 | 0.50 | 7.879 / 8.269 | 5.689 / 6.083 | 1.284 / 1.429 |
| Epidemic | 10 | 0.80 | 10.717 / 9.873 | 19.146 / 19.307 | 10.873 / 11.949 |
| Epidemic | 20 | 0.00 | 13.707 / 13.962 | 20.622 / 20.296 | 20.995 / 21.670 |
| Epidemic | 20 | 0.25 | 0.667 / 1.037 | 8.922 / 10.009 | 6.058 / 6.080 |
| Epidemic | 20 | 0.50 | 6.587 / 6.909 | 9.494 / 10.337 | 2.348 / 2.352 |
| Epidemic | 20 | 0.80 | 5.210 / 5.711 | 14.619 / 15.270 | 2.336 / 2.513 |
| PRoPHET | 10 | 0.00 | 6.202 / 6.563 | 6.512 / 7.442 | 3.433 / 4.155 |
| PRoPHET | 10 | 0.25 | 9.851 / 10.714 | 21.049 / 22.516 | 17.893 / 16.867 |
| PRoPHET | 10 | 0.50 | 17.613 / 18.321 | 19.271 / 22.039 | 13.112 / 13.033 |
| PRoPHET | 10 | 0.80 | 16.525 / 16.692 | 24.675 / 27.813 | 20.265 / 19.008 |
| Spray-and-Wait | 10 | 0.00 | 18.162 / 19.696 | 19.541 / 20.116 | 15.111 / 15.984 |
| Spray-and-Wait | 10 | 0.25 | 9.838 / 10.262 | 9.462 / 9.096 | 2.307 / 2.276 |
| Spray-and-Wait | 10 | 0.50 | 20.614 / 21.761 | 20.635 / 20.697 | 18.107 / 17.466 |
| Spray-and-Wait | 10 | 0.80 | 20.363 / 21.578 | 19.998 / 20.805 | 13.559 / 12.889 |
| Spray-and-Wait | 20 | 0.00 | 3.585 / 4.199 | 6.553 / 7.137 | 0.597 / 0.689 |
| Spray-and-Wait | 20 | 0.25 | 14.654 / 14.845 | 13.031 / 13.331 | 5.114 / 4.905 |
| Spray-and-Wait | 20 | 0.50 | 13.906 / 13.779 | 13.981 / 13.664 | 6.791 / 6.386 |
| Spray-and-Wait | 20 | 0.80 | 12.514 / 12.717 | 9.721 / 8.194 | 0.000 / 0.000 |