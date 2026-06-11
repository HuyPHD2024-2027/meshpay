#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT_DIR / "logs" / "benchmarks" / "scripts"
BENCHMARK = ROOT_DIR / "examples" / "meshpay_benchmark.py"


@dataclass(frozen=True)
class SpeedRange:
    min_velocity: float
    max_velocity: float

    @property
    def label(self) -> str:
        return f"{fmt(self.min_velocity)}-{fmt(self.max_velocity)}"


@dataclass(frozen=True)
class RunSpec:
    clients: int
    authorities: int
    node_range: float
    accounts_per_station: int
    total_virtual_accounts: int
    speed: SpeedRange
    payments: int
    payment_rate: float
    duration: float
    warmup: float
    amount: int
    initial_balance: int
    medium: str
    routing: str
    seed: int
    area_width: float
    area_height: float
    mobility_start: float
    no_mobility: bool
    run_index: int

    @property
    def run_id(self) -> str:
        mobility = "static" if self.no_mobility else f"s{self.speed.label}"
        return (
            f"{self.run_index:03d}_"
            f"c{self.clients}_"
            f"a{self.authorities}_"
            f"r{fmt(self.node_range)}_"
            f"v{self.accounts_per_station}_"
            f"tv{self.total_virtual_accounts}_"
            f"{mobility}_"
            f"p{self.payments}_"
            f"rate{fmt(self.payment_rate)}_"
            f"seed{self.seed}"
        )


def fmt(value: float | int) -> str:
    if isinstance(value, int):
        return str(value)

    value = float(value)
    if value.is_integer():
        return str(int(value))
    return str(value).replace(".", "p")


def parse_int_list(value: str, name: str) -> list[int]:
    result = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = int(raw)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be comma-separated integers") from exc
        if item <= 0:
            raise argparse.ArgumentTypeError(f"{name} values must be positive")
        result.append(item)

    if not result:
        raise argparse.ArgumentTypeError(f"{name} cannot be empty")
    return result


def parse_float_list(value: str, name: str) -> list[float]:
    result = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = float(raw)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name} must be comma-separated numbers") from exc
        if item <= 0:
            raise argparse.ArgumentTypeError(f"{name} values must be positive")
        result.append(item)

    if not result:
        raise argparse.ArgumentTypeError(f"{name} cannot be empty")
    return result


