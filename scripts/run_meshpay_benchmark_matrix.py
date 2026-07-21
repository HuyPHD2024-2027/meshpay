#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
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
    payment_rate: float
    duration: float
    warmup: float
    settle_time: float
    amount: int
    initial_balance: int
    medium: str
    routing: str
    seed: int
    area_width: float
    area_height: float
    mobility_start: float
    no_mobility: bool
    attack: str
    attack_loss_probability: float
    attack_tpre: float
    attack_tatk: float
    attack_tpost: float
    attack_target_count: str
    attack_load_rate: float
    keep_debug_logs: bool
    run_index: int
    plot: bool

    @property
    def run_id(self) -> str:
        return (
            f"{self.run_index:03d}_"
            f"c{self.clients}_"
            f"a{self.authorities}_"
            f"r{fmt(self.node_range)}_"
            f"rate{fmt(self.payment_rate)}_"
            f"m{self.medium}_"
            f"rt{routing_label(self.routing)}_"
            f"att{attack_label(self.attack)}_"
            f"loss{fmt(self.attack_loss_probability)}"
        )


def attack_label(attack: str) -> str:
    return {
        "none": "None",
        "packetloss": "PL",
        "load": "Load",
        "packetloss-load": "PLLoad",
    }.get(attack, attack)


def routing_label(routing: str) -> str:
    return {
        "epidemic": "Epi",
        "spray-and-wait": "SnW",
        "prophet": "Prophet",
    }.get(routing, routing.replace("-", ""))


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
            raise argparse.ArgumentTypeError(
                f"{name} must be comma-separated integers"
            ) from exc
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
            raise argparse.ArgumentTypeError(
                f"{name} must be comma-separated numbers"
            ) from exc
        if item <= 0:
            raise argparse.ArgumentTypeError(f"{name} values must be positive")
        result.append(item)

    if not result:
        raise argparse.ArgumentTypeError(f"{name} cannot be empty")
    return result


def parse_probability_list(value: str, name: str) -> list[float]:
    result = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = float(raw)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"{name} must be comma-separated numbers"
            ) from exc
        if not 0.0 <= item <= 1.0:
            raise argparse.ArgumentTypeError(f"{name} values must be between 0.0 and 1.0")
        result.append(item)

    if not result:
        raise argparse.ArgumentTypeError(f"{name} cannot be empty")
    return result


