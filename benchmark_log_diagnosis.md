# MeshPay Benchmark Log Diagnosis

## Conclusion

The corrected 240-second, no-drain runs still show hot-path saturation rather than a reason to replace Epidemic, Spray-and-Wait, or PRoPHET. At 50 TPS and 0% loss, all 12,000 payments are admitted, 1,832 reach an authority, 707 return a signed response, and 268 are confirmed.

## Main findings

1. 50 payment TPS creates at least 200 new transfer bundles per second because every payment is sent separately to four authorities. This is before routing replicas, signed responses, confirmations, discovery traffic, and TCP retransmissions. At 100 TPS the minimum injection is 400 bundles/s.
2. Router exchanges are failing at large scale. The Epidemic 0%-loss seed-1 logs contain 5,070 contact_missed events, predominantly TimeoutError timed out. The paired 25%-loss run contains 5,289.
3. The router allows 16 parallel exchanges per node, up to 10,000 bundles per exchange, and a 30-second socket timeout. The logged transfer payload is about 392 bytes before wire overhead, so a full batch is multiple megabytes, not the approximately 100 KB claimed by the configuration comment.
4. Pre-fix bundle persistence was a second bottleneck: the store refreshed every 50 ms and scanned all JSON bundle files after external injection. The journal change removes that scan from the normal hot path.
5. Throughput Tx+Rx is routing traffic, not useful transaction throughput. It includes duplicates, replicas, discovery, and failed exchange work. More KB/s does not imply more confirmations.

## Transaction funnel: Epidemic, 50 TPS, seed 1

| Stage | 0% loss | 25% loss |
|---|---:|---:|
| Payments created | 12,000 | 12,000 |
| Orders reaching at least one authority | 3,139 (26.2%) | 2,024 (16.9%) |
| Orders with a delivered signed response | 968 (8.1%) | 1,411 (11.8%) |
| Sender formed 3-signature confirmation | 301 (2.51%) | 527 (4.39%) |
| Confirmation reached recipient | 268 (2.23%) | 384 (3.20%) |
| Recipient accepted payment | 176 (1.47%) | 181 (1.51%) |

All 12,000 planned payments were created, with no skipped or failed submissions. The bottleneck is therefore not client admission, account balance, or payment creation. It is transfer delivery to enough authorities, signed-response return to the sender, and final confirmation delivery to the recipient.

## Why 25% loss can outperform 0%

The packet-loss attack is receive-only iptables loss on four selected nodes and is logged as occurring after wireless airtime. It drops TCP/IP packets, not complete DTN bundles independently. Dropped TCP traffic is not decoded, persisted, and re-flooded at the application layer.

When the no-loss system is already overloaded, this becomes application-level load shedding. It reduces downstream bundle ingestion, disk writes, and Epidemic replication. That can free enough processing and exchange capacity for more of the older surviving orders to collect three signatures. This result does not mean packet loss improves reliability; it means the loss variable is confounded with congestion and application work.

Across five paired Epidemic 50 TPS seeds:

| Seed | Confirmed at 0% | Confirmed at 25% |
|---:|---:|---:|
| 1 | 301 | 527 |
| 2 | 221 | 1,094 |
| 3 | 0 | 208 |
| 4 | 13 | 54 |
| 5 | 0 | 61 |
| Mean | 107.0 (0.89%) | 388.8 (3.24%) |

The result is systematic in this overloaded setup, but the seed-to-seed spread is extreme. It is evidence of an unstable saturated benchmark, not a valid causal loss-versus-reliability curve.

## Why attack and post-attack rows are zero

For positive-loss 50 TPS runs, approximately 3,000 payments are created in the first 60 seconds, 3,000 during the 60-second attack, and 6,000 in the following 120 seconds. The benchmark ends after the configured pre, attack, and post windows; there is no implicit drain phase.

The phase_funnel implementation does not count confirmation events by their event timestamps. It assigns each order to the phase of payment_created and then asks whether that order was ever confirmed or accepted. Therefore, zero confirmed in attack means no transaction created during the attack completed. It does not mean no confirmation event occurred during the attack.

The bundle scheduler prioritizes confirmation orders, then signed orders, then transfer orders. Within a type, older bundles are selected first. Under the persistent backlog, pre-attack transfer orders monopolize progress. Confirmation events continue later in wall-clock time, but they belong to the old pre-attack cohort. Newer attack and post-attack cohorts remain queued and are censored when the fixed test duration ends.

Orders still pending at the fixed benchmark end are right-censored and must be reported explicitly.

## Phase reporting defects

1. Scheduled phase windows are now emitted independently of attack-controller events, including at 0% loss.
2. At positive loss, confirmed and accepted are cohort outcomes based on creation time, not event activity in the named phase.
3. Pending and censored orders at test end are not reported.
4. Created throughput is now reported separately over the configured traffic window; the full-run rate remains a secondary compatibility metric.

These are benchmark and reporting defects, independent of routing protocol logic.

## Packet-loss model limitation

The attack metadata reports loss_hook=iptables_input_after_wireless_airtime and selects four of twelve nodes. Because it drops arbitrary TCP control, acknowledgement, and data packets, its effects include retransmission, connection timeout, routing backoff, persistence, and re-replication. It is not a clean independent DTN bundle-loss experiment.

Consequently, the 0% and 25% cells compare different congestion dynamics in a saturated system. They do not isolate end-to-end bundle delivery probability.

## Required benchmark corrections

1. Report event activity by event timestamp separately from cohort outcomes by creation timestamp. Emit scheduled phase boundaries even for 0% loss.
2. Add one-second counters for attempted, created, transfer delivered per authority, signed response delivered, quorum formed, confirmation delivered, and accepted.
3. Record per-node store depth, oldest bundle age, exchange attempts, successes, timeouts, bundle and byte counts per exchange, TCP duration, retransmissions, CPU, and disk I/O.
4. Establish capacity at 0% loss using 0.1, 0.5, 1, 2, 5, 10, 20, and 50 TPS. Compare loss below saturation separately from overload experiments.
5. Keep the fixed no-drain duration and explicitly report orders without sufficient observation as censored.
6. Report eligible and dropped packets per node and direction, TCP retransmissions, and complete-bundle loss as distinct measurements.
7. Use all paired seeds and report median, interquartile range, and confidence intervals. Do not interpret one run as representative.
8. Separate unique accepted transaction throughput from routing overhead and duplicate bytes.

## Valid interpretation

The current logs support one defensible conclusion: at 50 TPS, the MeshPay DTN emulation is saturated, older transactions monopolize progress, exchange timeouts are widespread, and fixed-end censoring materially affects raw yield. Moderate receive-side packet loss reduces higher-layer replication pressure and can improve completion of surviving old transactions. The matrix should not be used to claim that packet loss improves confirmation until the new censor-aware accounting is measured below the saturation point.
## Hot-path correction

External injection now publishes bundle files through an append-only per-store journal. Routers consume complete journal records incrementally, retain startup reconciliation, and use a 30-second recovery scan. Multi-destination injection enriches and serializes payment data once. Payment and store event streams are line-buffered and explicitly flushed before reporting or shutdown.

Reports preserve raw confirmed/created yield and add auditable 10, 30, 60, and 120-second fixed-horizon estimates with eligible, confirmed, and censored counts. Confirmed TPS and the 60-second matured-cohort yield are the primary matrix aggregates. The 268/12,000 corrected baseline is pre-fix evidence; paired 1, 5, 10, 20, and 50 TPS results remain to be measured.
