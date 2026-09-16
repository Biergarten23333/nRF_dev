from pathlib import Path
from dataclasses import asdict
import inspect
import json
import shutil
from types import SimpleNamespace
import sys

import numpy as np
import pytest

import run_c2_authoritative_articulated_action04 as runner
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS


def _payload():
    group = {
        "accepted": True,
        "u1_calls": 1,
        "fixed_root_solver_calls": [True],
        "root_revision_before": 10,
        "root_revision_after": 11,
        "pose_revision_before": 20,
        "pose_revision_after": 21,
        "robust_revision_before": 30,
        "robust_revision_after": 31,
        "temporal_revision_before": 40,
        "temporal_revision_after": 40,
        "next_native200_temporal_delta": 1,
        "contact_owner_digest_before": "same-contact",
        "contact_owner_digest_after": "same-contact",
        "trusted_nodes": ["n0", "n1", "n2", "n3"],
        "direct_nodes": ["n0", "n1", "n2", "n3"],
        "propagated_nodes": ["n4", "n5", "n6", "n7", "n8", "n9"],
        "node_inventory": [f"n{index}" for index in range(10)],
        "changed_bias_nodes": ["n0", "n2"],
        "identity_error_maximum_m": 0.0,
        "bone_length_error_maximum_m": 0.0,
        "contact_constraint_count": 1,
        "maximum_foothold_residual_m": 0.01,
        "contact_limit_m": 0.12,
        "contact_snapshot_supplied": True,
        "root_position_flat": [1.0, 2.0, 3.0],
        "root_covariance_flat": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
        "joint_covariance_status": "UNAVAILABLE_NOT_PROPAGATED",
        "root_joint_cross_covariance_status": "UNAVAILABLE_NOT_PROPAGATED",
        "native_period_s": 0.005,
        "pose_query_ns": [[10, 11], [20, 21]],
    }
    return runner.RawBranchPayload(
        counts={
            "imu_metric": 1000,
            "imu_context": 6,
            "imu_temporal_closure": 1,
            "groups": 41,
            "sweeps": 410,
            "links": 3266,
        },
        groups=tuple({"sequence": index, **group} for index in range(41)),
        runtime={
            "wall_s": 1.0,
            "maximum_rss_kib": 1000.0,
            "group_service_p99_ms": 10.0,
            "group_service_maximum_ms": 20.0,
            "effective_utilization": 0.1,
        },
        provenance={
            "loader": "NO_RAW_STUB",
            "source_sha256_before": {"source": "same"},
            "source_sha256_after": {"source": "same"},
            "data_sha256_before": {"data": "same"},
            "data_sha256_after": {"data": "same"},
        },
    )


def _raw_argv(**updates):
    values = {
        "action": runner.ACTION,
        "start": "0",
        "duration": "5",
        "attempt": "1",
        "authorized": runner._sha256(Path(runner.__file__).resolve()),
        "output": str(runner.EXPECTED_RAW_OUTPUT),
    }
    values.update(updates)
    return [
        "--action", values["action"],
        "--start-s", values["start"],
        "--duration-s", values["duration"],
        "--attempt", values["attempt"],
        "--authorized-runner-sha256", values["authorized"],
        "--output", values["output"],
    ]


def test_exact_raw_binary_dispatch_reaches_full_gate_path_with_no_raw_stub():
    arguments = runner._parse_args(_raw_argv())
    calls = []

    def no_raw_loader(request):
        calls.append(request)
        return _payload()

    payload, gates = runner._evaluate_raw_payload(arguments.request, no_raw_loader)

    assert calls == [runner.RawRunRequest(
        runner.ACTION, 0.0, 5.0, 1,
        runner._sha256(Path(runner.__file__).resolve()),
    )]
    assert payload.provenance["loader"] == "NO_RAW_STUB"
    assert all(gates.values())


@pytest.mark.parametrize(
    "updates",
    [
        {"action": "03_shoulder_right"},
        {"action": "H01_boxing"},
        {"start": "0.005"},
        {"duration": "0"},
        {"duration": "5.001"},
        {"duration": "nan"},
        {"attempt": "2"},
        {"authorized": "0" * 64},
        {"output": "logs/not_the_preregistered_raw_output"},
    ],
)
def test_bad_raw_contract_rejects_before_loader(updates):
    calls = []

    def forbidden_loader(_request):
        calls.append(True)
        raise AssertionError("loader must remain unreachable")

    with pytest.raises(SystemExit):
        arguments = runner._parse_args(_raw_argv(**updates))
        runner._evaluate_raw_payload(arguments.request, forbidden_loader)
    assert calls == []


@pytest.mark.parametrize("forbidden", ["--full", "--retry", "--hxx"])
def test_unknown_expansion_flags_reject_before_loader(forbidden):
    with pytest.raises(SystemExit):
        runner._parse_args([*_raw_argv(), forbidden])


def test_raw_mode_requires_every_explicit_execution_owner():
    with pytest.raises(SystemExit):
        runner._parse_args(["--output", str(runner.EXPECTED_RAW_OUTPUT)])


def test_dry_mode_preserves_no_raw_interface_and_rejects_raw_arguments():
    arguments = runner._parse_args([
        "--dry-run", "--output", str(runner.EXPECTED_DRY_OUTPUT),
    ])
    assert arguments.dry_run
    assert arguments.request == runner.RawRunRequest(
        runner.ACTION, 0.0, 5.0, 1,
        runner._sha256(Path(runner.__file__).resolve()),
    )
    with pytest.raises(SystemExit):
        runner._parse_args([
            "--dry-run", "--action", runner.ACTION,
            "--output", str(runner.EXPECTED_DRY_OUTPUT),
        ])