def parse_routing_list(value: str) -> list[str]:
    allowed = {"epidemic", "spray-and-wait", "prophet"}
    result = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        if item not in allowed:
            raise argparse.ArgumentTypeError(
                f"--routing values must be one of {sorted(allowed)}"
            )
        result.append(item)

    if not result:
        raise argparse.ArgumentTypeError("--routing cannot be empty")
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

    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run commands. Default only prints them.",
    )
    parser.add_argument(
        "--sudo",
        action="store_true",
        default=True,
        help="Prefix benchmark commands with sudo.",
    )
    parser.add_argument(
        "--no-sudo",
        dest="sudo",
        action="store_false",
        help="Do not prefix benchmark commands with sudo.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue remaining runs after a failure.",
    )

    parser.add_argument("--clients", default="4,6", help="Comma-separated client counts.")
    parser.add_argument(
        "--authorities",
        default="4",
        help="Comma-separated authority counts.",
    )
    parser.add_argument(
        "--ranges",
        default="100,300",
        help="Comma-separated transmission ranges.",
    )
    parser.add_argument(
        "--total-virtual-accounts",
        default=None,
        help=(
            "Comma-separated total virtual account counts. When set, "
            "accounts per station is derived as total/client count."
        ),
    )
    parser.add_argument(
        "--speeds",
        default="0.5:2.0",
        help="Comma-separated mobility speed ranges as min:max.",
    )

    parser.add_argument(
        "--payment-rate",
        default="0.5",
        help="Comma-separated open-loop payment rates in payments per second.",
    )
    parser.add_argument(
        "--duration",
        default="auto",
        help=(
            "Total benchmark duration in seconds. Use 'auto' (default) to "
            "compute per-run duration based on payment rate and attack timing."
        ),
    )
    parser.add_argument("--warmup", type=float, default=5.0)
    parser.add_argument("--settle-time", type=float, default=60.0)
    parser.add_argument("--amount", type=int, default=1)
    parser.add_argument("--initial-balance", type=int, default=10000)
    parser.add_argument("--medium", choices=["adhoc", "mesh"], default="adhoc")
    parser.add_argument(
        "--routing",
        default="epidemic",
        help="Comma-separated routing protocols: epidemic,spray-and-wait,prophet.",
    )
    parser.add_argument("--seed", type=int, default=20)
    parser.add_argument("--area-width", type=float, default=200.0)
    parser.add_argument("--area-height", type=float, default=200.0)
    parser.add_argument("--mobility-start", type=float, default=1.0)
    parser.add_argument("--no-mobility", action="store_true", help="Disable mobility for all runs.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--plot", action="store_true", help="Show Mininet-WiFi graph.")

    parser.add_argument(
        "--attack",
        choices=["none", "packetloss", "load", "packetloss-load"],
        default="none",
    )
    parser.add_argument(
        "--attack-loss-probability",
        default="0.1",
        help="Comma-separated packet loss probabilities.",
    )
    parser.add_argument("--attack-tpre", type=float, default=60.0)
    parser.add_argument("--attack-tatk", type=float, default=180.0)
    parser.add_argument("--attack-tpost", type=float, default=60.0)
    parser.add_argument("--attack-target-count", default="auto")
    parser.add_argument("--attack-load-rate", type=float, default=0.0)
    parser.add_argument(
        "--keep-debug-logs",
        action="store_true",
        help="Keep daemon and store debug logs instead of minimizing them.",
    )

    args = parser.parse_args()

    attack_option_flags = {
        "--attack-loss-probability",
        "--attack-tpre",
        "--attack-tatk",
        "--attack-tpost",
        "--attack-target-count",
        "--attack-load-rate",
    }
    attack_options_used = any(
        arg == flag or arg.startswith(f"{flag}=")
        for arg in sys.argv[1:]
        for flag in attack_option_flags
    )

    if args.attack == "none" and attack_options_used:
        parser.error(
            "attack parameters were provided, but --attack is none; "
            "pass --attack packetloss, --attack load, or --attack packetloss-load"
        )

    args.clients = parse_int_list(args.clients, "--clients")
    args.authorities = parse_int_list(args.authorities, "--authorities")
    args.ranges = parse_float_list(args.ranges, "--ranges")
    if args.total_virtual_accounts is not None:
        args.total_virtual_accounts = parse_int_list(
            args.total_virtual_accounts,
            "--total-virtual-accounts",
        )
    args.speeds = parse_speeds(args.speeds)
    args.payment_rate = parse_float_list(args.payment_rate, "--payment-rate")
    try:
        args.routing = parse_routing_list(args.routing)
        args.attack_loss_probability = parse_probability_list(
            args.attack_loss_probability,
            "--attack-loss-probability",
        )
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    # Parse --duration: 'auto' or a positive float.
    if str(args.duration).strip().lower() == "auto":
        args.duration = "auto"
    else:
        try:
            args.duration = float(args.duration)
        except ValueError:
            parser.error("--duration must be 'auto' or a positive number")
        if args.duration <= 0:
            parser.error("--duration must be positive")

    if args.warmup < 0:
        parser.error("--warmup must be >= 0")
    
    if args.settle_time < 0:
        parser.error("--settle-time must be >= 0")

    if args.amount <= 0:
        parser.error("--amount must be positive")

    if args.initial_balance < 0:
        parser.error("--initial-balance must be >= 0")
        
    for loss_probability in args.attack_loss_probability:
        if not 0.0 <= loss_probability <= 1.0:
            parser.error("--attack-loss-probability must be between 0.0 and 1.0")
    if args.attack_tpre < 0 or args.attack_tatk < 0 or args.attack_tpost < 0:
        parser.error("attack timing values must be >= 0")
    if args.attack != "none" and args.attack_tatk <= 0:
        parser.error("--attack-tatk must be greater than 0 when attack is enabled")
    if (
        args.attack != "none"
        and args.duration != "auto"
        and (args.attack_tpre + args.attack_tatk + args.attack_tpost) > args.duration
    ):
        parser.error("--duration must be at least attack tpre + tatk + tpost")
    if args.attack_load_rate < 0:
        parser.error("--attack-load-rate must be >= 0")
    if args.attack_target_count != "auto":
        try:
            target_count = int(args.attack_target_count)
        except ValueError:
            parser.error("--attack-target-count must be auto or a non-negative integer")
        if target_count < 0:
            parser.error("--attack-target-count must be auto or a non-negative integer")

    return args


