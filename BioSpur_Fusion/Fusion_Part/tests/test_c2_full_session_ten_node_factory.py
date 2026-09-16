from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import FullSessionContinuousReader
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
    CONTINUOUS_HINGE_RETENTION_CONTRACT,
    AuthoritativeContinuousHistoryOwner,
)
from biospur_fusion.c2_coupled_progressive.continuous_session_initializer import ContinuousSessionInitializer
from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
from biospur_fusion.c2_coupled_progressive.full_session_ten_node_ab import FullSessionTenNodeABCoordinator
from biospur_fusion.c2_uwb_root_world.full_session_body_pose import FullSessionBodyPoseOwner
from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import (
    ArticulatedRangeRejectionDiagnostic,
    AuthoritativeArticulatedFusion,
)
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import PoseTagLinkOwner
from biospur_fusion.root_r3 import RootState


TOOL = Path(__file__).resolve().parents[1] / "tools/build_c2_full_session_ten_node_ab.py"
BOUNDED_WRAPPER = TOOL.with_name("run_c2_full_session_ten_node_ab_bounded.sh")
SPEC = importlib.util.spec_from_file_location("build_c2_full_session_ten_node_ab", TOOL)
factory = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = factory
SPEC.loader.exec_module(factory)


def _articulated_diagnostic():
    return ArticulatedRangeRejectionDiagnostic(
        "a" * 64, "b" * 64, 7, 100, 200, None,
        tuple(f"node-{index}" for index in range(10)),
        "PROJECTED_FOOTHOLD_GATE_FAILURE", "FULL_EXISTING_NORMALIZED_RESIDUAL_OBJECTIVE",
        "JOINT_FULL_RESIDUAL", True, True, 1, ("ankle_left",),
        None, None, None, "optimizer-message", 17, 12.5,
        1.0, 0.9, 0.8, True, 2.0, 1.0, 1.5, 2e-10, True, 2e-10,
        True, True, True, True, True, True, 0.3, True,
        3, 4.0, 0.1, True, True, None, True, False, None,
    )


def _observer_admission(branch: str, index: int):
    return SimpleNamespace(
        branch=branch, packet_digest=f"{index + 1:064x}",
        epoch_digest=f"{index + 2:064x}",
        candidate_digest=f"{index + 3:064x}", bucket=index,
        source_sequence=index, source_identity=("pelvis", index),
        trusted_partition=("n0", "n1"), prepared_accepted=True,
        prepared_reason="ACCEPTED", commit_intent=branch == "B_UWB",
        commit_attempted=branch == "B_UWB",
        commit_succeeded=branch == "B_UWB",
        outcome=("BASELINE_NO_UWB_COMMIT" if branch == "A_BASELINE"
                 else "UWB_COMMIT_SUCCEEDED"),
        pre_pose_digest="d" * 64, result_pose_digest="d" * 64,
        diagnostic_digest=None, diagnostic=None,
    )


def _observer_pair(index: int, *, include_b: bool = True):
    return SimpleNamespace(
        provenance_digest=f"{index + 20:064x}",
        a=SimpleNamespace(
            reason="PREPARED_ADMISSION",
            admission=_observer_admission("A_BASELINE", index),
        ),
        b=(SimpleNamespace(
            reason="PREPARED_ADMISSION",
            admission=_observer_admission("B_UWB", index),
        ) if include_b else None),
    )