def test_short_positive_duration_uses_scaled_native200_inventory_gate():
    request = runner.RawRunRequest(
        runner.ACTION, 0.0, 1.0, 1,
        runner._sha256(Path(runner.__file__).resolve()),
    )
    source = _payload()
    payload = runner.RawBranchPayload(
        counts={**source.counts, "imu_metric": 200},
        groups=source.groups,
        runtime=source.runtime,
        provenance=source.provenance,
    )
    _actual, gates = runner._evaluate_raw_payload(request, lambda _request: payload)
    assert all(gates.values())


def test_real_loader_is_a_concrete_articulated_path_not_a_placeholder():
    names = set(runner._real_action04_loader.__code__.co_names)
    assert "AuthoritativeArticulatedFusion" in names
    assert "decode_measurements" in names
    assert "NotImplementedError" not in names
    assert Path(runner._real_action04_loader.__code__.co_filename).resolve() == Path(
        runner.__file__
    ).resolve()
    constants = repr(runner._real_action04_loader.__code__.co_consts).lower()
    assert "run_c2_h01" not in constants
    assert "run_c2_h02" not in constants
    assert "run_c2_robust_authoritative_root_u8_action04" not in constants
    source = inspect.getsource(runner._real_action04_loader)
    assert "body_proxy_at_fraction" not in source
    assert "ankle_proxy = ankle_proxy_from_pose_owner" in source
    assert source.count("pose_at_frame(") >= 3


def test_single_accepted_pose_owner_drives_contact_epoch_and_fk_identity():
    import hashlib
    from scipy.spatial.transform import Rotation
    from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
    from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
    from test_c2_authoritative_articulated_fusion import _engine_and_packet

    engine, _packet = _engine_and_packet()
    identity = np.array([1.0, 0.0, 0.0, 0.0])
    moved_xyzw = Rotation.from_rotvec([0.25, 0.0, 0.0]).as_quat()
    moved_wxyz = moved_xyzw[[3, 0, 1, 2]]
    action_data = {
        segment: {
            "quat_world_segment_wxyz": np.stack([identity, identity]).copy()
        }
        for segment in SEGMENTS
    }
    action_data["shank_left"]["quat_world_segment_wxyz"][1] = moved_wxyz
    before = {
        segment: hashlib.sha256(
            row["quat_world_segment_wxyz"].tobytes()
        ).hexdigest()
        for segment, row in action_data.items()
    }
    rotations0, points0 = runner._accepted_articulated_pose_frame(
        action_data, SEGMENTS, 0, np.eye(3), engine.pose.geometry
    )
    rotations1, points1 = runner._accepted_articulated_pose_frame(
        action_data, SEGMENTS, 1, np.eye(3), engine.pose.geometry
    )
    root = np.array([1.0, 2.0, 3.0])
    footholds = {
        side: root + points0[f"ankle_{side}"] for side in ("left", "right")
    }
    assert max(
        np.linalg.norm(root + points0[f"ankle_{side}"] - footholds[side])
        for side in ("left", "right")
    ) <= np.finfo(float).eps
    assert np.linalg.norm(points1["ankle_left"] - points0["ankle_left"]) > 0.0
    np.testing.assert_array_equal(points1["ankle_right"], points0["ankle_right"])
    node_positions = {
        node: root + points1[point] for node, point in NODE_TO_PROXY_POINT.items()
    }
    np.testing.assert_array_equal(
        node_positions["BSF6C53"], root + points1["ankle_left"]
    )
    np.testing.assert_array_equal(
        node_positions["BSF8BC4"], root + points1["ankle_right"]
    )
    length = engine.pose.geometry.segment_length_m
    for proximal, distal, expected_length in (
        ("shoulder_left", "elbow_left", length["upper_arm_left"]),
        ("elbow_left", "wrist_left", length["forearm_left"]),
        ("shoulder_right", "elbow_right", length["upper_arm_right"]),
        ("elbow_right", "wrist_right", length["forearm_right"]),
        ("hip_left", "knee_left", length["thigh_left"]),
        ("knee_left", "ankle_left", length["shank_left"]),
        ("hip_right", "knee_right", length["thigh_right"]),
        ("knee_right", "ankle_right", length["shank_right"]),
    ):
        assert np.isclose(
            np.linalg.norm(points1[distal] - points1[proximal]), expected_length
        )
    for rotations in (rotations0, rotations1):
        assert all(np.isfinite(value).all() for value in rotations.values())
    after = {
        segment: hashlib.sha256(
            row["quat_world_segment_wxyz"].tobytes()
        ).hexdigest()
        for segment, row in action_data.items()
    }
    assert after == before