def traffic_generation_duration(
    *,
    duration: float,
    attack: str,
    attack_tpre: float,
    attack_tatk: float,
    attack_tpost: float,
) -> float:
    if attack == "none":
        return duration
    return min(duration, attack_tpre + attack_tatk + attack_tpost)


def derive_payment_count(payment_rate: float, traffic_duration: float) -> int:
    return max(1, math.ceil(payment_rate * traffic_duration))


def compute_auto_duration(
    payment_rate: float,
    attack: str,
    attack_tpre: float,
    attack_tatk: float,
    attack_tpost: float,
    settle_time: float,
) -> float:
    """Compute duration without a separate payment-count control."""
    if attack == "none":
        base = 60.0
    else:
        base = attack_tpre + attack_tatk + attack_tpost
        
    return math.ceil((base + settle_time) / 10.0) * 10.0


def build_specs(args: argparse.Namespace) -> list[RunSpec]:
    specs = []
    run_index = 1

    total_accounts_list = args.total_virtual_accounts if args.total_virtual_accounts is not None else [None]

    matrix = itertools.product(
        args.clients,
        args.authorities,
        args.ranges,
        total_accounts_list,
        args.speeds,
        args.payment_rate,
        args.routing,
        args.attack_loss_probability,
    )

    for (
        clients,
        authorities,
        node_range,
        account_value,
        speed,
        payment_rate,
        routing,
        attack_loss_probability,
    ) in matrix:
        duration = (
            compute_auto_duration(
                payment_rate=payment_rate,
                attack=args.attack,
                attack_tpre=args.attack_tpre,
                attack_tatk=args.attack_tatk,
                attack_tpost=args.attack_tpost,
                settle_time=args.settle_time,
            )
            if args.duration == "auto"
            else args.duration
        )

        traffic_duration = traffic_generation_duration(
            duration=duration,
            attack=args.attack,
            attack_tpre=args.attack_tpre,
            attack_tatk=args.attack_tatk,
            attack_tpost=args.attack_tpost,
        )
        derived_payments = derive_payment_count(payment_rate, traffic_duration)
        accounts_per_station = math.ceil(derived_payments / clients)

        total_accounts = clients * accounts_per_station

        specs.append(
            RunSpec(
                clients=clients,
                authorities=authorities,
                node_range=node_range,
                accounts_per_station=accounts_per_station,
                total_virtual_accounts=total_accounts,
                speed=speed,
                payment_rate=payment_rate,
                duration=duration,
                warmup=args.warmup,
                settle_time=args.settle_time,
                amount=args.amount,
                initial_balance=args.initial_balance,
                medium=args.medium,
                routing=routing,
                seed=args.seed,
                area_width=args.area_width,
                area_height=args.area_height,
                mobility_start=args.mobility_start,
                no_mobility=args.no_mobility,
                attack=args.attack,
                plot=args.plot,
                attack_loss_probability=attack_loss_probability,
                attack_tpre=args.attack_tpre,
                attack_tatk=args.attack_tatk,
                attack_tpost=args.attack_tpost,
                attack_target_count=args.attack_target_count,
                attack_load_rate=args.attack_load_rate,
                keep_debug_logs=args.keep_debug_logs,
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
            "--payment-rate",
            str(spec.payment_rate),
            "--duration",
            str(spec.duration),
            "--node-range",
            str(spec.node_range),
            "--log-dir",
            str(run_dir),
            "--warmup",
            str(spec.warmup),
            "--settle-time",
            str(spec.settle_time),
            "--amount",
            str(spec.amount),
            "--initial-balance",
            str(spec.initial_balance),
            "--seed",
            str(spec.seed),
            "--accounts-per-station",
            str(spec.accounts_per_station),
        ]
    )

    if spec.no_mobility:
        command.append("--no-mobility")

    if spec.plot:
        command.append("--plot")

    if spec.keep_debug_logs:
        command.append("--keep-debug-logs")

    if spec.attack != "none":
        command.extend(
            [
                "--attack",
                spec.attack,
                "--attack-loss-probability",
                str(spec.attack_loss_probability),
                "--attack-tpre",
                str(spec.attack_tpre),
                "--attack-tatk",
                str(spec.attack_tatk),
                "--attack-tpost",
                str(spec.attack_tpost),
                "--attack-target-count",
                str(spec.attack_target_count),
                "--attack-load-rate",
                str(spec.attack_load_rate),
            ]
        )

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
        **{
            f"param.{key}": value
            for key, value in asdict(spec).items()
            if key != "speed"
        },
        "param.min_velocity": spec.speed.min_velocity,
        "param.max_velocity": spec.speed.max_velocity,
    }

    fields = {
        "payment_metrics.summary.payment_confirmation_rate_percent": "payment_confirmation_rate_percent",
        "payment_metrics.summary.payment_acceptance_rate_percent": "payment_acceptance_rate_percent",
        "network_metrics.summary.tx_bytes_rate": "network_tx_bytes_per_second",
        "network_metrics.summary.rx_bytes_rate": "network_rx_bytes_per_second",
        "payment_metrics.latency_ms.time_to_quorum.avg": "avg_time_to_quorum_ms",
        "attack.attack": "attack",
        "attack.loss_probability": "attack_loss_probability",
        "attack.selected_target_count": "attack_selected_target_count",
        "attack.targets": "attack_targets",
        "attack.tpre": "attack_tpre",
        "attack.tatk": "attack_tatk",
        "attack.tpost": "attack_tpost",
        "attack.load_rate": "attack_load_rate",
        "attack.attack_mode": "attack_mode",
        "attack.target_fraction": "attack_target_fraction",
        "attack.packet_loss_installation.install_success": "packet_loss_install_success",
        "attack.packet_loss_installation.installed_rules": "packet_loss_installed_rules",
        "attack.packet_loss_installation.attempted_rules": "packet_loss_attempted_rules",
        "attack.packet_loss_drop_counters.totals.drop_packets": "packet_loss_drop_packets",
        "attack.packet_loss_drop_counters.totals.drop_bytes": "packet_loss_drop_bytes",
        "attack.packet_loss_cleanup.rules_before_cleanup": "packet_loss_rules_before_cleanup",
        "attack.packet_loss_cleanup.removed_rules": "packet_loss_removed_rules",
        "attack.packet_loss_cleanup.remaining_rules": "packet_loss_rules_remaining_after_cleanup",
        "attack.packet_loss_cleanup.cleanup_success": "packet_loss_cleanup_success",
        "payment_metrics.phase_cohorts.cohorts_by_created_phase.before.confirmation_rate_by_run_end_percent": "cohort_before_confirmation_rate_percent",
        "payment_metrics.phase_cohorts.cohorts_by_created_phase.during.confirmation_rate_by_run_end_percent": "cohort_during_confirmation_rate_percent",
        "payment_metrics.phase_cohorts.cohorts_by_created_phase.after.confirmation_rate_by_run_end_percent": "cohort_after_confirmation_rate_percent",
        "payment_metrics.phase_cohorts.cohorts_by_created_phase.before.payments_censored_for_quorum": "cohort_before_quorum_censored",
        "payment_metrics.phase_cohorts.cohorts_by_created_phase.during.payments_censored_for_quorum": "cohort_during_quorum_censored",
        "payment_metrics.phase_cohorts.cohorts_by_created_phase.after.payments_censored_for_quorum": "cohort_after_quorum_censored",
        "payment_metrics.phase_cohorts.cohorts_by_created_phase.before.time_to_quorum_ms.avg": "cohort_before_avg_time_to_quorum_ms",
        "payment_metrics.phase_cohorts.cohorts_by_created_phase.during.time_to_quorum_ms.avg": "cohort_during_avg_time_to_quorum_ms",
        "payment_metrics.phase_cohorts.cohorts_by_created_phase.after.time_to_quorum_ms.avg": "cohort_after_avg_time_to_quorum_ms",
        "payment_metrics.hop_count.avg": "avg_hop_count",
    }

    for path, name in fields.items():
        row[name] = nested_get(benchmark, path)

    row["avg_time_to_quorum_ms"] = row.get("avg_time_to_quorum_ms")

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