class _ObserverFailureCoordinator:
    def __init__(self, *, paired: bool, fail: bool) -> None:
        self._a = SimpleNamespace(counters={"PREPARED_COMPLETE_GROUP": 0})
        self._b = SimpleNamespace(counters={"PREPARED_COMPLETE_GROUP": 0})
        self._pending = {}
        self._deferred = None
        self.a_journal: list[object] = []
        self.b_journal: list[object] = []
        self.ab_journal: list[object] = []
        self.events = 0
        self.paired = paired
        self.fail = fail

    def consume_record_ticket(self, _ticket) -> None:
        self.a_journal.append(_observer_admission("A_BASELINE", 0))
        if self.paired:
            self.b_journal.append(_observer_admission("B_UWB", 0))
        self.ab_journal.append(_observer_pair(0, include_b=self.paired))
        if self.fail:
            raise ValueError("primary callback failure")

    def run(self, _reader):
        self.consume_record_ticket(object())
        return (
            SimpleNamespace(route_audit=SimpleNamespace(event_count=0)),
            SimpleNamespace(preworld_pose_omissions=0, events=0),
        )

    def audit(self):
        return SimpleNamespace(
            events=self.events,
            a_admission_journal=tuple(self.a_journal),
            b_admission_journal=tuple(self.b_journal),
            ab_transaction_journal=tuple(self.ab_journal),
            ab_transaction_total=len(self.ab_journal),
        )

    def diagnostic_publication(self):
        branch = SimpleNamespace(
            root_state=RootState(0.0, np.zeros(9), np.eye(9)),
            publication_revision=0, publication_digest="0" * 64,
            counters=(), journal=(),
        )
        return SimpleNamespace(a=branch, b=branch)


def _observer_owners(coordinator):
    return factory.ConstructedFullSessionTenNodeAB(
        object(), coordinator, object(), "1" * 64, "2" * 64,
    )


def test_real_factory_constructs_all_owners_without_consuming_reader():
    owners = factory.build()
    assert type(owners.reader) is FullSessionContinuousReader
    assert type(owners.coordinator) is FullSessionTenNodeABCoordinator
    assert type(owners.coordinator._body) is FullSessionBodyPoseOwner
    assert type(owners.coordinator._initializer) is ContinuousSessionInitializer
    assert owners.coordinator._initializer._state_owner.stationarity is None
    assert {binding.node_id for binding in owners.clock_owner.bindings} == set(NODE_TO_SEGMENT)
    assert set(owners.coordinator._initializer._static.clocks) == set(NODE_TO_SEGMENT)
    assert owners.reader._consumed is False
    assert owners.coordinator.audit().events == 0