def test_real_loader_import_setup_reaches_raw_boundary_without_hxx(monkeypatch):
    actual_sha256 = runner._sha256
    raw_open_attempts = []

    def stop_before_raw(path):
        resolved = Path(path).resolve()
        if resolved.name == "fusion_host_raw.cobs.bin":
            raw_open_attempts.append(resolved)
            raise AssertionError("raw boundary must remain unreachable")
        return actual_sha256(resolved)

    from biospur_fusion.ingest.events import RecordType
    pose_clock, _paths = runner._load_sealed_action04_pose_clock()
    clock_document = json.loads(runner.POSE_CLOCK_TABLE.read_text(encoding="utf-8"))
    boot_epoch = int(clock_document["models"]["BSFC2CC"]["boot_epoch"])
    decode_shaped = [
        SimpleNamespace(
            node_id="BSFC2CC", record_type=RecordType.IMU,
            node_timer_us=int(pose_clock.timer_us[index]), global_time_ns=None,
            boot_epoch=boot_epoch,
            payload={"acc_raw": [2048, 0, 0], "gyro_raw": [0, 0, 0]},
            sequence=index,
        )
        for index in range(900, 1033)
    ]
    before = set(sys.modules)
    monkeypatch.setattr(runner, "_sha256", stop_before_raw)
    request = runner.RawRunRequest(
        runner.ACTION,
        0.0,
        5.0,
        1,
        actual_sha256(Path(runner.__file__).resolve()),
    )
    stages = []
    with pytest.raises(
        RuntimeError, match="CONTROLLED_DECODE_ADAPTER_BOUNDARY_SENTINEL"
    ):
        runner._real_action04_loader(
            request,
            _decoded_imu_sentinel=decode_shaped,
            _stage_observer=stages.append,
        )
    added = set(sys.modules) - before

    assert raw_open_attempts == []
    assert not any("run_c2_h01" in name.lower() for name in added)
    assert not any("run_c2_h02" in name.lower() for name in added)
    assert "run_c2_robust_authoritative_root_u8_action04" not in added
    assert stages == [
        "loader.enter",
        "loader.imports.complete",
        "pregroup.clock_owner.complete",
        "pregroup.decoded_sentinel.complete",
    ]


