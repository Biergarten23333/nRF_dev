from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.diagnose_c2_full_session_continuous_delivery as delivery
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
    ContinuousAdmissionAudit,
)
from biospur_fusion.c2_coupled_progressive.full_session_ten_node_ab import (
    BranchTransactionDisposition,
    FullSessionTenNodeABCoordinator,
    PairedABTransactionAudit,
)

from biospur_fusion.c2_uwb_root_world.continuous_full_session import (
    load_continuous_session_inventory,
)
from biospur_fusion.c2_uwb_root_world.full_session_body_pose import SESSION_ID
from tools.diagnose_c2_full_session_continuous_delivery import (
    CHECKPOINT_EVENT_THRESHOLDS,
    EXPECTED_CONTAINER_RECORDS,
    EXPECTED_DELIVERED_RECORDS,
    EXPECTED_EVENTS,
    EXPECTED_INVENTORY_SHA256,
    EXPECTED_OPTIMIZED_POSE_SOURCE_SHA256,
    EXPECTED_REGIONS,
    MAX_OUTPUT_BYTES,
    INTERNAL_SECONDS,
    OUTER_SECONDS,
    SCHEMA,
    STATUS_PASS,
    _CompactChain,
    _admission_audit_digest,
    FullSessionDeliveryObserver,
    _canonical_digest,
    _inventory_contract_rows,
    _pending_summary,
    validate_result,
)


ROOT = Path(__file__).resolve().parents[1]


def _passing_result() -> dict:
    return {
        "schema": SCHEMA,
        "status": STATUS_PASS,
        "authorization": "DELIVERY_COMPLETE",
        "product_ready": False,
        "scientific_pass": False,
        "failure": None,
        "stop_reason": "EXACT_EOF",
        "limits": {
            "internal_seconds": INTERNAL_SECONDS,
            "outer_seconds": OUTER_SECONDS,
            "rlimit_as_bytes": 1 << 30,
            "threads": 1,
            "maximum_output_bytes": MAX_OUTPUT_BYTES,
            "retry": False,
            "resume_capable": False,
            "progress_checkpoints_are_metrics_only": True,
        },
        "boundary": {
            "container_records": EXPECTED_CONTAINER_RECORDS,
            "delivered_records": EXPECTED_DELIVERED_RECORDS,
            "events": EXPECTED_EVENTS,
            "regions": EXPECTED_REGIONS,
        },
        "provenance": {
            "optimized_pose_source_sha256": EXPECTED_OPTIMIZED_POSE_SOURCE_SHA256,
            "session_id": "FULL_SESSION_CONTINUOUS_00_TO_19",
        },
        "report_only": {
            "per_region_metrics_are_independent_verdicts": False,
            "per_record_displacement_sentinel_m": 0.10,
            "step_is_acceptance_gate": False,
        },
        "per_region_report_only": {
            str(index): {"verdict_scope": "REPORT_ONLY_NOT_INDEPENDENT"}
            for index in range(EXPECTED_REGIONS)
        },
        "gates": {"all": True},
    }


def test_full_inventory_is_exact_37_region_continuous_00_to_19_contract() -> None:
    inventory = load_continuous_session_inventory(ROOT)
    rows = [{
        "ordinal": int(region.ordinal),
        "region_id": str(region.region_id),
        "kind": str(region.kind),
        "action_id": None if region.action_id is None else str(region.action_id),
        "start_offset": int(region.start_offset),
        "stop_offset": int(region.stop_offset),
        "start_ns": int(region.start_ns),
        "stop_ns": int(region.stop_ns),
        "expected_sha256": region.expected_sha256,
        "observed_sha256": "0" * 64,
    } for region in inventory.regions]
    assert len(rows) == EXPECTED_REGIONS == 37
    assert _canonical_digest(_inventory_contract_rows(rows)) == (
        EXPECTED_INVENTORY_SHA256
    )
    actions = [row["action_id"] for row in rows if row["kind"] == "ACTION"]
    assert actions == ["00_initial_still"] + [
        f"{index:02d}_{name}" for index, name in (
            (2, "t_pose"), (3, "pelvis_hula_circle"),
            (4, "shoulder_left"), (5, "shoulder_right"),
            (6, "elbow_left"), (7, "elbow_right"),
            (8, "hip_left"), (9, "hip_right"),
            (10, "knee_left_seated"), (11, "knee_right_seated"),
            (12, "heel_raise_left"), (13, "heel_raise_right"),
            (14, "trunk_flex_extend"), (15, "trunk_axial_rotation"),
            (16, "squat"), (17, "final_still"),
            (18, "heel_to_butt_left"), (19, "heel_to_butt_right"),
        )
    ]
    assert SESSION_ID == "FULL_SESSION_CONTINUOUS_00_TO_19"


