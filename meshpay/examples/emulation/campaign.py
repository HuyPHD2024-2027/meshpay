"""Research campaign runner for MeshPay opportunistic payment emulation."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from meshpay.examples.emulation.aggregation import load_run_records, write_summary_csv
from meshpay.examples.emulation.config import EmulationConfig
from meshpay.examples.emulation.runner import build_subprocess_command
from meshpay.routing.registry import normalize_routing_name


PROTOCOLS = ("sdn_dtn", "epidemic", "prophet", "spray_and_wait")
DISRUPTION_RANGES = (10, 15, 20, 30)
DISRUPTION_SPEEDS = ((1, 3), (3, 6), (6, 10))
SCALABILITY_SIZES = ((5, 10), (7, 20), (9, 30), (11, 40))
PLACEMENT_SCENARIOS = ("uniform", "clustered", "corridor", "edge_authorities")


@dataclass(frozen=True)
class CampaignTrial:
    """One isolated benchmark subprocess in a campaign."""

    campaign: str
    scenario_name: str
    routing: str
    seed: int
    authorities: int
    clients: int
    wireless_range: int
    mobility_speed: Tuple[int, int]
    authority_layout: str
    client_layout: str
    experiment_id: str

    def metadata(self) -> Dict[str, object]:
        return {
            "campaign": self.campaign,
            "scenario_name": self.scenario_name,
            "routing": self.routing,
            "seed": self.seed,
            "authorities": self.authorities,
            "clients": self.clients,
            "wireless_range": self.wireless_range,
            "mobility_speed": f"{self.mobility_speed[0]}-{self.mobility_speed[1]}",
            "authority_layout": self.authority_layout,
            "client_layout": self.client_layout,
            "experiment_id": self.experiment_id,
        }


def parse_seed_list(value: str | Sequence[int]) -> List[int]:
    """Parse campaign seed CLI input."""

    if isinstance(value, str):
        return [int(part.strip()) for part in value.split(",") if part.strip()]
    return [int(seed) for seed in value]


def expand_campaign(config: EmulationConfig) -> List[CampaignTrial]:
    """Expand a balanced campaign matrix into per-protocol trials."""

    seeds = parse_seed_list(config.seeds)
    selected = ("disruption", "scalability", "placement") if config.campaign == "all" else (config.campaign,)
    trials: List[CampaignTrial] = []

    def add_trials(campaign: str, scenario: str, authorities: int, clients: int, wireless_range: int, speed: Tuple[int, int], authority_layout: str, client_layout: str) -> None:
        for seed in seeds:
            for routing in PROTOCOLS:
                experiment_id = f"{campaign}_{scenario}_a{authorities}_c{clients}_r{wireless_range}_v{speed[0]}-{speed[1]}_s{seed}_{routing}"
                trials.append(
                    CampaignTrial(
                        campaign=campaign,
                        scenario_name=scenario,
                        routing=routing,
                        seed=seed,
                        authorities=authorities,
                        clients=clients,
                        wireless_range=wireless_range,
                        mobility_speed=speed,
                        authority_layout=authority_layout,
                        client_layout=client_layout,
                        experiment_id=experiment_id,
                    )
                )

    if "disruption" in selected:
        for wireless_range in DISRUPTION_RANGES:
            for speed in DISRUPTION_SPEEDS:
                add_trials("disruption", f"range_{wireless_range}_speed_{speed[0]}_{speed[1]}", 5, 10, wireless_range, speed, "uniform", "uniform")

    if "scalability" in selected:
        for authorities, clients in SCALABILITY_SIZES:
            add_trials("scalability", f"scale_a{authorities}_c{clients}", authorities, clients, 15, (3, 6), "uniform", "uniform")

    if "placement" in selected:
        for scenario in PLACEMENT_SCENARIOS:
            auth_layout = "edge_authorities" if scenario == "edge_authorities" else scenario
            client_layout = "uniform" if scenario == "edge_authorities" else scenario
            add_trials("placement", scenario, 5, 10, 15, (3, 6), auth_layout, client_layout)

    return trials


def trial_config(base: EmulationConfig, trial: CampaignTrial, output_file: str | Path) -> EmulationConfig:
    """Build an EmulationConfig for a campaign trial."""

    routing = normalize_routing_name(trial.routing)
    workload_size = base.workload_size or 3 * trial.clients
    return replace(
        base,
        campaign="",
        routing=routing,
        authorities=trial.authorities,
        clients=trial.clients,
        wireless_range=trial.wireless_range,
        random_seed=trial.seed,
        workload_seed=trial.seed,
        workload_size=workload_size,
        workload_interval=base.workload_interval if base.workload_interval != 1.5 else 3.0,
        mobility_min_v=trial.mobility_speed[0],
        mobility_max_v=trial.mobility_speed[1],
        peer_discovery_timeout=base.peer_discovery_timeout,
        scenario_name=trial.scenario_name,
        authority_layout=trial.authority_layout,
        client_layout=trial.client_layout,
        experiment_id=trial.experiment_id,
        output_file=str(output_file),
    )


def run_trial(base: EmulationConfig, trial: CampaignTrial, results_dir: str | Path, retries: int = 1) -> Path:
    """Run one isolated trial, retrying failures once by default."""

    root = Path(results_dir)
    root.mkdir(parents=True, exist_ok=True)
    output_path = root / trial.campaign / f"{trial.experiment_id}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    config = trial_config(base, trial, output_path)
    cmd = build_subprocess_command(config, trial.routing, output_path)

    last_error = None
    for attempt in range(retries + 1):
        try:
            subprocess.run(cmd, check=True)
            with output_path.open("r", encoding="utf-8") as f:
                stats = json.load(f)
            metadata = trial.metadata()
            stats.update({key: value for key, value in metadata.items() if key not in stats})
            with output_path.open("w", encoding="utf-8") as f:
                json.dump({"metadata": metadata, "stats": stats}, f, indent=2, sort_keys=True)
            return output_path
        except (subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt >= retries:
                break
    raise RuntimeError(f"Campaign trial failed after {retries + 1} attempt(s): {trial.experiment_id}") from last_error


def run_campaign(config: EmulationConfig) -> List[Path]:
    """Run a selected campaign and write per-run JSON plus summary.csv."""

    trials = expand_campaign(config)
    results_dir = Path(config.results_dir)
    manifest = {
        "campaign": config.campaign,
        "seeds": parse_seed_list(config.seeds),
        "duration": config.duration,
        "peer_discovery_timeout": config.peer_discovery_timeout,
        "trial_count": len(trials),
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    with (results_dir / "campaign_manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    outputs: List[Path] = []
    for index, trial in enumerate(trials, start=1):
        print(f"[{index}/{len(trials)}] {trial.experiment_id}")
        outputs.append(run_trial(config, trial, results_dir))

    records = load_run_records(outputs)
    write_summary_csv(records, results_dir / "summary.csv")
    return outputs