def test_observability_preserves_success_payload_and_gate_bytes():
    request = runner.RawRunRequest(
        runner.ACTION, 0.0, 5.0, 1,
        runner._sha256(Path(runner.__file__).resolve()),
    )
    baseline_payload, baseline_gates = runner._evaluate_raw_payload(
        request, lambda _request: _payload()
    )
    stages = []

    def observed_loader(_request):
        stages.extend(("loader.enter", "loader.success.complete"))
        return _payload()

    observed_payload, observed_gates = runner._evaluate_raw_payload(
        request, observed_loader
    )
    baseline_bytes = json.dumps(
        asdict(baseline_payload), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    observed_bytes = json.dumps(
        asdict(observed_payload), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert stages == ["loader.enter", "loader.success.complete"]
    assert observed_bytes == baseline_bytes
    assert observed_gates == baseline_gates


def test_failure_evidence_records_deterministic_stage_and_full_traceback(tmp_path):
    stages = ("loader.enter", "pregroup.pose_owner.complete", "group.0.prepare.begin")

    def fail_at_owned_boundary():
        raise ValueError("injected read-only buffer")

    try:
        fail_at_owned_boundary()
    except ValueError as error:
        evidence = runner._failure_evidence(
            error, stage_trace=stages, runner_sha256="a" * 64
        )
    assert evidence["failure_type"] == "ValueError"
    assert evidence["failure_message"] == "injected read-only buffer"
    assert evidence["last_stage_marker"] == "group.0.prepare.begin"
    assert evidence["stage_trace"] == list(stages)
    traceback_text = "".join(evidence["traceback"])
    assert "fail_at_owned_boundary" in traceback_text
    assert "ValueError: injected read-only buffer" in traceback_text

    target = tmp_path / "FAILURE.json"
    runner._write_fresh_json_atomic(target, evidence)
    assert json.loads(target.read_text(encoding="utf-8")) == evidence
    with pytest.raises(RuntimeError, match="already exists"):
        runner._write_fresh_json_atomic(target, evidence)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["FAILURE.json"]


def test_failure_recording_does_not_mutate_authoritative_owners(tmp_path):
    from test_c2_authoritative_articulated_fusion import _engine_and_packet

    engine, _packet = _engine_and_packet(clock_owner_sha256="e" * 64)
    before = {
        "root": engine.root.publication_token().digest,
        "pose": engine.pose.publication_token().digest,
        "robust": engine.robust.snapshot(),
    }
    try:
        raise RuntimeError("injected observability failure")
    except RuntimeError as error:
        evidence = runner._failure_evidence(
            error,
            stage_trace=("loader.enter", "group.0.admit.begin"),
            runner_sha256="b" * 64,
        )
    runner._write_fresh_json_atomic(tmp_path / "FAILURE.json", evidence)
    after = {
        "root": engine.root.publication_token().digest,
        "pose": engine.pose.publication_token().digest,
        "robust": engine.robust.snapshot(),
    }
    assert after == before


def test_production_stage_markers_preserve_logical_order_and_stay_out_of_gates():
    source = inspect.getsource(runner._real_action04_loader)
    ordered_fragments = (
        'stage("loader.enter")',
        'stage("loader.imports.complete")',
        'stage("pregroup.clock_owner.complete")',
        'stage("pregroup.raw_hash.complete")',
        'stage("pregroup.layout_calibration.complete")',
        'stage("pregroup.pose_owner.complete")',
        'stage("pregroup.range_groups.complete")',
        'stage("pregroup.measurement_decode.complete")',
        'stage("pregroup.pelvis_vqf.complete")',
        'stage("pregroup.ankle_decode.complete")',
        'stage("pregroup.temporal_closure.complete")',
        'stage("pregroup.static_packets.complete")',
        'stage("pregroup.axis_model.complete")',
        'stage("pregroup.articulated_engine.complete")',
        'stage(f"group.{group_index}.prepare.begin")',
        'stage(f"group.{group_index}.prepare.complete")',
        'stage(f"group.{group_index}.source_pair.begin")',
        'stage(f"group.{group_index}.source_pair.complete")',
        'stage(f"group.{group_index}.epoch.begin")',
        'stage(f"group.{group_index}.epoch.complete")',
        'stage(f"group.{group_index}.admit.begin")',
        'stage(f"group.{group_index}.admit.complete")',
        'stage(f"group.{group_index}.audit.complete")',
        'stage("loader.success.complete")',
    )
    offsets = [source.index(fragment) for fragment in ordered_fragments]
    assert offsets == sorted(offsets)
    assert "stage_trace" not in inspect.getsource(runner._evaluate_raw_payload)


def test_stage_callback_time_is_excluded_from_production_service_and_wall_metrics():
    source = inspect.getsource(runner._real_action04_loader)
    assert "service_observation_started = observation_overhead_s" in source
    assert "observation_overhead_s - service_observation_started" in source
    assert "elapsed = time.monotonic() - started - observation_overhead_s" in source


def test_neutral_native200_support_preserves_generic_imu_and_contact_contracts():
    import c2_native200_contact_support as support
    from biospur_fusion.ingest.events import RecordType
    from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
    from test_c2_authoritative_articulated_fusion import _engine_and_packet

    engine, _packet = _engine_and_packet(clock_owner_sha256="e" * 64)
    mapping_owner = engine.native200_clock_mapping_owner(
        node=support.PELVIS_NODE, clock_owner_sha256="e" * 64
    )

    class Clock:
        boot_epoch = mapping_owner.boot_epoch
        a_ns_per_us = mapping_owner.a_ns_per_us
        b_ns = mapping_owner.b_ns

        @staticmethod
        def seconds(timer_us):
            return mapping_owner.global_ns(timer_us) * 1e-9

    events = [
        SimpleNamespace(
            node_id=support.PELVIS_NODE,
            record_type=RecordType.IMU,
            node_timer_us=400_000 + 5_000 * index,
            global_time_ns=mapping_owner.global_ns(400_000 + 5_000 * index),
            boot_epoch=mapping_owner.boot_epoch,
            payload={"acc_raw": [2048, 0, 0], "gyro_raw": [0, 0, 0]},
            sequence=index,
        )
        for index in range(120)
    ]
    rows, audit = support.pelvis_imu(events, Clock(), 1_000_000_000, 0.0)
    assert len(rows) == 120
    assert audit["vqf_instances"] == 1
    assert audit["sample_period_argument_s"] == 0.005
    assert all(np.isfinite(row["rotation_world"]).all() for row in rows)
    decode_shaped_events = [
        SimpleNamespace(**{**event.__dict__, "global_time_ns": None})
        for event in events
    ]
    owned_rows, owned_audit = support.pelvis_imu(
        decode_shaped_events, Clock(), 1_000_000_000, 0.0,
        include_source_ticks=True, clock_mapping_owner=mapping_owner,
    )
    assert owned_rows[0]["source_timer_us"] == 400_000
    assert owned_rows[1]["source_timer_us"] - owned_rows[0]["source_timer_us"] == 5_000
    assert owned_rows[0]["source_global_ns"] == mapping_owner.global_ns(400_000)
    assert owned_rows[0]["source_clock_domain"] == "B306_TIMER2"
    assert owned_audit["source_tick_owner"]["boot_epoch"] == mapping_owner.boot_epoch
    previous, current = runner._strict_native200_pair_before(
        owned_rows, owned_rows[-1]["source_global_ns"] + 1
    )
    source_pair = engine.native200_source_pair(
        clock_mapping_owner=mapping_owner,
        previous_timer_us=previous["source_timer_us"],
        current_timer_us=current["source_timer_us"],
        previous_global_ns=previous["source_global_ns"],
        current_global_ns=current["source_global_ns"],
    )
    epoch = engine.epoch(
        measurement_time_s=(source_pair.current_global_ns + 1) * 1e-9,
        availability_time_s=(source_pair.current_global_ns + 2) * 1e-9,
        previous_orientation_time_s=source_pair.previous_global_ns * 1e-9,
        base_rotations_world={segment: np.eye(3) for segment in SEGMENTS},
        previous_correction_rotvec={segment: np.zeros(3) for segment in SEGMENTS},
        provenance="DECODE_SHAPED_SOURCE_TICK_TRAVERSAL",
        native200_source_pair=source_pair,
    )
    assert epoch.native200_source_pair is source_pair
    with pytest.raises(RuntimeError, match="duplicate or reordered"):
        support.pelvis_imu(
            list(reversed(decode_shaped_events)), Clock(), 1_000_000_000, 0.0,
            include_source_ticks=True, clock_mapping_owner=mapping_owner,
        )
    inconsistent = list(decode_shaped_events)
    inconsistent[100] = SimpleNamespace(
        **{**inconsistent[100].__dict__, "global_time_ns": 1}
    )
    with pytest.raises(RuntimeError, match="mapping mismatch"):
        support.pelvis_imu(
            inconsistent, Clock(), 1_000_000_000, 0.0,
            include_source_ticks=True, clock_mapping_owner=mapping_owner,
        )

    def proxy(fraction):
        return (
            {
                "BSF6C53": np.array([fraction, 0.0, 0.1]),
                "BSF8BC4": np.array([0.0, fraction, 0.1]),
            },
            {},
            0,
        )

    interpolate = support.ankle_proxy_interpolator(proxy, 10.0, 11.0)
    positions, velocities = interpolate(10.5)
    assert positions["left"] == pytest.approx([0.5, 0.0, 0.1])
    assert positions["right"] == pytest.approx([0.0, 0.5, 0.1])
    assert velocities["left"] == pytest.approx([1.0, 0.0, 0.0])
    assert velocities["right"] == pytest.approx([0.0, 1.0, 0.0])


def test_strict_native200_pair_uses_two_observed_ticks_and_rejects_bad_ownership():
    def row(timer_us, *, global_ns=None, boot=7, domain="B306_TIMER2"):
        return {
            "source_node": "BSFC2CC",
            "source_boot_epoch": boot,
            "source_timer_us": timer_us,
            "source_global_ns": timer_us * 1000 if global_ns is None else global_ns,
            "source_clock_domain": domain,
        }

    rows = [row(40_000), row(45_000), row(50_000)]
    previous, current = runner._strict_native200_pair_before(rows, 50_000_001)
    assert previous["source_timer_us"] == 45_000
    assert current["source_timer_us"] == 50_000
    assert current["source_timer_us"] - previous["source_timer_us"] == 5_000

    bad_rows = (
        [row(40_000)],
        [row(40_000), {**row(45_000), "source_timer_us": 46_000}],
        [row(40_000), row(45_000, boot=8)],
        [row(40_000), row(45_000, domain="OTHER")],
        [row(45_000), row(40_000)],
        [row(40_000), row(45_000, global_ns=40_000_000)],
        [{"source_node": "BSFC2CC"}, row(45_000)],
    )
    for bad in bad_rows:
        with pytest.raises(RuntimeError):
            runner._strict_native200_pair_before(bad, 60_000_000)


def _owned_publication_rows(owner, timers):
    return [
        {
            "time_s": owner.global_ns(timer_us) * 1e-9,
            "sequence": index,
            "source_node": owner.node,
            "source_boot_epoch": owner.boot_epoch,
            "source_timer_us": timer_us,
            "source_global_ns": owner.global_ns(timer_us),
            "source_clock_domain": owner.clock_domain,
            "source_clock_mapping_digest": owner.digest,
        }
        for index, timer_us in enumerate(timers)
    ]


@pytest.mark.parametrize(
    "defect",
    (
        "missing", "gap", "reorder", "node", "boot", "domain", "digest",
        "mapping_global", "time_global",
    ),
)
def test_runner_rejects_invalid_publication_pair_before_any_owner_mutation(
    monkeypatch, defect,
):
    from test_c2_authoritative_articulated_fusion import _engine_and_packet

    engine, _packet = _engine_and_packet(clock_owner_sha256="e" * 64)
    owner = engine.native200_clock_mapping_owner(
        node="BSFC2CC", clock_owner_sha256="e" * 64
    )
    rows = _owned_publication_rows(owner, (40_000, 45_000, 50_000))
    if defect == "missing":
        rows[-1].pop("source_timer_us")
    elif defect == "gap":
        rows[-1]["source_timer_us"] = 51_000
    elif defect == "reorder":
        rows[-2], rows[-1] = rows[-1], rows[-2]
    elif defect == "node":
        rows[-1]["source_node"] = "OTHER"
    elif defect == "boot":
        rows[-1]["source_boot_epoch"] += 1
    elif defect == "domain":
        rows[-1]["source_clock_domain"] = "OTHER"
    elif defect == "digest":
        rows[-1]["source_clock_mapping_digest"] = "0" * 64
    elif defect == "mapping_global":
        rows[-1]["source_global_ns"] += 1_000
        rows[-1]["time_s"] = rows[-1]["source_global_ns"] * 1e-9
    elif defect == "time_global":
        rows[-1]["time_s"] += 0.001

    calls = []
    monkeypatch.setattr(engine, "add_imu", lambda _sample: calls.append("add_imu"))
    monkeypatch.setattr(
        engine, "sample_native200_pose",
        lambda **_kwargs: calls.append("sample_native200_pose"),
    )
    temporal = getattr(engine.pose, "_CausalArticulatedPose__hinge_temporal_owner")
    before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        repr(engine.robust.snapshot()),
        temporal._history_bytes(),
        temporal._snapshot_token().revision,
        b"contact-owner-is-not-reachable-from-the-imu-branch",
    )
    source = rows[-1]
    item = SimpleNamespace(sequence=source.get("sequence", 2), payload=object())
    base = {segment: np.eye(3) for segment in SEGMENTS}
    exact_base = lambda global_ns, _timer_us: (
        SimpleNamespace(pose_global_ns=global_ns), base
    )
    with pytest.raises((RuntimeError, ValueError)):
        runner._process_native200_timeline_event(
            engine=engine, item=item, event_time=float(source["time_s"]),
            all_pelvis_rows=tuple(rows),
            imu_row_by_sequence={int(row["sequence"]): row for row in rows},
            clock_mapping_owner=owner, pending_temporal_rows=[],
            exact_base_at_source=exact_base,
            base_pose_owner_digest="f" * 64,
        )
    after = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        repr(engine.robust.snapshot()),
        temporal._history_bytes(),
        temporal._snapshot_token().revision,
        b"contact-owner-is-not-reachable-from-the-imu-branch",
    )
    assert calls == []
    assert after == before