def test_compact_chain_is_order_sensitive_constant_size_and_deterministic() -> None:
    left = _CompactChain()
    right = _CompactChain()
    reversed_chain = _CompactChain()
    for value in ({"n": 1}, {"n": 2}, {"n": 3}):
        left.add(value)
        right.add(value)
    for value in ({"n": 3}, {"n": 2}, {"n": 1}):
        reversed_chain.add(value)
    assert left.result() == right.result()
    assert left.result()["count"] == 3
    assert left.result()["chain_sha256"] != reversed_chain.result()["chain_sha256"]
    assert set(left.result()) == {"count", "chain_sha256", "first", "last"}


def _pending(availability_ns: int, suffix: str) -> object:
    return SimpleNamespace(
        digest=(suffix * 64)[:64],
        admission_digest=((suffix + "a") * 64)[:64],
        root_plan_digest=((suffix + "b") * 64)[:64],
        availability_time_ns=availability_ns,
        trusted_nodes=("N0",), anchors_used=(0, 1),
    )


def _native_frame(source_global_ns: int = 10_000_000_000) -> object:
    return SimpleNamespace(
        digest="d" * 64, node="N0", boot_epoch=7,
        source_timer_us=source_global_ns // 1_000,
        source_global_ns=source_global_ns, publication_revision=11,
        source_frame=12, pose_publication_digest="e" * 64,
        raw_provenance=SimpleNamespace(
            record_index=13, start_offset=14, end_offset=15,
            encoded_sha256="f" * 64, sample_index=16,
        ),
    )


def test_eof_pending_allows_only_strictly_future_packages() -> None:
    future = _pending(10_000_000_001, "1")
    result = _pending_summary((future,), _native_frame())
    assert result["all_pending_strictly_future"] is True
    assert result["eligible_pending_count"] == 0
    assert result["count"] == 1
    assert result["oldest_availability_time_ns"] == 10_000_000_001
    assert result["authenticated_final_native_frame"] == {
        "frame_digest": "d" * 64,
        "node": "N0",
        "boot_epoch": 7,
        "source_timer_us": 10_000_000,
        "source_global_ns": 10_000_000_000,
        "publication_revision": 11,
        "source_frame": 12,
        "pose_publication_digest": "e" * 64,
        "raw_identity": [13, 14, 15, "f" * 64, 16],
    }
    assert _pending_summary(
        (), _native_frame(),
    )["all_pending_strictly_future"] is True


@pytest.mark.parametrize("availability_ns", (9_999_999_999, 10_000_000_000))
def test_eof_pending_rejects_past_or_exactly_eligible_package(
    availability_ns: int,
) -> None:
    result = _pending_summary(
        (_pending(availability_ns, "2"),), _native_frame(),
    )
    assert result["all_pending_strictly_future"] is False
    assert result["eligible_pending_count"] == 1


def test_gap_clear_has_compact_exact_package_disposition_chain() -> None:
    observer = FullSessionDeliveryObserver.__new__(FullSessionDeliveryObserver)
    observer.gap_cleared_chain = _CompactChain()
    observer.gap_cleared_package_digests = set()
    observer.drift_decision_digests = []
    packages = (_pending(9, "3"), _pending(10, "4"))
    plan = SimpleNamespace(
        result=SimpleNamespace(kind="GAP", consumed_observation_digest=None),
        _base_state=SimpleNamespace(pending=packages),
        digest="5" * 64,
    )
    observer._observe_drift_commit(
        object(), plan, before_pending=2, after_pending=0,
    )
    assert observer.gap_cleared_chain.result()["count"] == 2
    assert observer.gap_cleared_package_digests == {
        packages[0].digest, packages[1].digest,
    }
    assert observer.gap_cleared_chain.result()["first"] == {
        "package_digest": packages[0].digest,
        "admission_digest": packages[0].admission_digest,
        "root_plan_digest": packages[0].root_plan_digest,
        "availability_time_ns": 9,
        "trusted_nodes": ["N0"],
        "anchors_used": [0, 1],
        "gap_plan_digest": "5" * 64,
    }


def _actual_admission(
    branch: str, marker: str, *, accepted: bool = True,
) -> ContinuousAdmissionAudit:
    committed = branch == "B_UWB" and accepted
    return ContinuousAdmissionAudit(
        bucket=123,
        packet_digest=marker * 64,
        epoch_digest="b" * 64,
        candidate_digest="c" * 64,
        source_sequence=456,
        source_identity=("node", 7, 8),
        trusted_partition=("N0", "N1"),
        branch=branch,
        prepared_accepted=accepted,
        prepared_reason="ACCEPTED" if accepted else "ROBUST_REJECTED",
        commit_intent=branch == "B_UWB",
        commit_attempted=committed,
        commit_succeeded=committed,
        outcome=(
            "UWB_COMMIT_SUCCEEDED" if committed
            else ("PREPARED_REJECTED_NO_COMMIT" if not accepted
                  else "BASELINE_NO_UWB_COMMIT")
        ),
        pre_pose_digest="d" * 64,
        result_pose_digest="e" * 64,
        diagnostic_digest=None,
        diagnostic=None,
    )


