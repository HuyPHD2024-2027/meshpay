from meshpay.examples.emulation.arguments import parse_args
from meshpay.examples.emulation.config import BenchmarkStats, DEFAULT_WORKLOAD, EmulationConfig
from meshpay.examples.emulation.runner import build_subprocess_command


def test_argument_parsing_normalizes_legacy_sdn_routing_mode():
    config = parse_args(["--routing-mode", "sdn"])

    assert config.routing == "sdn_dtn"


def test_emulation_config_defaults_match_benchmark_behavior():
    config = EmulationConfig()

    assert config.authorities == 5
    assert config.clients == 3
    assert config.duration == 300
    assert config.wireless_range == 15
    assert not config.plot
    assert config.network_mode == "oppnet"
    assert config.wireless_interface == "mesh_80211s"
    assert config.routing == "both"
    assert config.policy_file == ""
    assert config.output_file == ""
    assert config.random_seed == 42
    assert config.mobility_min_x == 0
    assert config.mobility_max_x == 200
    assert config.mobility_min_y == 0
    assert config.mobility_max_y == 150
    assert config.mobility_min_v == 1
    assert config.mobility_max_v == 3
    assert config.peer_discovery_timeout == 30.0
    assert config.scenario_name == "single"
    assert config.workload_size == 0
    assert config.workload_interval == 1.5
    assert config.authority_layout == "uniform"
    assert config.client_layout == "uniform"
    assert config.workload == DEFAULT_WORKLOAD


def test_benchmark_stats_to_dict_preserves_json_keys():
    stats = BenchmarkStats(
        finality_rate=75.0,
        avg_latency_ms=12.5,
        control_bytes=100,
        data_bytes=200,
        avg_buffer_size=1.5,
        total_tx=4,
        successful_tx=3,
        successful_transaction_ids=["order1", "order2", "order3"],
        raw_successful_events=5,
        network_mode="oppnet",
        wireless_interface="mesh_80211s",
        routing="sdn_dtn",
        policy_file="policy.yaml",
    )

    payload = stats.to_dict()

    assert payload["finality_rate"] == 75.0
    assert payload["avg_latency_ms"] == 12.5
    assert payload["control_bytes"] == 100
    assert payload["data_bytes"] == 200
    assert payload["avg_buffer_size"] == 1.5
    assert payload["total_tx"] == 4
    assert payload["successful_tx"] == 3
    assert payload["successful_transaction_ids"] == ["order1", "order2", "order3"]
    assert payload["raw_successful_events"] == 5
    assert payload["network_mode"] == "oppnet"
    assert payload["wireless_interface"] == "mesh_80211s"
    assert payload["routing"] == "sdn_dtn"
    assert payload["policy_file"] == "policy.yaml"
    assert payload["submitted_payments"] == 0
    assert payload["tps"] == 0.0


def test_comparison_subprocess_command_forwards_cli_options():
    config = EmulationConfig(
        authorities=7,
        clients=4,
        duration=12,
        plot=True,
        network_mode="oppnet",
        wireless_interface="wifi_direct",
        policy_file="configs/policies/market_oppnet_policy.yaml",
    )

    cmd = build_subprocess_command(config, "sdn_dtn", "/tmp/sdn_stats.json", script_path="/tmp/bench.py")

    assert cmd[:4] == [cmd[0], "/tmp/bench.py", "--routing", "sdn_dtn"]
    assert "--plot" in cmd
    assert cmd[cmd.index("--network-mode") + 1] == "oppnet"
    assert cmd[cmd.index("--wireless-interface") + 1] == "wifi_direct"
    assert cmd[cmd.index("--policy-file") + 1] == "configs/policies/market_oppnet_policy.yaml"
    assert cmd[cmd.index("--output-file") + 1] == "/tmp/sdn_stats.json"
    assert cmd[cmd.index("--scenario-name") + 1] == "single"
    assert cmd[cmd.index("--workload-size") + 1] == str(len(config.workload))