def test_first_submitted_action_row_uses_observed_pre_action_predecessor(
    monkeypatch,
):
    from test_c2_authoritative_articulated_fusion import _engine_and_packet

    engine, _packet = _engine_and_packet(clock_owner_sha256="e" * 64)
    owner = engine.native200_clock_mapping_owner(
        node="BSFC2CC", clock_owner_sha256="e" * 64
    )
    rows = _owned_publication_rows(
        owner, tuple(400_000 + 5_000 * index for index in range(105))
    )
    action_start_ns = rows[100]["source_global_ns"]
    submitted = [row for row in rows if row["source_global_ns"] >= action_start_ns]
    first = submitted[0]
    calls = []
    monkeypatch.setattr(engine, "add_imu", lambda _sample: calls.append(("add", None)))

    def publish(
        *, time_s, native200_source_pair,
        previous_base_pose, current_base_pose,
    ):
        calls.append(("publish", native200_source_pair))

    monkeypatch.setattr(engine, "sample_native200_pose", publish)
    item = SimpleNamespace(sequence=first["sequence"], payload=object())
    base = {segment: np.eye(3) for segment in SEGMENTS}

    def exact_base(global_ns, timer_us):
        expected = next(
            row for row in rows if row["source_timer_us"] == timer_us
        )
        assert expected["source_global_ns"] == global_ns
        return SimpleNamespace(pose_global_ns=global_ns), base

    assert runner._process_native200_timeline_event(
        engine=engine, item=item, event_time=first["time_s"],
        all_pelvis_rows=tuple(rows),
        imu_row_by_sequence={row["sequence"]: row for row in submitted},
        clock_mapping_owner=owner, pending_temporal_rows=[],
        exact_base_at_source=exact_base,
        base_pose_owner_digest="f" * 64,
    )
    pair = calls[1][1]
    assert [name for name, _value in calls] == ["add", "publish"]
    assert pair.previous_timer_us == rows[99]["source_timer_us"]
    assert pair.current_timer_us == rows[100]["source_timer_us"]
    assert pair.previous_global_ns == rows[99]["source_global_ns"]
    assert pair.current_global_ns == rows[100]["source_global_ns"]
    assert pair.current_timer_us - pair.previous_timer_us == 5_000
    assert rows[99]["source_global_ns"] < action_start_ns