def _transaction_observer(coordinator: object) -> FullSessionDeliveryObserver:
    observer = FullSessionDeliveryObserver.__new__(FullSessionDeliveryObserver)
    observer.owners = SimpleNamespace(coordinator=coordinator)
    observer.ab_transaction_chain = _CompactChain()
    observer.outer_rollback_count = 0
    observer.last_ab_total = 0
    observer.a_uwb = delivery.Counter()
    observer.b_uwb = delivery.Counter()
    observer.trusted_partition_histogram = delivery.Counter()
    return observer


def test_actual_staged_transaction_journal_has_deterministic_audit_chain(
) -> None:
    a = _actual_admission("A_BASELINE", "a")
    b = _actual_admission("B_UWB", "f")
    pair = PairedABTransactionAudit(
        "9" * 64,
        BranchTransactionDisposition("PREPARED_ADMISSION", a),
        BranchTransactionDisposition("PREPARED_ADMISSION", b),
    )
    coordinator = FullSessionTenNodeABCoordinator.__new__(
        FullSessionTenNodeABCoordinator,
    )
    coordinator._ab_transaction_journal = ()
    coordinator._ab_transaction_total = 0
    coordinator._record_batch_ab_stage = None
    coordinator._publish_ab_transactions((pair,))
    first = _transaction_observer(coordinator)
    second = _transaction_observer(coordinator)
    first._capture_transactions()
    second._capture_transactions()
    assert first.ab_transaction_chain.result() == (
        second.ab_transaction_chain.result()
    )
    assert first.ab_transaction_chain.count == coordinator._ab_transaction_total == 1
    assert sum(first.a_uwb.values()) == sum(first.b_uwb.values()) == 1
    assert first.b_uwb["accepted"] == sum(
        first.trusted_partition_histogram.values()
    ) == 1
    row = first.ab_transaction_chain.result()["first"]
    assert row["a_admission_digest"] == _admission_audit_digest(a)
    assert row["b_admission_digest"] == _admission_audit_digest(b)


def test_real_staged_journal_conserves_accepted_rejected_and_no_admission(
) -> None:
    accepted = PairedABTransactionAudit(
        "1" * 64,
        BranchTransactionDisposition(
            "PREPARED_ADMISSION", _actual_admission("A_BASELINE", "a"),
        ),
        BranchTransactionDisposition(
            "PREPARED_ADMISSION", _actual_admission("B_UWB", "f"),
        ),
    )
    rejected = PairedABTransactionAudit(
        "2" * 64,
        BranchTransactionDisposition(
            "PREPARED_ADMISSION",
            _actual_admission("A_BASELINE", "6", accepted=False),
        ),
        BranchTransactionDisposition(
            "PREPARED_ADMISSION",
            _actual_admission("B_UWB", "7", accepted=False),
        ),
    )
    absent = PairedABTransactionAudit(
        "3" * 64,
        BranchTransactionDisposition("NO_GROUP_ADMISSION", None),
        BranchTransactionDisposition("NO_GROUP_ADMISSION", None),
    )
    coordinator = FullSessionTenNodeABCoordinator.__new__(
        FullSessionTenNodeABCoordinator,
    )
    coordinator._ab_transaction_journal = ()
    coordinator._ab_transaction_total = 0
    coordinator._record_batch_ab_stage = None
    coordinator._publish_ab_transactions((accepted, rejected, absent))
    first = _transaction_observer(coordinator)
    second = _transaction_observer(coordinator)
    first._capture_transactions()
    second._capture_transactions()
    assert first.ab_transaction_chain.result() == (
        second.ab_transaction_chain.result()
    )
    assert first.ab_transaction_chain.count == coordinator._ab_transaction_total == 3
    assert first.a_uwb == {
        "accepted": 1, "rejected": 1, "no_admission": 1,
    }
    assert first.b_uwb == {
        "accepted": 1, "rejected": 1, "no_admission": 1,
    }
    assert sum(first.a_uwb.values()) == sum(first.b_uwb.values()) == 3
    assert sum(first.trusted_partition_histogram.values()) == 1


def test_admission_audit_digest_fails_closed_on_schema_drift(monkeypatch) -> None:
    audit = _actual_admission("B_UWB", "f")
    original = delivery.dataclass_fields
    monkeypatch.setattr(
        delivery, "dataclass_fields",
        lambda kind: (*original(kind), SimpleNamespace(name="future_field")),
    )
    with pytest.raises(RuntimeError, match="schema changed"):
        _admission_audit_digest(audit)