def test_factory_b_drift_uses_exact_sealed_unqualified_diagnostic_config():
    owner = factory._b_consensus_drift_owner()
    assert owner.revision == 0
    assert owner.pending_count == 0
    assert np.array_equal(
        owner.cumulative_absolute_position_correction_m, np.zeros(3),
    )
    assert factory.EXPECTED[factory.CONSENSUS_DRIFT_CONFIG_SOURCE] == (
        "16cca24d4d68107676c1ab022f2792f3f2028fc0b5f97099ecc80e14c2bd05b0"
    )
    assert factory.asdict(owner.config) == {
        "minimum_lag_s": 0.32,
        "maximum_lag_s": 0.72,
        "update_period_s": 0.48,
        "minimum_consensus_pairs": 4,
        "rank_relative_tolerance": 1e-2,
        "maximum_velocity_step_mps": 0.50,
        "covariance_floor": 1e-12,
        "acceleration_bias": None,
    }
    expected_stream = factory.hashlib.sha256(factory.json.dumps({
        "session_id": factory.SESSION_ID,
        "role": "B_DIAGNOSTIC_UNQUALIFIED_CONTINUOUS_SHARED_ROOT_CONSENSUS_VELOCITY",
        "config_source_sha256": factory.EXPECTED[
            factory.CONSENSUS_DRIFT_CONFIG_SOURCE
        ],
        "config": factory.asdict(owner.config),
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert owner.stream_owner_digest == expected_stream

    owners = factory.build()
    initializer = owners.coordinator._initializer
    assert initializer._actions == frozenset((factory.SESSION_ID,))
    assert initializer._b_consensus_drift is not owner
    assert initializer._b_consensus_drift.owner_digest == owner.owner_digest


def test_factory_pose_and_provisional_history_share_exact_continuous_retention_contract():
    owners = factory.build()
    initializer = owners.coordinator._initializer
    pose = initializer._pose_factory()
    assert pose.hinge_temporal_retention_contract == CONTINUOUS_HINGE_RETENTION_CONTRACT

    pose_links = tuple(
        PoseTagLinkOwner(
            node, anchor, 2.0, 1, np.array([0.0, 0.0, 0.1]), np.zeros(3),
            0, 0, "0" * 64,
        )
        for node in sorted(initializer._static.clocks)
        for anchor in range(8)
    )
    state = RootState(0.0, np.zeros(9), np.eye(9))
    static = initializer._reference_materializer(
        initializer._static,
        initial_state=state,
        pose_links=pose_links,
        initial_state_provenance="FACTORY_RETENTION_CONTRACT_TEST",
    )
    engine = AuthoritativeArticulatedFusion(
        static_owner=static,
        pose=pose,
        native200_clock_owner_sha256=initializer._clock_sha,
        native200_base_pose_owner_digest=initializer._base_pose_digest,
    )
    history = AuthoritativeContinuousHistoryOwner(
        engine=engine,
        a_sigma_owner=initializer._a_sigma,
        b_sigma_owner=initializer._b_sigma,
        b_shadow_provenance=initializer._shadow_provenance,
        history_provenance=initializer._history_provenance,
        hinge_retention_contract=CONTINUOUS_HINGE_RETENTION_CONTRACT,
    )
    assert history.hinge_retention_contract == CONTINUOUS_HINGE_RETENTION_CONTRACT


def test_factory_uses_mechanism_materializer_and_no_action00_or_stale_static_owner():
    source = TOOL.read_text()
    tree = ast.parse(source)
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any("diagnostic_c2_static_owner" in name for name in imports)
    assert not any("prospective_action00_initializer" in name for name in imports)
    assert "materialize_mechanism_reference_owner" in source
    assert "stationarity" not in source
    assert ".consume(" not in source


def test_cli_requires_explicit_construct_only_and_never_runs(monkeypatch, capsys):
    created = factory.ConstructedFullSessionTenNodeAB(
        type("Reader", (), {"_consumed": False})(), object(),
        type("Clock", (), {"bindings": ()})(), "1" * 64, "2" * 64,
    )
    monkeypatch.setattr(factory, "build", lambda: created)
    monkeypatch.setattr("sys.argv", [str(TOOL), "--construct-only"])
    assert factory.main() == 0
    assert '"status": "CONSTRUCTED_NOT_EXECUTED"' in capsys.readouterr().out


def test_execute_result_shape_and_atomic_new_output_without_reader(monkeypatch, tmp_path):
    root = RootState(2.0, np.arange(9.0), np.eye(9))
    branch = SimpleNamespace(
        root_state=root, publication_revision=3, publication_digest="a" * 64,
        counters=(("ACCEPTED", 1),), journal=(),
    )
    stream = SimpleNamespace(route_audit=SimpleNamespace(event_count=5))
    audit = SimpleNamespace(preworld_pose_omissions=2, events=5)
    coordinator = SimpleNamespace(
        consume_record_ticket=lambda _ticket: None,
        _a=None, _b=None, _pending={}, _deferred=None,
        audit=lambda: audit,
        run=lambda _reader: (stream, audit),
        diagnostic_publication=lambda: SimpleNamespace(a=branch, b=branch),
        body_audit=lambda: SimpleNamespace(imu_events=4),
    )
    owners = factory.ConstructedFullSessionTenNodeAB(
        object(), coordinator, object(), "1" * 64, "2" * 64,
    )
    result = factory.execute(owners)
    assert result["status"] == "RUNNABLE_DIAGNOSTIC"
    assert result["a"]["vector"] == list(np.arange(9.0))
    assert result["a"]["node_histogram_status"] == "UNAVAILABLE_NOT_RETAINED_BY_ENGINE"
    assert result["product_ready"] is result["scientific_pass"] is False
    output = tmp_path / "result.json"
    factory._write_json_atomic_new(output, result)
    with pytest.raises(FileExistsError):
        factory._write_json_atomic_new(output, result)


def test_whole_session_metric_formulas_checkpoint_schedule_and_unique_final(monkeypatch):
    positions = iter((np.array([0.0, 0.0, 0.0]),
                      np.array([3.0, 4.0, 0.0]),
                      np.array([3.0, 4.0, 12.0])))
    current = next(positions)

    class Coordinator:
        _pending = {}
        _deferred = None

        def __init__(self):
            self._a = SimpleNamespace(counters={"PREPARED_COMPLETE_GROUP": 0})
            self._b = SimpleNamespace(counters={"PREPARED_COMPLETE_GROUP": 0})
            self.events = 0

        def audit(self):
            return SimpleNamespace(events=self.events)

        def diagnostic_publication(self):
            branch = SimpleNamespace(
                root_state=RootState(1.0, np.r_[current, np.zeros(6)], np.eye(9)),
                publication_revision=1, counters=(
                    ("TEMPORAL_REJECTED", 2), ("PREPARED_COMPLETE_GROUP", 0),
                ),
            )
            return SimpleNamespace(a=branch, b=branch)

    coordinator = Coordinator()
    metrics = factory._WholeSessionMetrics(0.0)
    monkeypatch.setattr(factory.time, "monotonic", lambda: 10.0)
    metrics.checkpoint(coordinator, batches=1, final=False)
    current = next(positions)
    metrics._roots(coordinator)
    current = next(positions)
    metrics._roots(coordinator)
    coordinator.events = 250_000
    assert metrics.due(coordinator, batches=2)
    metrics.checkpoint(coordinator, batches=2, final=True)
    root = metrics.checkpoints[-1]["roots"]["a"]
    assert root["path_length_m"] == pytest.approx(17.0)
    assert root["displacement_m"] == pytest.approx(13.0)
    assert root["maximum_radius_m"] == pytest.approx(13.0)
    assert root["correction_m"] == pytest.approx(12.0)
    assert root["causal_temporal_contact_rejections"] == {"TEMPORAL_REJECTED": 2}
    assert [row["events"] for row in metrics.checkpoints] == [0, 250_000]
    assert sum(row["final"] for row in metrics.checkpoints) == 1
    with pytest.raises(RuntimeError, match="not unique"):
        metrics.checkpoint(coordinator, batches=2, final=True)


def test_execute_persists_bounded_final_failure_evidence(tmp_path):
    class Coordinator:
        consume_record_ticket = lambda self, _ticket: None
        _a = _b = None
        _pending = {}
        _deferred = None

        def audit(self):
            return SimpleNamespace(events=0)

        def run(self, _reader):
            raise RuntimeError("synthetic bounded failure")

    owners = factory.ConstructedFullSessionTenNodeAB(
        object(), Coordinator(), object(), "1" * 64, "2" * 64,
    )
    checkpoint = tmp_path / "checkpoint.json"
    result = factory.execute(owners, checkpoint_output=checkpoint)
    assert result["status"] == "FAILED"
    assert result["failure"] == {
        "type": "RuntimeError", "message": "synthetic bounded failure",
    }
    assert len(result["checkpoints"]) == 1
    assert result["checkpoints"][0]["final"] is True
    assert result["per_action_metrics"] is None
    assert checkpoint.exists()
    import json
    assert json.loads(checkpoint.read_text())["failure"] == result["failure"]


def test_callback_failure_remains_primary_when_observer_detects_unpaired_journals():
    result = factory.execute(_observer_owners(
        _ObserverFailureCoordinator(paired=False, fail=True),
    ))
    assert result["failure"] == {
        "type": "ValueError", "message": "primary callback failure",
    }
    assert result["observer_error"] == {
        "type": "RuntimeError",
        "message": "authoritative A/B transaction provenance is invalid",
    }
    journals = result["authoritative_admission_journals"]
    assert (journals["a"]["length"], journals["b"]["length"]) == (1, 0)
    assert journals["a"]["tail"] == {
        "identity": factory._jsonable(factory._admission_identity(
            _observer_admission("A_BASELINE", 0)
        )),
        "outcome": "BASELINE_NO_UWB_COMMIT",
    }
    assert journals["b"]["tail"] is None
    assert result["checkpoints"][-1]["roots"]["a"]["prepared_accepted"] == 0


def test_callback_failure_diagnostic_does_not_consume_valid_admission_pair():
    result = factory.execute(_observer_owners(
        _ObserverFailureCoordinator(paired=True, fail=True),
    ))
    assert result["failure"]["message"] == "primary callback failure"
    assert result["observer_error"] is None
    journals = result["authoritative_admission_journals"]
    assert (journals["a"]["length"], journals["b"]["length"]) == (1, 1)
    roots = result["checkpoints"][-1]["roots"]
    assert roots["a"]["prepared_accepted"] == 0
    assert roots["b"]["prepared_accepted"] == 0


def test_successful_callback_keeps_unpaired_admission_fatal():
    result = factory.execute(_observer_owners(
        _ObserverFailureCoordinator(paired=False, fail=False),
    ))
    assert result["failure"] == {
        "type": "RuntimeError",
        "message": "authoritative A/B transaction provenance is invalid",
    }
    assert result["observer_error"] is None
    assert result["authoritative_admission_journals"] is None


def test_observer_validation_is_read_only_until_explicit_consumption():
    coordinator = _ObserverFailureCoordinator(paired=True, fail=False)
    coordinator.consume_record_ticket(object())
    metrics = factory._WholeSessionMetrics(0.0)
    pairs = metrics._validated_unseen_admission_pairs(coordinator)
    assert len(pairs) == 1
    assert metrics.admission_pairs == 0
    assert metrics.accepted == {}
    assert metrics._last_ab_transaction_total == 0
    metrics.consume_authoritative_admissions(coordinator)
    assert metrics.admission_pairs == 1


def test_execute_consumes_more_than_bounded_journal_capacity_without_loss():
    def entry(branch, index, *, accepted):
        diagnostic = None if accepted else _articulated_diagnostic()
        return SimpleNamespace(
            branch=branch, packet_digest=f"{index + 1}" * 64,
            epoch_digest=f"{index + 3}" * 64,
            candidate_digest=f"{index + 5}" * 64, bucket=index,
            source_sequence=index, source_identity=("pelvis", index),
            trusted_partition=tuple(f"node-{item}" for item in range(9)),
            prepared_accepted=accepted,
            prepared_reason="ACCEPTED" if accepted else "TEMPORAL_REJECTED",
            commit_intent=branch == "B_UWB",
            commit_attempted=branch == "B_UWB" and accepted,
            commit_succeeded=branch == "B_UWB" and accepted,
            outcome=("BASELINE_NO_UWB_COMMIT" if branch == "A_BASELINE" else
                     ("UWB_COMMIT_SUCCEEDED" if accepted else
                      "PREPARED_REJECTED_NO_COMMIT")),
            pre_pose_digest="d" * 64, result_pose_digest="d" * 64,
            diagnostic_digest=None if diagnostic is None else "f" * 64,
            diagnostic=diagnostic,
        )

    class Coordinator:
        def __init__(self):
            self._a = SimpleNamespace(counters={"PREPARED_COMPLETE_GROUP": 0})
            self._b = SimpleNamespace(counters={"PREPARED_COMPLETE_GROUP": 0})
            self.a_journal, self.b_journal = [], []
            self.ab_journal = []
            self._pending = {}
            self._deferred = None
            self.events = 0

        def consume_record_ticket(self, _ticket):
            accepted = self.events == 0
            self.a_journal.append(entry("A_BASELINE", self.events, accepted=accepted))
            self.b_journal.append(entry("B_UWB", self.events, accepted=accepted))
            self.ab_journal.append(SimpleNamespace(
                provenance_digest=f"{self.events + 100:064x}",
                a=SimpleNamespace(reason="PREPARED_ADMISSION",
                                  admission=self.a_journal[-1]),
                b=SimpleNamespace(reason="PREPARED_ADMISSION",
                                  admission=self.b_journal[-1]),
            ))
            self.a_journal[:] = self.a_journal[-64:]
            self.b_journal[:] = self.b_journal[-64:]
            self.ab_journal[:] = self.ab_journal[-64:]
            self.events += 1
            self._a.counters["PREPARED_COMPLETE_GROUP"] = self.events
            self._b.counters["PREPARED_COMPLETE_GROUP"] = self.events

        def run(self, _reader):
            for _ in range(70):
                self.consume_record_ticket(object())
            return (SimpleNamespace(route_audit=SimpleNamespace(event_count=70)),
                    SimpleNamespace(preworld_pose_omissions=0, events=70))

        def audit(self):
            return SimpleNamespace(
                events=self.events,
                a_admission_journal=tuple(self.a_journal),
                b_admission_journal=tuple(self.b_journal),
                ab_transaction_journal=tuple(self.ab_journal),
                ab_transaction_total=self.events,
            )

        def diagnostic_publication(self):
            branch = SimpleNamespace(
                root_state=RootState(1.0, np.zeros(9), np.eye(9)),
                publication_revision=1, publication_digest="a" * 64,
                counters=(('PREPARED_COMPLETE_GROUP', self.events),), journal=(),
            )
            return SimpleNamespace(a=branch, b=branch)

        def body_audit(self):
            return SimpleNamespace(imu_events=0)

    coordinator = Coordinator()
    result = factory.execute(factory.ConstructedFullSessionTenNodeAB(
        object(), coordinator, object(), "1" * 64, "2" * 64,
    ))
    assert result["status"] == "RUNNABLE_DIAGNOSTIC"
    checkpoint = result["whole_session_checkpoints"][-1]
    a_metrics, b_metrics = checkpoint["roots"]["a"], checkpoint["roots"]["b"]
    assert (a_metrics["accepted_updates"], a_metrics["rejected_updates"]) == (1, 69)
    assert (b_metrics["accepted_updates"], b_metrics["rejected_updates"]) == (1, 69)
    assert a_metrics["credible_node_x_of_10_histogram"] == {"9": 70}
    assert b_metrics["credible_node_x_of_10_histogram"] == {"9": 70}
    assert b_metrics["causal_temporal_contact_rejections"] == {
        "TEMPORAL_REJECTED": 69,
    }
    assert a_metrics["latest_articulated_range_rejection"] == factory._jsonable(
        _articulated_diagnostic()
    )
    assert b_metrics["latest_articulated_range_rejection"] == factory._jsonable(
        _articulated_diagnostic()
    )
    assert len(result["whole_session_checkpoints"]) == 1
    assert checkpoint["final"] is True
    assert b_metrics["commit_succeeded"] == 1
    assert b_metrics["commit_outcomes"] == {
        "PREPARED_REJECTED_NO_COMMIT": 69, "UWB_COMMIT_SUCCEEDED": 1,
    }


def _wrapper_dummy(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)
    return path


def test_full_session_bounded_wrapper_success_failure_and_structure(tmp_path):
    success = _wrapper_dummy(tmp_path / "success", """
row='{"final":true,"roots":{"a":{"prepared_accepted":1,"prepared_rejected":0,"credible_node_x_of_10_histogram":{"9":1},"primary_articulated_accepted":0,"accepted_root_fallback":1,"diagnostic_count":1},"b":{"prepared_accepted":1,"prepared_rejected":0,"credible_node_x_of_10_histogram":{"9":1},"primary_articulated_accepted":0,"accepted_root_fallback":1,"diagnostic_count":1,"commit_intent":1,"commit_attempted":1,"commit_succeeded":1,"commit_outcomes":{"UWB_COMMIT_SUCCEEDED":1}}}}'
printf '{"status":"RUNNABLE_DIAGNOSTIC","stream_audit":{},"coordinator_audit":{},"body_audit":{},"a":{},"b":{},"whole_session_checkpoints":[%s]}\n' "$row" > "$C2_BOUNDED_OUTPUT/RESULT.json"
printf '{"status":"COMPLETE","checkpoints":[%s]}\n' "$row" > "$C2_BOUNDED_OUTPUT/RESULT.checkpoints.json"
exit 0
""")
    failure = _wrapper_dummy(tmp_path / "failure", "exit 7\n")
    empty = _wrapper_dummy(tmp_path / "empty", "exit 0\n")
    for name, child, expected, finalization in (
        ("success-output", success, 0, "PASS"),
        ("failure-output", failure, 7, "CHILD_FAILED"),
        ("empty-output", empty, 93, "RESULT_STRUCTURE_INVALID"),
    ):
        output = tmp_path / name
        completed = subprocess.run(
            (str(BOUNDED_WRAPPER), str(output), "--", str(child)),
            check=False, env={**os.environ, "C2_BOUNDED_TIMEOUT_SECONDS": "5"},
        )
        assert completed.returncode == expected
        assert (output / "COMMAND.txt").read_text() == f"{child} \n"
        status = (output / "STATUS.txt").read_text()
        assert f"finalization={finalization}" in status
        assert (output / "PROCESS_FINAL.txt").read_text() == ""
        if expected in (0, 7, 93):
            assert subprocess.run(
                ("sha256sum", "-c", "SHA256SUMS"), cwd=output,
                check=False, capture_output=True,
            ).returncode == 0
            assert output.stat().st_mode & 0o777 == 0o555
            assert all(item.stat().st_mode & 0o777 == 0o444
                       for item in output.iterdir())


def test_full_session_bounded_wrapper_rejects_status_mismatch_and_missing_metrics(tmp_path):
    cases = {
        "banana": (
            '{"status":"BANANA","stream_audit":{},"coordinator_audit":{},'
            '"body_audit":{},"a":{},"b":{},"whole_session_checkpoints":[{"final":true}]}',
            '{"status":"COMPLETE","checkpoints":[{"final":true}]}',
        ),
        "mismatch": (
            '{"status":"RUNNABLE_DIAGNOSTIC","stream_audit":{},"coordinator_audit":{},'
            '"body_audit":{},"a":{},"b":{},"whole_session_checkpoints":[{"final":true}]}',
            '{"status":"COMPLETE","checkpoints":[{"final":false}]}',
        ),
        "missing-metrics": (
            '{"status":"RUNNABLE_DIAGNOSTIC","stream_audit":{},"coordinator_audit":{},'
            '"body_audit":{},"a":{},"b":{},"whole_session_checkpoints":'
            '[{"final":true,"roots":{"a":{},"b":{}}}]}',
            '{"status":"COMPLETE","checkpoints":[{"final":true,"roots":{"a":{},"b":{}}}]}',
        ),
    }
    for name, (result, checkpoint) in cases.items():
        child = _wrapper_dummy(tmp_path / f"child-{name}", f"""
printf '%s\n' '{result}' > "$C2_BOUNDED_OUTPUT/RESULT.json"
printf '%s\n' '{checkpoint}' > "$C2_BOUNDED_OUTPUT/RESULT.checkpoints.json"
""")
        output = tmp_path / name
        completed = subprocess.run(
            (str(BOUNDED_WRAPPER), str(output), "--", str(child)), check=False,
            env={**os.environ, "C2_BOUNDED_TIMEOUT_SECONDS": "5"},
        )
        assert completed.returncode == 93
        status = (output / "STATUS.txt").read_text()
        assert "structure_gate=FAIL" in status
        assert "finalization=RESULT_STRUCTURE_INVALID" in status
        assert subprocess.run(("sha256sum", "-c", "SHA256SUMS"), cwd=output,
                              check=False, capture_output=True).returncode == 0
        assert output.stat().st_mode & 0o777 == 0o555


def test_full_session_bounded_wrapper_default_contract_is_explicit():
    source = BOUNDED_WRAPPER.read_text()
    assert ".venv-v0/bin/python tools/build_c2_full_session_ten_node_ab.py" in source
    assert '--execute --output "$output/RESULT.json"' in source
    assert "timeout --foreground --signal=TERM --kill-after=5s" in source
    assert "${C2_BOUNDED_TIMEOUT_SECONDS:-2400}" in source
    assert "52428800" in source
    assert "find src/biospur_fusion -type f -name '*.py'" in source
    assert '[[ -f "$input" && ! -L "$input" ]]' in source
    precedence = tuple(source.index(marker) for marker in (
        "if [[ $hash_gate == FAIL ]]", "elif [[ $residual_gate == FAIL ]]",
        "elif [[ $size_gate == FAIL ]]",
        "elif [[ $child_status == 0 && $structure_gate == FAIL ]]",
        "elif [[ $child_status != 0 ]]",
    ))
    assert precedence == tuple(sorted(precedence))
    assert all(f"{gate}_gate=%s" in source for gate in (
        "hash", "residual", "size", "structure",
    ))