def test_temporal_closure_selects_first_actual_owned_consecutive_sample():
    from test_c2_authoritative_articulated_fusion import _engine_and_packet

    engine, _packet = _engine_and_packet(clock_owner_sha256="e" * 64)
    owner = engine.native200_clock_mapping_owner(
        node="BSFC2CC", clock_owner_sha256="e" * 64
    )

    def row(timer_us, **updates):
        value = {
            "source_node": owner.node,
            "source_boot_epoch": owner.boot_epoch,
            "source_timer_us": timer_us,
            "source_global_ns": owner.global_ns(timer_us),
            "source_clock_domain": owner.clock_domain,
            "source_clock_mapping_digest": owner.digest,
        }
        value.update(updates)
        return value

    rows = [row(40_000), row(45_000), row(50_000), row(55_000)]
    previous, closure = runner._select_temporal_closure_pair(
        rows, owner.global_ns(45_000), owner
    )
    assert previous is rows[1]
    assert closure is rows[2]
    assert closure["source_timer_us"] - previous["source_timer_us"] == 5_000

    invalid = (
        rows[:2],
        [row(40_000), row(45_000), row(51_000)],
        [row(40_000), row(45_000), row(50_000, source_clock_domain="OTHER")],
        [row(40_000), row(45_000), row(50_000, source_boot_epoch=99)],
        [row(40_000), row(45_000), row(50_000, source_clock_mapping_digest="x")],
        [row(40_000), row(50_000), row(45_000)],
        [row(40_000), row(45_000), row(45_000)],
    )
    for bad in invalid:
        with pytest.raises(RuntimeError):
            runner._select_temporal_closure_pair(
                bad, owner.global_ns(45_000), owner
            )


def test_all_41_groups_close_once_and_final_requires_separate_native200():
    rows = []
    for index in range(41):
        group = {
            "temporal_revision_before": index,
            "temporal_revision_after": index,
            "next_native200_temporal_delta": None,
            "next_native200_time_s": None,
        }
        pending = [group]
        assert group["next_native200_temporal_delta"] is None
        assert runner._close_pending_temporal_row(
            pending,
            revision_before=index,
            revision_after=index + 1,
            native200_time_s=1.0 + 0.005 * index,
        ) == 1
        assert pending == []
        rows.append(group)
    assert len(rows) == 41
    assert all(row["temporal_revision_after"] == row["temporal_revision_before"] for row in rows)
    assert all(row["next_native200_temporal_delta"] == 1 for row in rows)

    pending = [{}, {}]
    with pytest.raises(RuntimeError, match="multiple UWB"):
        runner._close_pending_temporal_row(
            pending, revision_before=0, revision_after=1, native200_time_s=1.0
        )


def test_temporal_closure_inventory_is_separate_from_metric_and_context():
    request = runner.RawRunRequest(
        runner.ACTION, 0.0, 5.0, 1,
        runner._sha256(Path(runner.__file__).resolve()),
    )
    source = _payload()
    _actual, gates = runner._evaluate_raw_payload(request, lambda _request: source)
    assert gates["metric_imu"] and gates["context_imu"]
    assert gates["temporal_closure_imu"]

    missing_closure = runner.RawBranchPayload(
        counts={**source.counts, "imu_temporal_closure": 0},
        groups=source.groups,
        runtime=source.runtime,
        provenance=source.provenance,
    )
    _actual, gates = runner._evaluate_raw_payload(
        request, lambda _request: missing_closure
    )
    assert gates["metric_imu"] and gates["context_imu"]
    assert not gates["temporal_closure_imu"]


def test_payload_mutation_and_provenance_changes_are_measured_not_declared():
    request = runner.RawRunRequest(
        runner.ACTION, 0.0, 5.0, 1,
        runner._sha256(Path(runner.__file__).resolve()),
    )
    source = _payload()
    broken_group = dict(source.groups[0])
    broken_group["pose_revision_after"] = broken_group["pose_revision_before"]
    broken_groups = (broken_group, *source.groups[1:])
    broken_provenance = {
        **source.provenance,
        "source_sha256_after": {"source": "mutated"},
    }
    payload = runner.RawBranchPayload(
        counts=source.counts,
        groups=broken_groups,
        runtime=source.runtime,
        provenance=broken_provenance,
    )
    _actual, gates = runner._evaluate_raw_payload(request, lambda _request: payload)
    assert not gates["atomic_revision_transition"]
    assert not gates["sources_unchanged"]