@pytest.mark.parametrize(
    ("section", "key", "value"),
    (
        ("boundary", "container_records", EXPECTED_CONTAINER_RECORDS - 1),
        ("boundary", "delivered_records", EXPECTED_DELIVERED_RECORDS - 1),
        ("boundary", "events", EXPECTED_EVENTS - 1),
        ("boundary", "regions", EXPECTED_REGIONS - 1),
        ("provenance", "optimized_pose_source_sha256", "0" * 64),
        ("report_only", "per_region_metrics_are_independent_verdicts", True),
        ("report_only", "step_is_acceptance_gate", True),
        ("gates", "all", False),
    ),
)
def test_full_result_validator_fails_closed_on_eof_provenance_or_gate(
    section: str, key: str, value: object,
) -> None:
    result = _passing_result()
    result[section][key] = value
    with pytest.raises(ValueError):
        validate_result(result)


def test_full_result_validator_accepts_only_one_complete_monolithic_verdict() -> None:
    validate_result(_passing_result())


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("schema", "wrong"),
        ("product_ready", True),
        ("scientific_pass", True),
        ("failure", {"type": "RuntimeError"}),
        ("stop_reason", "TIMEOUT"),
    ),
)
def test_validator_rejects_promotion_or_non_eof_result(
    key: str, value: object,
) -> None:
    result = _passing_result()
    result[key] = value
    with pytest.raises(ValueError):
        validate_result(result)


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("internal_seconds", INTERNAL_SECONDS - 1),
        ("outer_seconds", OUTER_SECONDS - 1),
        ("rlimit_as_bytes", (1 << 30) - 1),
        ("threads", 2),
        ("maximum_output_bytes", MAX_OUTPUT_BYTES + 1),
        ("retry", True),
        ("resume_capable", True),
        ("progress_checkpoints_are_metrics_only", False),
    ),
)
def test_validator_rejects_any_limit_or_execution_semantics_change(
    key: str, value: object,
) -> None:
    result = _passing_result()
    result["limits"][key] = value
    with pytest.raises(ValueError):
        validate_result(result)


def test_validator_rejects_session_or_per_region_promotion() -> None:
    result = _passing_result()
    result["provenance"]["session_id"] = "00_initial_still"
    with pytest.raises(ValueError):
        validate_result(result)


@pytest.mark.parametrize(
    ("section", "key"),
    (
        (None, "schema"), (None, "product_ready"),
        (None, "scientific_pass"), (None, "stop_reason"),
        ("limits", "retry"), ("limits", "resume_capable"),
        ("limits", "progress_checkpoints_are_metrics_only"),
        ("report_only", "per_region_metrics_are_independent_verdicts"),
        ("report_only", "step_is_acceptance_gate"),
    ),
)
def test_validator_rejects_omitted_nonpromotion_or_execution_field(
    section: str | None, key: str,
) -> None:
    result = _passing_result()
    target = result if section is None else result[section]
    target.pop(key)
    with pytest.raises(ValueError):
        validate_result(result)
    result = _passing_result()
    result["per_region_report_only"]["0"]["verdict_scope"] = "PASS"
    with pytest.raises(ValueError):
        validate_result(result)


def test_wrapper_enforces_limits_hashes_residuals_no_retry_and_no_resume() -> None:
    path = ROOT / "tools/run_c2_full_session_continuous_delivery_bounded.sh"
    text = path.read_text()
    assert "timeout --foreground --signal=TERM --kill-after=5s 11100" in text
    assert "ulimit -v 1048576" in text
    assert "OPENBLAS_NUM_THREADS=1" in text
    assert "bytes <= 16777216" in text
    assert "START_HASHES.txt" in text and "END_HASHES.txt" in text
    assert "PROCESS_FINAL.txt" in text and "RESIDUAL_PROCESS_GROUP" in text
    assert "validate_result" in text
    assert "retry" not in text.lower()
    assert OUTER_SECONDS == 11_100 and MAX_OUTPUT_BYTES == 16 << 20


def test_progress_checkpoints_are_metrics_only_and_end_at_exact_eof() -> None:
    assert CHECKPOINT_EVENT_THRESHOLDS[-1] == EXPECTED_EVENTS
    assert tuple(sorted(set(CHECKPOINT_EVENT_THRESHOLDS))) == (
        CHECKPOINT_EVENT_THRESHOLDS
    )
    source = (
        ROOT / "tools/diagnose_c2_full_session_continuous_delivery.py"
    ).read_text()
    assert "C2_FULL_PROGRESS_METRICS_ONLY_NOT_RESUMABLE_V1" in source
    assert '"resume_capable": False' in source
