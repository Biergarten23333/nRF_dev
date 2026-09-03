#!/usr/bin/env python3
"""Run one training-only primary/fresh equivalence gate after RUN013.

This continuation entrypoint never opens held-out data.  It creates two
independent bounded-reader sessions, drives the same frozen owner pipeline in
chronological order, compares every causal prefix and frozen scientific array,
and stops at the immutable fresh gate.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import traceback
from typing import Any, Mapping

import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN_RELATIVE = Path("logs/c2_basis_progressive_20260829T102836Z")
SPRINT_RELATIVE = RUN_RELATIVE / "CONTINUATION_SPRINT"
AMENDMENT_RELATIVE = (
    RUN_RELATIVE
    / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
)
SEAL_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_021_SOURCE_CORRECTION_001.json"
AUTHORITY_RELATIVE = (
    RUN_RELATIVE / "C2_FRESH_CONTINUATION_SOURCE_DELTA_001_RUNTIME_BUGFIX_002.json"
)

# Direct script execution otherwise places only ``tools/`` and ``src/`` on
# sys.path.  The qualified episode driver is a repository-root namespace
# module, so bind the canonical root before importing it.
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _semantic_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return {
            str(name): _jsonable(getattr(value, name))
            for name in value.__dataclass_fields__
        }
    return value


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(_jsonable(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _binding(relative: Path) -> dict[str, str]:
    return {"path": str(relative), "sha256": _sha(WORKSPACE / relative)}


def _load_immutable(relative: Path) -> dict[str, Any]:
    path = (WORKSPACE / relative).resolve()
    path.relative_to(WORKSPACE)
    if not path.is_file() or path.stat().st_mode & 0o222:
        raise RuntimeError(f"fresh continuation authority is missing or mutable: {relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def _next_index() -> int:
    for index in range(1, 1000):
        if not (WORKSPACE / SPRINT_RELATIVE / f"C2_FRESH_CONTINUATION_ATTEMPT_{index:03d}.json").exists():
            return index
    raise RuntimeError("fresh continuation attempt namespace exhausted")


def _array_binding(value: np.ndarray) -> dict[str, Any]:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
    ).encode()
    return {
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "sha256": hashlib.sha256(header + array.tobytes()).hexdigest(),
    }


def _drive(
    *,
    settings: Mapping[str, Any],
    initial_state: Mapping[str, Any],
    role: str,
    label: str,
    attempt_index: int,
    progress: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    from biospur_fusion.v0.c2_progressive.pipeline_runtime import C2PipelineRuntime
    from biospur_fusion.v0.c2_progressive.range_reader import SealedPrefitRangeReader
    from tools.run_c2_progressive_real import _run_episode, _snapshot_evidence

    execution = settings["execution_contract"]
    runtime = C2PipelineRuntime(
        settings,
        initial_state,
        prefit_registry_seal_path=WORKSPACE / SEAL_RELATIVE,
        fresh_continuation_source_delta_path=WORKSPACE / AUTHORITY_RELATIVE,
        execution_role=role,
    )
    nodes = tuple(
        str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    )
    plan_relative = Path(str(execution["payload_byte_access_plan_relative_path"]))
    reader = SealedPrefitRangeReader(
        root=WORKSPACE,
        plan_path=WORKSPACE / plan_relative,
        expected_plan_sha256=str(execution["payload_byte_access_plan_sha256"]),
        nodes=nodes,
    )
    chronology = tuple(str(value) for value in execution["chronological_actions"])
    access_rows: list[dict[str, Any]] = []
    access_bindings: list[dict[str, str]] = []
    for action_index, action in enumerate(chronology):
        progress.update({
            "stage": f"{label}_BOUNDED_TRAINING_READ",
            "execution_role": role,
            "chronological_index": action_index,
            "action": action,
        })
        decoded = reader.read_action(action_index)
        if decoded.action != action:
            raise RuntimeError("fresh continuation reader chronology mismatch")
        read_relative = SPRINT_RELATIVE / (
            f"C2_FRESH_{label}_READ_{action_index:02d}_ATTEMPT_{attempt_index:03d}.json"
        )
        _write_new(WORKSPACE / read_relative, {
            "schema": "biospur-c2-fresh-continuation-per-action-read-v1",
            "attempt_index": attempt_index,
            "execution_role": role,
            "reader_session_id": reader.reader_session_id,
            "chronological_index": action_index,
            "action": action,
            "access_audit": dict(decoded.access_audit),
            "decode_audit": dict(decoded.decode_audit),
            "training_ranges_only": True,
            "heldout_opened": False,
        })
        access_bindings.append(_binding(read_relative))
        access_rows.append(dict(decoded.access_audit))
        runtime.ingest_orientation_episode(decoded)
        progress[f"{label.lower()}_actions_read"] = action_index + 1
    runtime.finish_orientation_and_begin_calibration()
    aggregate_relative = SPRINT_RELATIVE / (
        f"C2_FRESH_{label}_READ_AUDIT_ATTEMPT_{attempt_index:03d}.json"
    )
    _write_new(WORKSPACE / aggregate_relative, {
        "schema": "biospur-c2-fresh-continuation-exact-read-audit-v1",
        "attempt_index": attempt_index,
        "execution_role": role,
        "reader_session_id": reader.reader_session_id,
        "plan": {"path": str(plan_relative), "sha256": reader.plan_sha256},
        "action_access": access_rows,
        "per_action_evidence": access_bindings,
        "capture_wide_continuity": reader.state.audit(),
        "whole_file_stat_hash_or_traversal": False,
        "training_ranges_only": True,
        "heldout_opened": False,
    })

    prefix_bindings: list[dict[str, str]] = []
    retry_limit = int(execution["real_runner"]["ordinary_episode_retry_limit"])
    for action_index, action in enumerate(chronology):
        progress.update({
            "stage": f"{label}_CAUSAL_CALIBRATION",
            "chronological_index": action_index,
            "action": action,
        })
        snapshot, pivots = _run_episode(
            runtime,
            chronological_index=action_index,
            action=action,
            role_label=f"FRESH_{label}",
            run_attempt_index=attempt_index,
            retry_limit=retry_limit,
        )
        prefix_relative = SPRINT_RELATIVE / (
            f"C2_FRESH_{label}_PREFIX_{action_index:02d}_ATTEMPT_{attempt_index:03d}.json"
        )
        _write_new(WORKSPACE / prefix_relative, {
            **_snapshot_evidence(snapshot),
            "attempt_index": attempt_index,
            "execution_role": role,
            "ordinary_pivots": pivots,
        })
        prefix_bindings.append(_binding(prefix_relative))
        progress[f"{label.lower()}_prefixes_committed"] = action_index + 1
    runtime.freeze_fit()
    return runtime, {
        "execution_role": role,
        "reader_session_id": reader.reader_session_id,
        "exact_read_audit": _binding(aggregate_relative),
        "prefix_artifacts": prefix_bindings,
        "heldout_opened": False,
    }


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("fresh continuation runner requires canonical Fusion_Part")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    tmp = (WORKSPACE / SPRINT_RELATIVE / "tmp_fresh_gate").resolve()
    tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(tmp)
    attempt_index = _next_index()
    running_relative = SPRINT_RELATIVE / f"C2_FRESH_CONTINUATION_RUNNING_{attempt_index:03d}.json"
    attempt_relative = SPRINT_RELATIVE / f"C2_FRESH_CONTINUATION_ATTEMPT_{attempt_index:03d}.json"
    gate_relative = SPRINT_RELATIVE / f"C2_FRESH_CONTINUATION_GATE_{attempt_index:03d}.json"
    started = _now()
    progress: dict[str, Any] = {
        "stage": "PREPAYLOAD_AUTHORITY",
        "primary_actions_read": 0,
        "primary_prefixes_committed": 0,
        "fresh_actions_read": 0,
        "fresh_prefixes_committed": 0,
        "heldout_opened": False,
    }
    running_written = False
    try:
        amendment = _load_immutable(AMENDMENT_RELATIVE)
        seal = _load_immutable(SEAL_RELATIVE)
        authority = _load_immutable(AUTHORITY_RELATIVE)
        settings = amendment["effective_settings"]
        if (
            seal.get("amendment") != _binding(AMENDMENT_RELATIVE)
            or seal.get("settings_semantic_sha256") != _semantic_sha(settings)
            or authority.get("parent_prefit_registry_seal") != _binding(SEAL_RELATIVE)
            or authority.get("settings_semantic_sha256") != seal.get("settings_semantic_sha256")
            or authority.get("execution_authorized") is not True
            or authority.get("heldout_opened") is not False
        ):
            raise RuntimeError("fresh continuation amendment/seal/authority chain is inconsistent")
        execution = settings["execution_contract"]
        initial_relative = Path(str(execution["initial_stochastic_state_relative_path"]))
        initial_state = json.loads((WORKSPACE / initial_relative).read_text(encoding="utf-8"))
        if (
            _sha(WORKSPACE / initial_relative)
            != execution["initial_stochastic_state_file_sha256"]
            or _semantic_sha(initial_state)
            != execution["initial_stochastic_state_semantic_sha256"]
        ):
            raise RuntimeError("fresh continuation initial stochastic authority changed")
        effective_source_hashes = dict(seal["qualified_source_hashes"])
        effective_source_hashes.update(authority["source_hash_overrides"])
        _write_new(WORKSPACE / running_relative, {
            "schema": "biospur-c2-fresh-continuation-running-v1",
            "created_utc": started,
            "attempt_index": attempt_index,
            "prefit_registry_seal": _binding(SEAL_RELATIVE),
            "fresh_continuation_authority": _binding(AUTHORITY_RELATIVE),
            "settings_semantic_sha256": seal["settings_semantic_sha256"],
            "effective_qualified_source_hashes": effective_source_hashes,
            "execution_roles": ["PRIMARY_CAUSAL", "FRESH_RAW_RECOMPUTATION"],
            "training_ranges_only": True,
            "distinct_reader_sessions_required": True,
            "heldout_opened": False,
            "open_holdout_after_pass": False,
            "retrospective_arrays_consumed": False,
            "bytecode_disabled": bool(sys.dont_write_bytecode),
        })
        running_written = True
        primary, primary_evidence = _drive(
            settings=settings,
            initial_state=initial_state,
            role="PRIMARY_CAUSAL",
            label="PRIMARY",
            attempt_index=attempt_index,
            progress=progress,
        )
        fresh, fresh_evidence = _drive(
            settings=settings,
            initial_state=initial_state,
            role="FRESH_RAW_RECOMPUTATION",
            label="FRESH",
            attempt_index=attempt_index,
            progress=progress,
        )
        progress["stage"] = "COMPARE_FROZEN_CAUSAL_PREFIX_AND_SCIENTIFIC_STATE"
        verification = dict(primary.verify_final_raw_fresh_runtime(fresh))
        if primary_evidence["reader_session_id"] == fresh_evidence["reader_session_id"]:
            raise RuntimeError("fresh continuation reader sessions are not distinct")
        export = primary.export_frozen_scientific_state()
        arrays = {name: np.asarray(value) for name, value in export["arrays"].items()}
        npz_relative = SPRINT_RELATIVE / f"C2_FRESH_CONTINUATION_FROZEN_STATE_{attempt_index:03d}.npz"
        with (WORKSPACE / npz_relative).open("xb") as handle:
            np.savez_compressed(handle, **{name: arrays[name] for name in sorted(arrays)})
        (WORKSPACE / npz_relative).chmod(0o444)
        manifest_relative = SPRINT_RELATIVE / f"C2_FRESH_CONTINUATION_FROZEN_STATE_{attempt_index:03d}.json"
        _write_new(WORKSPACE / manifest_relative, {
            "schema": "biospur-c2-fresh-continuation-frozen-scientific-state-v1",
            "created_utc": _now(),
            "attempt_index": attempt_index,
            "prefit_registry_seal": _binding(SEAL_RELATIVE),
            "fresh_continuation_authority": _binding(AUTHORITY_RELATIVE),
            "settings_semantic_sha256": seal["settings_semantic_sha256"],
            "qualified_source_hashes": effective_source_hashes,
            "fresh_verification": verification,
            "npz": {"path": str(npz_relative), "sha256": _sha(WORKSPACE / npz_relative)},
            "array_bindings": {
                name: _array_binding(value) for name, value in sorted(arrays.items())
            },
            "structure": export["structure"],
            "structure_semantic_sha256": _semantic_sha(export["structure"]),
            "heldout_opened": False,
            "scientific_acceptance_pass": False,
        })
        _write_new(WORKSPACE / gate_relative, {
            "schema": "biospur-c2-training-only-independent-fresh-gate-v1",
            "created_utc": _now(),
            "attempt_index": attempt_index,
            "running_manifest": _binding(running_relative),
            "fresh_continuation_authority": _binding(AUTHORITY_RELATIVE),
            "primary": primary_evidence,
            "fresh": fresh_evidence,
            "verification": verification,
            "frozen_state": _binding(manifest_relative),
            "fresh_raw_gate_pass": bool(verification["pass"]),
            "heldout_opened": False,
            "heldout_unlock_authorized_by_this_gate": False,
            "scientific_acceptance_pass": False,
        })
        _write_new(WORKSPACE / attempt_relative, {
            "schema": "biospur-c2-fresh-continuation-attempt-v1",
            "started_utc": started,
            "completed_utc": _now(),
            "attempt_index": attempt_index,
            "status": "TRAINING_ONLY_FRESH_EQUIVALENCE_PASS_HELDOUT_CLOSED_NOT_FINAL_PASS",
            "running_manifest": _binding(running_relative),
            "fresh_gate": _binding(gate_relative),
            "frozen_state": _binding(manifest_relative),
            "primary_reader_session_id": primary_evidence["reader_session_id"],
            "fresh_reader_session_id": fresh_evidence["reader_session_id"],
            "distinct_reader_sessions": True,
            "primary_prefix_count": len(primary_evidence["prefix_artifacts"]),
            "fresh_prefix_count": len(fresh_evidence["prefix_artifacts"]),
            "fresh_raw_gate_pass": True,
            "heldout_opened": False,
            "scientific_acceptance_pass": False,
            "exit_status": 0,
        })
        print(json.dumps({
            "attempt": _binding(attempt_relative),
            "fresh_gate": _binding(gate_relative),
            "fresh_raw_gate_pass": True,
            "heldout_opened": False,
            "scientific_acceptance_pass": False,
        }, indent=2, sort_keys=True))
        return 0
    except BaseException as exc:
        failure = {
            "schema": "biospur-c2-fresh-continuation-attempt-v1",
            "started_utc": started,
            "failed_utc": _now(),
            "attempt_index": attempt_index,
            "status": "FAIL_PRESERVED_HELDOUT_CLOSED",
            "running_manifest": _binding(running_relative) if running_written else None,
            "progress": progress,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback.format_exc(),
            "fresh_raw_gate_pass": False,
            "heldout_opened": False,
            "scientific_acceptance_pass": False,
            "exit_status": 2,
        }
        _write_new(WORKSPACE / attempt_relative, failure)
        print(json.dumps({
            "attempt": _binding(attempt_relative),
            "status": failure["status"],
            "failure_stage": progress["stage"],
            "exception": f"{type(exc).__name__}:{exc}",
            "heldout_opened": False,
        }, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
