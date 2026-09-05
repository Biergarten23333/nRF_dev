from __future__ import annotations

from biospur_fusion.c2_uwb_calibration.coverage import (
    summarize_beacon_clock_health,
    summarize_clock_health,
    summarize_episode_coverage,
)
from biospur_fusion.ingest.events import EventStatus, RecordType, TypedEvent


NODES = tuple(f"BSF{index:04X}" for index in range(10))


def _event(node: str, timer_us: int, valid_count: int) -> TypedEvent:
    return TypedEvent(
        node_id=node,
        boot_epoch=0,
        record_type=RecordType.UWB,
        sequence=timer_us,
        node_timer_us=timer_us,
        global_time_ns=None,
        global_time_sigma_ns=None,
        master_arrival_ms=0,
        payload={
            "anchor_id": list(range(8)),
            "range_mm": [1000 + index for index in range(8)],
            "t_round_us": [1000 + 1000 * index for index in range(8)],
            "quality_percent": [100] * 8,
            "valid_mask": (1 << valid_count) - 1,
            "strobe_us": timer_us,
        },
        status=EventStatus.DECODED,
    )


def test_coverage_distinguishes_direct_partial_and_propagated_bins() -> None:
    models = {
        node: {"a_ns_per_us": 1000.0, "b_ns": 0.0} for node in NODES
    }
    events = [
        _event(NODES[0], 10_000, 8),
        _event(NODES[1], 10_000, 2),
        _event(NODES[2], 130_000, 7),
        _event(NODES[3], 250_000, 0),
    ]
    summary = summarize_episode_coverage(
        events,
        expected_nodes=NODES,
        clock_models=models,
        episode_start_global_ns=0,
        episode_stop_global_ns_exclusive=480_000_000,
    )
    assert summary["uwb_updates"] == 4
    assert summary["valid_links"] == 17
    assert summary["nodes"][NODES[0]]["fraction_updates_with_8_links"] == 1.0
    assert summary["nodes"][NODES[1]]["fraction_partial_or_missing_updates"] == 1.0
    assert summary["nodes"][NODES[2]]["fraction_direct_3d_candidate_updates"] == 1.0
    propagation = summary["propagation_availability"]
    assert propagation["expected_bins"] == 4
    assert propagation["fraction_bins_with_any_direct_node"] == 0.5
    assert propagation["direct_node_count_histogram"] == {
        "0": 2, "1": 2, "2": 0, "3": 0, "4": 0, "5": 0,
        "6": 0, "7": 0, "8": 0, "9": 0, "10": 0,
    }


def test_unsealed_node_is_rejected() -> None:
    models = {
        node: {"a_ns_per_us": 1000.0, "b_ns": 0.0} for node in NODES
    }
    event = _event("BSFFFFF", 10_000, 8)
    try:
        summarize_episode_coverage(
            [event],
            expected_nodes=NODES,
            clock_models=models,
            episode_start_global_ns=0,
            episode_stop_global_ns_exclusive=120_000_000,
        )
    except ValueError as error:
        assert "unsealed UWB node" in str(error)
    else:
        raise AssertionError("unsealed UWB node was accepted")


def test_clock_health_does_not_trust_weaker_aggregate_gate() -> None:
    healthy = {
        "clean_residual_p95_us": 499.0,
        "clean_residual_max_us": 999.0,
        "sigma_ns": 50_000.0,
        "clean_pairs": 300,
        "capture_span_coverage": 0.99,
        "max_clean_anchor_gap_s": 0.24,
    }
    degraded = {
        **healthy,
        "clean_residual_p95_us": 1137.0,
        "clean_residual_max_us": 1975.0,
        "sigma_ns": 2_288_000.0,
        "clean_pairs": 21,
        "capture_span_coverage": 0.80,
        "max_clean_anchor_gap_s": 5.16,
    }
    result = summarize_clock_health({"BSFGOOD": healthy, "BSFBAD": degraded})
    assert result["strict_residual_gate"] is False
    assert result["degraded_nodes"] == ["BSFBAD"]
    assert result["nodes"]["BSFGOOD"]["strict_residual_gate"] is True


def test_beacon_clock_rejects_listener_poll_or_response_dependency() -> None:
    model = {
        "residual_p95_us": 100.0,
        "residual_max_us": 200.0,
        "sigma_ns": 100_000.0,
        "drift_ppm": 10.0,
        "sf_mod16_agreement_fraction": 1.0,
        "timestamp_reversals": 0,
    }
    document = {
        "schema": "biospur.c2.beacon_only_clock_table.v1",
        "clock_contract": {
            "pass": True,
            "accepted_listener_record_kinds": ["LBD"],
            "forbidden_listener_record_kinds": ["LPD", "LRD"],
            "poll_or_response_measurements_consumed": True,
            "measurement_time_source": "B306_TIMER2",
            "same_model_applies_to": [
                "uwb.strobe_us", "uwb.frame_us", "imu.base_us", "imu.trigger_us",
            ],
        },
        "models": {node: model for node in NODES},
    }
    try:
        summarize_beacon_clock_health(document)
    except ValueError as error:
        assert "poll_response_not_consumed" in str(error)
    else:
        raise AssertionError("Listener poll/response clock dependency was accepted")


def test_beacon_clock_accepts_shared_timer_contract() -> None:
    model = {
        "residual_p95_us": 100.0,
        "residual_max_us": 200.0,
        "sigma_ns": 100_000.0,
        "drift_ppm": 10.0,
        "sf_mod16_agreement_fraction": 1.0,
        "timestamp_reversals": 0,
    }
    document = {
        "schema": "biospur.c2.beacon_only_clock_table.v1",
        "clock_contract": {
            "pass": True,
            "accepted_listener_record_kinds": ["LBD"],
            "forbidden_listener_record_kinds": ["LPD", "LRD"],
            "poll_or_response_measurements_consumed": False,
            "measurement_time_source": "B306_TIMER2",
            "same_model_applies_to": [
                "uwb.strobe_us", "uwb.frame_us", "imu.base_us", "imu.trigger_us",
            ],
        },
        "models": {node: model for node in NODES},
    }
    result = summarize_beacon_clock_health(document)
    assert result["strict_residual_gate"] is True
    assert result["degraded_nodes"] == []
