import csv
import json

from meshpay.examples.emulation.aggregation import aggregate_records, load_run_records, write_summary_csv
from meshpay.examples.emulation.campaign import expand_campaign, parse_seed_list
from meshpay.examples.emulation.config import EmulationConfig
from meshpay.examples.emulation.scenarios import deterministic_positions
from meshpay.examples.emulation.workload import generate_deterministic_workload


def test_campaign_expansion_counts_are_balanced():
    config = EmulationConfig(campaign="all", seeds="1,2,3,4,5")
    trials = expand_campaign(config)

    assert len(trials) == 400
    assert {trial.routing for trial in trials} == {"sdn_dtn", "epidemic", "prophet", "spray_and_wait"}


def test_campaign_single_sweep_counts():
    assert len(expand_campaign(EmulationConfig(campaign="disruption", seeds="1"))) == 48
    assert len(expand_campaign(EmulationConfig(campaign="scalability", seeds="1,2"))) == 32
    assert len(expand_campaign(EmulationConfig(campaign="placement", seeds="1"))) == 16


def test_seed_parsing():
    assert parse_seed_list("1, 2,3") == [1, 2, 3]


def test_deterministic_workload_generation():
    first = generate_deterministic_workload(clients=4, size=12, seed=7)
    second = generate_deterministic_workload(clients=4, size=12, seed=7)

    assert first == second
    assert len(first) == 12
    assert all(item.sender != item.recipient for item in first)


def test_scenario_placement_is_deterministic():
    first = deterministic_positions(5, layout="clustered", seed=3, role="client")
    second = deterministic_positions(5, layout="clustered", seed=3, role="client")
    different = deterministic_positions(5, layout="corridor", seed=3, role="client")

    assert first == second
    assert first != different


def test_aggregation_summary_stats_and_normalized_overhead(tmp_path):
    records = [
        {"campaign": "disruption", "scenario_name": "s", "routing": "sdn_dtn", "authorities": 5, "clients": 10, "wireless_range": 15, "mobility_speed": "3-6", "finality_rate": 80, "successful_tx": 8, "submitted_payments": 10, "avg_latency_ms": 100, "control_bytes": 20, "data_bytes": 60},
        {"campaign": "disruption", "scenario_name": "s", "routing": "sdn_dtn", "authorities": 5, "clients": 10, "wireless_range": 15, "mobility_speed": "3-6", "finality_rate": 100, "successful_tx": 10, "submitted_payments": 10, "avg_latency_ms": 120, "control_bytes": 40, "data_bytes": 60},
    ]
    for record in records:
        record["total_bytes_per_success"] = (record["control_bytes"] + record["data_bytes"]) / record["successful_tx"]

    rows = aggregate_records(records)

    assert len(rows) == 1
    assert rows[0]["finality_rate_mean"] == 90
    assert rows[0]["runs"] == 2
    assert rows[0]["total_bytes_per_success_mean"] == 10


def test_load_records_accepts_campaign_wrapped_json(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps({"metadata": {"campaign": "placement", "authorities": 5}, "stats": {"routing": "epidemic", "successful_tx": 2, "control_bytes": 10, "data_bytes": 6}}))

    records = load_run_records([path])

    assert records[0]["campaign"] == "placement"
    assert records[0]["authorities"] == 5
    assert records[0]["total_bytes_per_success"] == 8
