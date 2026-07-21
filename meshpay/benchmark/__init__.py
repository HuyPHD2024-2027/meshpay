"""Reusable benchmark utilities for MeshPay.

The benchmark layer is intentionally application-neutral.

Current use:
    DTN Epidemic Routing benchmark.

Future use:
    MeshPay offline payment / FastPay benchmark.

Benchmark collectors should consume generic event logs instead of depending on
one specific protocol implementation.
"""