def test_temporal_owner_must_wait_for_next_native200_sample():
    request = runner.RawRunRequest(
        runner.ACTION, 0.0, 5.0, 1,
        runner._sha256(Path(runner.__file__).resolve()),
    )
    source = _payload()

    changed_during_uwb = dict(source.groups[0])
    changed_during_uwb["temporal_revision_after"] += 1
    payload = runner.RawBranchPayload(
        counts=source.counts,
        groups=(changed_during_uwb, *source.groups[1:]),
        runtime=source.runtime,
        provenance=source.provenance,
    )
    _actual, gates = runner._evaluate_raw_payload(request, lambda _request: payload)
    assert not gates["atomic_revision_transition"]

    no_next_sample = dict(source.groups[0])
    no_next_sample["next_native200_temporal_delta"] = 0
    payload = runner.RawBranchPayload(
        counts=source.counts,
        groups=(no_next_sample, *source.groups[1:]),
        runtime=source.runtime,
        provenance=source.provenance,
    )
    _actual, gates = runner._evaluate_raw_payload(request, lambda _request: payload)
    assert not gates["next_native200_advances_temporal"]


def test_contact_profile_is_loaded_from_exact_sealed_derived_owner():
    profile = runner._load_sealed_contact_profile_document()
    assert profile["source_actions"] == ["00_initial_still", "17_final_still"]
    assert set(profile["profiles"]) == {"left", "right"}


def test_axis_adapter_fits_all_four_joints_from_sealed_base_owner():
    report, model, audit = runner._load_and_fit_sealed_axis_owner()
    expected = {"elbow_left", "elbow_right", "knee_left", "knee_right"}
    assert set(report["qmt_olsson_hinge_axes"]) == expected
    assert set(model) == expected
    assert set(audit["joints"]) == expected
    assert audit["report_sha256"] == runner.AXIS_OWNER_REPORT_SHA256
    assert audit["trajectory_sha256"] == runner.AXIS_OWNER_TRAJECTORY_SHA256


def test_corrected_output_report_cannot_be_used_as_axis_owner():
    output_report = runner.CORRECTED_OUTPUT_REPORT
    assert runner._sha256(output_report) == runner.CORRECTED_OUTPUT_REPORT_SHA256
    document = json.loads(output_report.read_text(encoding="utf-8"))
    assert "qmt_olsson_hinge_axes" not in document
    with pytest.raises(RuntimeError, match="exactly four"):
        runner._validate_axis_owner_document(document)


def test_failed_raw_attempt_remains_bound_and_nonpromoted():
    audit = runner._verify_failed_raw_nonpromoted()
    assert audit["promoted"] is False
    assert audit["seal_sha256"] == runner.FAILED_RAW_SEAL_SHA256


def test_failed_readonly_raw_attempt_remains_bound_and_nonpromoted():
    audit = runner._verify_failed_readonly_raw_nonpromoted()
    assert audit["promoted"] is False
    assert audit["seal_sha256"] == runner.FAILED_READONLY_RAW_SEAL_SHA256
    assert audit["failure"] == "ValueError: buffer source array is read-only"


def test_failed_readonly_audit_raw_attempt_remains_bound_and_nonpromoted():
    audit = runner._verify_failed_readonly_audit_raw_nonpromoted()
    assert audit["promoted"] is False
    assert audit["seal_sha256"] == runner.FAILED_READONLY_AUDIT_RAW_SEAL_SHA256
    assert audit["last_stage_marker"] == "group.0.admit.complete"


def test_failed_temporal_closure_raw_attempt_remains_bound_and_nonpromoted():
    audit = runner._verify_failed_temporal_closure_raw_nonpromoted()
    assert audit["promoted"] is False
    assert audit["seal_sha256"] == runner.FAILED_TEMPORAL_CLOSURE_RAW_SEAL_SHA256
    assert audit["last_stage_marker"] == "group.40.audit.complete"


def test_failed_dual_pose_owner_raw_attempt_remains_bound_and_nonpromoted():
    audit = runner._verify_failed_dual_pose_owner_raw_nonpromoted()
    assert audit["promoted"] is False
    assert audit["seal_sha256"] == runner.FAILED_DUAL_POSE_OWNER_RAW_SEAL_SHA256
    assert audit["foothold_rejections"] == 40


def test_sealed_action04_interval_and_common_global_pose_owner_are_exact():
    audit = runner._verify_common_global_pose_adapter()
    interval = audit["interval_owner"]
    rows = audit["fraction_rows"]

    assert interval["start_global_ns"] == 235_093_762_417_886
    assert interval["stop_global_ns_exclusive"] == 235_123_792_451_482
    assert interval["sha256"] == runner.ACTION_INTERVAL_LINEAGE_SHA256
    assert interval["seal_sha256"] == runner.ACTION_INTERVAL_SEAL_SHA256
    assert audit["bounded_prefix_stop_global_ns"] == 235_098_762_417_886
    assert rows[0] == {
        "fraction": 0.0,
        "query_global_ns": 235_093_762_417_886,
        "selected_action": runner.ACTION,
        "selected_frame": 1032,
        "selected_pose_global_ns": 235_093_758_190_563,
        "age_ns": 4_227_323.0,
    }
    assert [row["fraction"] for row in rows] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert [row["query_global_ns"] for row in rows] == [
        235_093_762_417_886,
        235_095_012_417_886,
        235_096_262_417_886,
        235_097_512_417_886,
        235_098_762_417_886,
    ]
    assert all(row["selected_action"] == runner.ACTION for row in rows)
    assert all(
        row["selected_pose_global_ns"] < row["query_global_ns"]
        and 0 < row["age_ns"] <= audit["maximum_pose_age_ns"]
        for row in rows
    )
    assert audit["equal_timestamp_reselection"]["strictly_preceding"] is True
    assert audit["exact_native200_publication"] == {
        "query_global_ns": 235_093_758_190_563,
        "selected_frame": 1032,
        "selected_timer_us": 4_392_893_151,
        "selected_pose_global_ns": 235_093_758_190_563,
        "same_tick": True,
    }
    assert audit["raw_uwb_opened"] is False
    assert audit["H01_H02_opened_or_hashed"] is False


