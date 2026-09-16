"""Tool-only gates for the Action04 loader memory profiler."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import profile_c2_action04_loader_memory as profile


def _first_bound_hxx(audit: profile.AccessAudit) -> str:
    return next(iter(audit.provenance_bindings))


def test_bound_hxx_open_is_not_a_path_name_exemption() -> None:
    audit = profile.AccessAudit()
    path = _first_bound_hxx(audit)
    with pytest.raises(RuntimeError, match="FORBIDDEN_HXX_COMPUTE_INPUT"):
        audit.hook("open", (path, "r", os.O_RDONLY))
    assert audit.forbidden[-1]["classification"] == "COMPUTE_INPUT"
    assert audit.provenance_hash_only == []


def test_bound_hxx_write_is_always_rejected() -> None:
    audit = profile.AccessAudit()
    path = _first_bound_hxx(audit)
    stack = [
        {
            "file": str((profile.ROOT / "src/biospur_fusion/c2_3a_kinematics/provenance.py").resolve()),
            "function": function,
        }
        for function in ("sha256_file", "_require_digest", "verify_frozen_c2")
    ]
    assert audit._is_digest_verifier_stack(stack)
    assert not audit._read_only_open((path, "w", os.O_WRONLY | os.O_TRUNC))


def test_hxx_provenance_bindings_are_exactly_seal_owned() -> None:
    audit = profile.AccessAudit()
    assert len(audit.provenance_bindings) == 17
    assert all(
        row["owning_seal_sha256"] == profile.FORMAL_SEAL_SHA256
        and row["manifest_sha256"] == profile.FORMAL_MANIFEST_SHA256
        and len(row["expected_sha256"]) == 64
        for row in audit.provenance_bindings.values()
    )


def test_invocation_guards_are_observed_and_restored() -> None:
    guard = profile.InvocationGuards()
    original = guard.engine_class.admit
    with guard:
        with pytest.raises(RuntimeError, match="CROSSED_ENGINE_ADMIT_BOUNDARY"):
            guard.engine_class.admit(None)
        assert guard.counts["engine_admit"] == 1
    assert guard.engine_class.admit is original
    assert guard.overhead_ns < 1_000_000


def test_inventory_observer_reads_actual_objects_without_retaining_them() -> None:
    rows = [object() for _ in range(1007)]
    groups = [tuple(object() for _ in range(10)) for _ in range(41)]
    packets = [object() for _ in range(41)]
    observer = profile.InventoryObserver()
    value = observer.capture_objects(
        metric_rows=rows[:1000], context_rows=rows[1000:1006],
        temporal_closure_row=rows[1006], imu_rows=rows,
        groups=groups, packets=packets,
    )
    observer.validate()
    assert value == profile.EXPECTED_INVENTORY
    assert set(observer.__dict__) == {"value", "overhead_ns"}
    assert observer.overhead_ns < 1_000_000