def parse_speeds(value: str) -> list[SpeedRange]:
    result = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split(":")
        if len(parts) != 2:
            raise argparse.ArgumentTypeError(
                "--speeds values must look like min:max,min:max"
            )
        try:
            min_velocity = float(parts[0])
            max_velocity = float(parts[1])
        except ValueError as exc:
            raise argparse.ArgumentTypeError("--speeds values must be numbers") from exc
        if min_velocity <= 0 or max_velocity <= 0:
            raise argparse.ArgumentTypeError("--speeds values must be positive")
        if max_velocity < min_velocity:
            raise argparse.ArgumentTypeError("--speeds max must be >= min")
        result.append(SpeedRange(min_velocity, max_velocity))

    if not result:
        raise argparse.ArgumentTypeError("--speeds cannot be empty")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a matrix of MeshPay offline payment benchmarks."
    )

    parser.add_argument("--execute", action="store_true", help="Run commands. Default only prints them.")
    parser.add_argument("--sudo", action="store_true", default=True, help="Prefix benchmark commands with sudo.")
    parser.add_argument("--no-sudo", dest="sudo", action="store_false", help="Do not prefix benchmark commands with sudo.")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue remaining runs after a failure.")

    parser.add_argument("--clients", default="4,6", help="Comma-separated client counts.")
    parser.add_argument("--authorities", default="4", help="Comma-separated authority counts.")
    parser.add_argument("--ranges", default="100,300", help="Comma-separated transmission ranges.")
    parser.add_argument("--accounts", default="10,20", help="Comma-separated virtual accounts per station.")
    parser.add_argument(
        "--total-virtual-accounts",
        default=None,
        help=(
            "Comma-separated total virtual account counts. When set, "
            "accounts per station is derived as total/client count and "
            "payments is set to the same total, so each virtual account sends one tx."
        ),
    )
    parser.add_argument("--speeds", default="0.5:2.0", help="Comma-separated mobility speed ranges as min:max.")

    parser.add_argument("--payments", default="100", help="Comma-separated payment counts. Ignored when --total-virtual-accounts is set.")
    parser.add_argument(
        "--payment-rate",
        default="0.5",
        help="Comma-separated payment rates, or 'match' to use one-second bursts matching each payment count.",
    )
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--warmup", type=float, default=5.0)
    parser.add_argument("--amount", type=int, default=1)
    parser.add_argument("--initial-balance", type=int, default=10000)
    parser.add_argument("--medium", choices=["adhoc", "mesh"], default="adhoc")
    parser.add_argument("--routing", choices=["epidemic"], default="epidemic")
    parser.add_argument("--seed", type=int, default=20)
    parser.add_argument("--area-width", type=float, default=200.0)
    parser.add_argument("--area-height", type=float, default=200.0)
    parser.add_argument("--mobility-start", type=float, default=1.0)
    parser.add_argument("--no-mobility", action="store_true", help="Disable mobility for all runs.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))

    args = parser.parse_args()

    args.clients = parse_int_list(args.clients, "--clients")
    args.authorities = parse_int_list(args.authorities, "--authorities")
    args.ranges = parse_float_list(args.ranges, "--ranges")
    args.accounts = parse_int_list(args.accounts, "--accounts")
    if args.total_virtual_accounts is not None:
        args.total_virtual_accounts = parse_int_list(
            args.total_virtual_accounts,
            "--total-virtual-accounts",
        )
    args.speeds = parse_speeds(args.speeds)
    args.payments = parse_int_list(args.payments, "--payments")
    if str(args.payment_rate).strip().lower() == "match":
        args.payment_rate = "match"
    else:
        args.payment_rate = parse_float_list(args.payment_rate, "--payment-rate")

    if args.duration <= 0:
        parser.error("--duration must be positive")
    if args.warmup < 0:
        parser.error("--warmup must be >= 0")
    if args.amount <= 0:
        parser.error("--amount must be positive")
    if args.initial_balance < 0:
        parser.error("--initial-balance must be >= 0")

    return args


def payment_rates_for(args: argparse.Namespace, payments: int) -> list[float]:
    if args.payment_rate == "match":
        return [float(payments)]
    return list(args.payment_rate)


def build_specs(args: argparse.Namespace) -> list[RunSpec]:
    specs = []
    run_index = 1

    if args.total_virtual_accounts is not None:
        matrix = itertools.product(
            args.clients,
            args.authorities,
            args.ranges,
            args.total_virtual_accounts,
            args.speeds,
        )

        for clients, authorities, node_range, total_accounts, speed in matrix:
            if total_accounts % clients != 0:
                raise SystemExit(
                    f"total virtual accounts {total_accounts} must be divisible by clients {clients}"
                )

            accounts = total_accounts // clients
            payments = total_accounts

            for payment_rate in payment_rates_for(args, payments):
                specs.append(
                    RunSpec(
                        clients=clients,
                        authorities=authorities,
                        node_range=node_range,
                        accounts_per_station=accounts,
                        total_virtual_accounts=total_accounts,
                        speed=speed,
                        payments=payments,
                        payment_rate=payment_rate,
                        duration=args.duration,
                        warmup=args.warmup,
                        amount=args.amount,
                        initial_balance=args.initial_balance,
                        medium=args.medium,
                        routing=args.routing,
                        seed=args.seed,
                        area_width=args.area_width,
                        area_height=args.area_height,
                        mobility_start=args.mobility_start,
                        no_mobility=args.no_mobility,
                        run_index=run_index,
                    )
                )
                run_index += 1

        return specs

    matrix = itertools.product(
        args.clients,
        args.authorities,
        args.ranges,
        args.accounts,
        args.speeds,
        args.payments,
    )

    for clients, authorities, node_range, accounts, speed, payments in matrix:
        total_accounts = clients * accounts
        for payment_rate in payment_rates_for(args, payments):
            specs.append(
                RunSpec(
                    clients=clients,
                    authorities=authorities,
                    node_range=node_range,
                    accounts_per_station=accounts,
                    total_virtual_accounts=total_accounts,
                    speed=speed,
                    payments=payments,
                    payment_rate=payment_rate,
                    duration=args.duration,
                    warmup=args.warmup,
                    amount=args.amount,
                    initial_balance=args.initial_balance,
                    medium=args.medium,
                    routing=args.routing,
                    seed=args.seed,
                    area_width=args.area_width,
                    area_height=args.area_height,
                    mobility_start=args.mobility_start,
                    no_mobility=args.no_mobility,
                    run_index=run_index,
                )
            )
            run_index += 1

    return specs


def command_for(spec: RunSpec, run_dir: Path, use_sudo: bool) -> list[str]:
    command = []
    if use_sudo:
        command.append("sudo")

    command.extend(
        [
            sys.executable,
            str(BENCHMARK),
            "--routing",
            spec.routing,
            "--medium",
            spec.medium,
            "--clients",
            str(spec.clients),
            "--authorities",
            str(spec.authorities),
            "--accounts-per-station",
            str(spec.accounts_per_station),
            "--payments",
            str(spec.payments),
            "--payment-rate",
            str(spec.payment_rate),
            "--amount",
            str(spec.amount),
            "--initial-balance",
            str(spec.initial_balance),
            "--duration",
            str(spec.duration),
            "--warmup",
            str(spec.warmup),
            "--seed",
            str(spec.seed),
            "--node-range",
            str(spec.node_range),
            "--area-width",
            str(spec.area_width),
            "--area-height",
            str(spec.area_height),
            "--min-velocity",
            str(spec.speed.min_velocity),
            "--max-velocity",
            str(spec.speed.max_velocity),
            "--mobility-start",
            str(spec.mobility_start),
            "--log-dir",
            str(run_dir),
        ]
    )

    if spec.no_mobility:
        command.append("--no-mobility")

    return command


def shell_join(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def nested_get(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def summarize_result(
    spec: RunSpec,
    run_dir: Path,
    command: list[str],
    exit_code: int | None,
    started_at: float | None,
    ended_at: float | None,
) -> dict[str, Any]:
    benchmark_path = run_dir / "benchmark.json"
    benchmark = {}
    if benchmark_path.exists():
        with benchmark_path.open("r", encoding="utf-8") as f:
            benchmark = json.load(f)

    row: dict[str, Any] = {
        "run_id": spec.run_id,
        "run_dir": str(run_dir),
        "command": shell_join(command),
        "exit_code": exit_code,
        "wall_time_s": (ended_at - started_at) if started_at and ended_at else None,
        **{f"param.{key}": value for key, value in asdict(spec).items() if key != "speed"},
        "param.min_velocity": spec.speed.min_velocity,
        "param.max_velocity": spec.speed.max_velocity,
    }

    fields = {
        "summary.payments_created": "payments_created",
        "summary.payments_confirmed": "payments_confirmed",
        "summary.payments_accepted": "payments_accepted",
        "summary.payment_confirmation_rate_percent": "payment_confirmation_rate_percent",
        "summary.payment_acceptance_rate_percent": "payment_acceptance_rate_percent",
        "summary.created_tps": "created_tps",
        "summary.confirmed_tps": "confirmed_tps",
        "summary.accepted_tps": "accepted_tps",
        "summary.tx_payloads_per_second": "tx_payloads_per_second",
        "summary.rx_payloads_per_second": "rx_payloads_per_second",
        "summary.tx_plus_rx_payloads_per_second": "tx_plus_rx_payloads_per_second",
        "summary.tx_bytes_per_second": "tx_bytes_per_second",
        "summary.rx_bytes_per_second": "rx_bytes_per_second",
        "summary.tx_plus_rx_bytes_per_second": "tx_plus_rx_bytes_per_second",
        "latency_ms.time_to_quorum.avg": "avg_time_to_quorum_ms",
        "latency_ms.time_to_quorum.p50": "p50_time_to_quorum_ms",
        "latency_ms.time_to_quorum.p95": "p95_time_to_quorum_ms",
        "latency_ms.time_to_acceptance.avg": "avg_time_to_acceptance_ms",
        "latency_ms.time_to_acceptance.p50": "p50_time_to_acceptance_ms",
        "latency_ms.time_to_acceptance.p95": "p95_time_to_acceptance_ms",
        "raw_counts.payment_created_events": "raw_payment_created_events",
        "raw_counts.confirmation_created_events": "raw_confirmation_created_events",
        "raw_counts.payment_accepted_events": "raw_payment_accepted_events",
        "raw_counts.tx_events": "raw_tx_events",
        "raw_counts.rx_events": "raw_rx_events",
    }

    for path, name in fields.items():
        row[name] = nested_get(benchmark, path)

    return row


def write_summary(output_root: Path, rows: list[dict[str, Any]]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)

    json_path = output_root / "summary.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, sort_keys=True)
        f.write("\n")

    csv_path = output_root / "summary.csv"
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    specs = build_specs(args)

    print(f"planned_runs={len(specs)}")
    print(f"output_root={output_root}")

    rows: list[dict[str, Any]] = []

    for spec in specs:
        run_dir = output_root / spec.run_id
        command = command_for(spec, run_dir, args.sudo)
        print(f"[{spec.run_id}] {shell_join(command)}")

        if not args.execute:
            rows.append(
                summarize_result(
                    spec=spec,
                    run_dir=run_dir,
                    command=command,
                    exit_code=None,
                    started_at=None,
                    ended_at=None,
                )
            )
            continue

        output_root.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT_DIR)

        started_at = time.time()
        completed = subprocess.run(command, cwd=str(ROOT_DIR), env=env, check=False)
        ended_at = time.time()

        rows.append(
            summarize_result(
                spec=spec,
                run_dir=run_dir,
                command=command,
                exit_code=completed.returncode,
                started_at=started_at,
                ended_at=ended_at,
            )
        )
        write_summary(output_root, rows)

        if completed.returncode != 0 and not args.continue_on_error:
            print(
                f"stopping after failed run {spec.run_id} "
                f"exit_code={completed.returncode}",
                file=sys.stderr,
            )
            return completed.returncode

    if args.execute:
        write_summary(output_root, rows)
        print(f"summary_json={output_root / 'summary.json'}")
        print(f"summary_csv={output_root / 'summary.csv'}")
    else:
        print("dry_run=true")
        print("pass --execute to run benchmarks")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