def test_rev006_actual_owner_separates_native_contact_and_uwb_lookup_roles():
    revision = (
        runner.ROOT
        / "logs/c2_direct_native200_articulated_wiring_revision_006_20260907T074300Z"
    )
    direct_owner = (
        runner.ROOT
        / "src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py"
    )
    assert runner._sha256(revision / "SHA256SUMS") == (
        "282ef87484802a04aab67965648eb7bb73f8420793d05232c6f033f6e8128f03"
    )
    assert (
        "7c5249c387828fe195204c97a58ad4cb8635a60c04b67e022fcb1f6a37dc27ad  "
        "tools/run_c2_authoritative_articulated_action04.py"
    ) in (revision / "SOURCE_HASHES.txt").read_text(encoding="utf-8")
    assert runner._sha256(direct_owner) == (
        "1f3de5f1a2a9c808048bba6f30ac8e9e00e33edbced7378bbb2b150fcb0a08c1"
    )

    owner, _expected_paths = runner._load_sealed_action04_pose_clock()
    frame = 1032
    tick_ns = int(owner.global_ns[frame])
    timer_us = int(owner.timer_us[frame])
    exact = owner.exact_tick(tick_ns, source_timer_us=timer_us)
    equal_contact = owner.strict_floor(tick_ns)
    # An independently phased ankle clock falls between pelvis rows and must
    # consume the latest strictly earlier pelvis pose, never frame n+1.
    asynchronous_contact_ns = tick_ns + 1_234_567
    phased_contact = owner.strict_floor(asynchronous_contact_ns)
    equal_uwb = owner.strict_floor(tick_ns)

    assert exact.frame == frame and exact.pose_global_ns == tick_ns
    assert equal_contact.frame == frame - 1
    assert equal_uwb.frame == frame - 1
    assert phased_contact.frame == frame
    assert phased_contact.pose_global_ns < asynchronous_contact_ns
    assert int(owner.global_ns[frame + 1]) > asynchronous_contact_ns


def test_exact_source_base_resolution_uses_frames_1032_and_1033_not_action_start():
    owner, _expected_paths = runner._load_sealed_action04_pose_clock()
    previous_frame = 1032
    current_frame = 1033
    previous_global_ns = int(owner.global_ns[previous_frame])
    current_global_ns = int(owner.global_ns[current_frame])
    action_start_ns = runner.ACTION04_COMMON_START_NS
    queries = []

    def exact_base(global_ns, timer_us):
        queries.append(global_ns)
        selected = owner.exact_tick(global_ns, source_timer_us=timer_us)
        return selected, {segment: np.eye(3) for segment in SEGMENTS}

    previous, _base0 = exact_base(
        previous_global_ns, int(owner.timer_us[previous_frame])
    )
    current, _base1 = exact_base(
        current_global_ns, int(owner.timer_us[current_frame])
    )
    assert previous.frame == previous_frame
    assert current.frame == current_frame
    assert previous_global_ns < action_start_ns <= current_global_ns
    assert queries == [previous_global_ns, current_global_ns]
    assert action_start_ns not in queries


def test_complete_raw_preamble_reaches_raw_hash_sentinel_without_opening(
    tmp_path, monkeypatch,
):
    actual_sha256 = runner._sha256
    raw_hash_attempts = []

    def stop_at_raw_hash(path):
        resolved = Path(path).resolve()
        if resolved.name == "fusion_host_raw.cobs.bin":
            raw_hash_attempts.append(resolved)
            raise RuntimeError("CONTROLLED_RAW_HASH_OPEN_SENTINEL")
        return actual_sha256(resolved)

    monkeypatch.setattr(runner, "_sha256", stop_at_raw_hash)
    output = runner.ROOT / "logs" / f".pytest-{tmp_path.name}-raw-preamble"
    request = runner.RawRunRequest(
        runner.ACTION,
        0.0,
        5.0,
        1,
        actual_sha256(Path(runner.__file__).resolve()),
    )
    try:
        status = runner._run_raw(SimpleNamespace(output=output, request=request))

        assert status == 2
        assert len(raw_hash_attempts) == 1
        failure = json.loads((output / "FAILURE.json").read_text(encoding="utf-8"))
        assert failure["failure_type"] == "RuntimeError"
        assert failure["failure_message"] == "CONTROLLED_RAW_HASH_OPEN_SENTINEL"
        assert failure["last_stage_marker"] == "pregroup.clock_owner.complete"
        assert not (output / "GROUPS.json").exists()
        assert (output / "SHA256SUMS").is_file()
    finally:
        if output.exists():
            shutil.rmtree(output)


def test_fraction_mapping_rejects_local_timer_domain_and_bad_intervals():
    owner, _expected_paths = runner._load_sealed_action04_pose_clock()

    local_timer_query_ns = int(owner.timer_us[1032]) * 1000
    with pytest.raises(ValueError, match="NO_STRICTLY_PRECEDING_VALID_POSE"):
        owner.strict_floor(local_timer_query_ns)
    with pytest.raises(ValueError, match="fraction"):
        runner._fraction_to_common_global_ns(
            -0.01,
            action_start_global_ns=1,
            action_stop_global_ns=2,
        )
    with pytest.raises(ValueError, match="interval"):
        runner._fraction_to_common_global_ns(
            0.5,
            action_start_global_ns=2,
            action_stop_global_ns=2,
        )
