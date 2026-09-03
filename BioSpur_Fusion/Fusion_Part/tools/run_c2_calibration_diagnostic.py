#!/usr/bin/env python3
"""Run the user-authorized one-pass C2 training-range diagnostic.

This sprint entrypoint reads only the sealed prefit half-open byte ranges, uses
one ``REAL_DIAGNOSTIC`` runtime, never opens held-out data, never claims fresh
recomputation, and renders only owner-produced post-QMT trajectories.
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
AMENDMENT_RELATIVE = (
    RUN_RELATIVE
    / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
)
SEAL_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_021_SOURCE_CORRECTION_001.json"
)
ACTIVATION_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ACTIVATION_009.json"
SOURCE_DELTA_RELATIVE = (
    RUN_RELATIVE
    / "C2_REAL_DIAGNOSTIC_ACTIVATION_009_AUTHORIZED_SOURCE_DELTA_001_RUNTIME_BUGFIX_002.json"
)


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
        raise RuntimeError(f"diagnostic authority is missing or mutable: {relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_authority() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    amendment = _load_immutable(AMENDMENT_RELATIVE)
    seal = _load_immutable(SEAL_RELATIVE)
    activation = _load_immutable(ACTIVATION_RELATIVE)
    settings = amendment["effective_settings"]
    if (
        amendment.get("schema")
        != "biospur-c2-active-parameter-registry-prefit-amendment-v2"
        or seal.get("schema") != "biospur-c2-p2-prefit-registry-seal-v2"
        or seal.get("amendment") != _binding(AMENDMENT_RELATIVE)
        or seal.get("settings_semantic_sha256") != _semantic_sha(settings)
        or seal.get("qualified_source_hashes") != amendment.get("qualified_source_hashes")
        or activation.get("schema")
        != "biospur-c2-real-training-range-diagnostic-activation-v1"
        or activation.get("activation_role")
        != "REAL_TRAINING_RANGE_DIAGNOSTIC_ONLY"
        or activation.get("prefit_registry_seal") != _binding(SEAL_RELATIVE)
        or activation.get("settings_semantic_sha256")
        != seal.get("settings_semantic_sha256")
        or activation.get("qualified_source_hashes")
        != seal.get("qualified_source_hashes")
        or activation.get("execution_authorized") is not True
        or activation.get("training_ranges_only") is not True
        or activation.get("heldout_opened") is not False
        or activation.get("full_qualification_complete") is not False
    ):
        raise RuntimeError("diagnostic amendment/seal/activation chain is inconsistent")
    from biospur_fusion.v0.c2_progressive.pipeline_runtime import (
        _validated_prefit_seal_authority,
        _validated_real_diagnostic_activation,
    )
    seal_authority = _validated_prefit_seal_authority(
        settings,
        WORKSPACE / SEAL_RELATIVE,
        real_diagnostic_source_delta_path=WORKSPACE / SOURCE_DELTA_RELATIVE,
    )
    activation_authority = _validated_real_diagnostic_activation(
        settings,
        seal_authority,
        WORKSPACE / ACTIVATION_RELATIVE,
    )
    source_rows = []
    for relative, expected in sorted(
        seal_authority["qualified_source_hashes"].items()
    ):
        path = (WORKSPACE / relative).resolve()
        path.relative_to(WORKSPACE)
        observed = _sha(path) if path.is_file() else None
        source_rows.append({
            "path": relative,
            "expected_sha256": expected,
            "observed_sha256": observed,
            "pass": bool(path.is_file() and observed == expected),
        })
    if not source_rows or not all(row["pass"] for row in source_rows):
        raise RuntimeError("diagnostic prepayload exact source closure changed")
    return (
        amendment,
        {
            **seal,
            "runtime_effective_qualified_source_hashes": dict(
                seal_authority["qualified_source_hashes"]
            ),
            "authorized_source_delta": activation_authority[
                "authorized_source_delta"
            ],
        },
        {
            **activation,
            "source_revalidation": source_rows,
            "authorized_source_delta": activation_authority[
                "authorized_source_delta"
            ],
        },
    )


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


def _next_index() -> int:
    run_dir = WORKSPACE / RUN_RELATIVE
    for index in range(1, 1000):
        if not (run_dir / f"C2_REAL_DIAGNOSTIC_ATTEMPT_{index:03d}.json").exists():
            return index
    raise RuntimeError("diagnostic attempt namespace exhausted")


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("diagnostic runner requires canonical Fusion_Part")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    run_tmp = (WORKSPACE / RUN_RELATIVE / "CONTINUATION_SPRINT" / "tmp_real").resolve()
    run_tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(run_tmp)
    index = _next_index()
    running_relative = RUN_RELATIVE / f"C2_REAL_DIAGNOSTIC_RUNNING_{index:03d}.json"
    attempt_relative = RUN_RELATIVE / f"C2_REAL_DIAGNOSTIC_ATTEMPT_{index:03d}.json"
    started = _now()
    progress: dict[str, Any] = {
        "stage": "AUTHORITY_BEFORE_PAYLOAD",
        "actions_read": 0,
        "actions_committed": 0,
        "per_action_read_evidence": [],
        "heldout_opened": False,
    }
    running_written = False
    try:
        amendment, seal, activation = _validate_authority()
        settings = amendment["effective_settings"]
        execution = settings["execution_contract"]
        initial_relative = Path(execution["initial_stochastic_state_relative_path"])
        initial_path = WORKSPACE / initial_relative
        initial_state = json.loads(initial_path.read_text(encoding="utf-8"))
        if (
            _sha(initial_path) != execution["initial_stochastic_state_file_sha256"]
            or _semantic_sha(initial_state)
            != execution["initial_stochastic_state_semantic_sha256"]
        ):
            raise RuntimeError("diagnostic initial stochastic authority changed")
        _write_new(WORKSPACE / running_relative, {
            "schema": "biospur-c2-real-training-range-diagnostic-running-v1",
            "started_utc": started,
            "attempt_index": index,
            "prefit_registry_seal": _binding(SEAL_RELATIVE),
            "diagnostic_activation": _binding(ACTIVATION_RELATIVE),
            "authorized_source_delta": _binding(SOURCE_DELTA_RELATIVE),
            "settings_semantic_sha256": seal["settings_semantic_sha256"],
            "qualified_source_hashes": seal[
                "runtime_effective_qualified_source_hashes"
            ],
            "qualified_source_runtime_revalidation": activation["source_revalidation"],
            "execution_role": "REAL_DIAGNOSTIC",
            "training_ranges_only": True,
            "fresh_verification_claimed": False,
            "heldout_opened": False,
            "bytecode_disabled": bool(sys.dont_write_bytecode),
            "tmpdir": str(run_tmp.relative_to(WORKSPACE)),
        })
        running_written = True

        from biospur_fusion.v0.c2_progressive.pipeline_runtime import C2PipelineRuntime
        from biospur_fusion.v0.c2_progressive.range_reader import SealedPrefitRangeReader
        from tools.run_c2_progressive_real import (
            _run_episode,
            _snapshot_evidence,
            _write_frozen_npz,
        )

        nodes = tuple(
            str(row["hardware_id"])
            for row in settings["segment_frames"]["wear_authority"]["rows"]
        )
        runtime = C2PipelineRuntime(
            settings,
            initial_state,
            prefit_registry_seal_path=WORKSPACE / SEAL_RELATIVE,
            real_fit_activation_path=WORKSPACE / ACTIVATION_RELATIVE,
            real_diagnostic_source_delta_path=WORKSPACE / SOURCE_DELTA_RELATIVE,
            execution_role="REAL_DIAGNOSTIC",
        )
        plan_relative = Path(execution["payload_byte_access_plan_relative_path"])
        reader = SealedPrefitRangeReader(
            root=WORKSPACE,
            plan_path=WORKSPACE / plan_relative,
            expected_plan_sha256=execution["payload_byte_access_plan_sha256"],
            nodes=nodes,
        )
        chronology = tuple(execution["chronological_actions"])
        access_rows = []
        for action_index, action in enumerate(chronology):
            progress.update({
                "stage": "BOUNDED_TRAINING_RANGE_READ",
                "chronological_index": action_index,
                "action": action,
            })
            decoded = reader.read_action(action_index)
            if decoded.action != action:
                raise RuntimeError("diagnostic reader chronology mismatch")
            read_relative = RUN_RELATIVE / (
                f"C2_REAL_DIAGNOSTIC_READ_{action_index:02d}_RUN_{index:03d}.json"
            )
            _write_new(WORKSPACE / read_relative, {
                "schema": "biospur-c2-real-diagnostic-per-action-read-v1",
                "attempt_index": index,
                "reader_session_id": reader.reader_session_id,
                "chronological_index": action_index,
                "action": action,
                "access_audit": dict(decoded.access_audit),
                "decode_audit": dict(decoded.decode_audit),
                "training_ranges_only": True,
                "heldout_opened": False,
            })
            progress["per_action_read_evidence"].append(_binding(read_relative))
            access_rows.append(dict(decoded.access_audit))
            runtime.ingest_orientation_episode(decoded)
            progress["actions_read"] = action_index + 1
        runtime.finish_orientation_and_begin_calibration()
        read_audit_relative = RUN_RELATIVE / f"C2_REAL_DIAGNOSTIC_READ_AUDIT_{index:03d}.json"
        _write_new(WORKSPACE / read_audit_relative, {
            "schema": "biospur-c2-real-diagnostic-exact-read-audit-v1",
            "reader_session_id": reader.reader_session_id,
            "plan": {"path": str(plan_relative), "sha256": reader.plan_sha256},
            "action_access": access_rows,
            "per_action_evidence": progress["per_action_read_evidence"],
            "capture_wide_continuity": reader.state.audit(),
            "whole_file_stat_hash_or_traversal": False,
            "heldout_opened": False,
        })

        prefix_bindings = []
        retry_limit = int(execution["real_runner"]["ordinary_episode_retry_limit"])
        for action_index, action in enumerate(chronology):
            progress.update({
                "stage": "CAUSAL_DIAGNOSTIC_CALIBRATION",
                "chronological_index": action_index,
                "action": action,
            })
            snapshot, pivots = _run_episode(
                runtime,
                chronological_index=action_index,
                action=action,
                role_label="DIAGNOSTIC",
                run_attempt_index=index,
                retry_limit=retry_limit,
            )
            prefix_relative = RUN_RELATIVE / (
                f"C2_REAL_DIAGNOSTIC_PREFIX_{action_index:02d}_RUN_{index:03d}.json"
            )
            _write_new(WORKSPACE / prefix_relative, {
                **_snapshot_evidence(snapshot),
                "execution_role": "REAL_DIAGNOSTIC",
                "attempt_index": index,
                "ordinary_pivots": pivots,
            })
            prefix_bindings.append(_binding(prefix_relative))
            progress["actions_committed"] = action_index + 1
        runtime.freeze_fit()

        export = runtime.export_frozen_scientific_state()
        arrays = {name: np.asarray(value) for name, value in export["arrays"].items()}
        npz_relative = RUN_RELATIVE / f"C2_REAL_DIAGNOSTIC_FROZEN_STATE_{index:03d}.npz"
        _write_frozen_npz(WORKSPACE / npz_relative, arrays)
        manifest_relative = RUN_RELATIVE / f"C2_REAL_DIAGNOSTIC_FROZEN_STATE_{index:03d}.json"
        manifest = {
            "schema": "biospur-c2-reloadable-frozen-scientific-state-manifest-v1",
            "created_utc": _now(),
            "run_attempt_index": index,
            "prefit_registry_seal": _binding(SEAL_RELATIVE),
            "diagnostic_activation": _binding(ACTIVATION_RELATIVE),
            "authorized_source_delta": _binding(SOURCE_DELTA_RELATIVE),
            "diagnostic_execution_role": "REAL_DIAGNOSTIC",
            "settings_semantic_sha256": seal["settings_semantic_sha256"],
            "qualified_source_hashes": seal[
                "runtime_effective_qualified_source_hashes"
            ],
            "npz": {"path": str(npz_relative), "sha256": _sha(WORKSPACE / npz_relative)},
            "array_bindings": {
                name: _array_binding(value) for name, value in sorted(arrays.items())
            },
            "structure": export["structure"],
            "structure_semantic_sha256": _semantic_sha(export["structure"]),
            "scientific_state_mutable": False,
            "threshold_or_parameter_override_allowed": False,
            "fit_refit_rebase_ik_or_anthropometric_geometry_allowed": False,
            "heldout_opened_when_exported": False,
            "fresh_verification": None,
            "fresh_verification_claimed": False,
            "full_qualification_complete": False,
            "scientific_acceptance_pass": False,
        }
        _write_new(WORKSPACE / manifest_relative, manifest)

        progress["stage"] = "RENDER_ACTUAL_OWNER_PRODUCED_DIAGNOSTIC"
        from biospur_fusion.v0.c2_progressive.scientific_renderer import (
            render_registered_scientific_triviews,
        )

        render_relative = RUN_RELATIVE / f"C2_REAL_DIAGNOSTIC_RENDER_{index:03d}"
        render_result = dict(render_registered_scientific_triviews(
            manifest_path=WORKSPACE / manifest_relative,
            output_directory=WORKSPACE / render_relative,
            settings=settings,
        ))
        for row in (
            list(render_result["artifacts"])
            + list(render_result["partial_sensor_axis_artifacts"])
            + list(render_result["unavailable_diagnostic_artifacts"])
        ):
            (WORKSPACE / row["path"]).chmod(0o444)
        render_audit_relative = render_relative / "SCIENTIFIC_RENDER_AUDIT.json"
        render_result.update({
            "created_utc": _now(),
            "diagnostic_activation": _binding(ACTIVATION_RELATIVE),
            "authorized_source_delta": _binding(SOURCE_DELTA_RELATIVE),
            "actual_pixels_personally_inspected": False,
            "full_qualification_complete": False,
            "scientific_acceptance_pass": False,
            "status": "REAL_C2_DIAGNOSTIC_RENDERED_NOT_FRESH_VERIFIED_NOT_PASS",
        })
        _write_new(WORKSPACE / render_audit_relative, render_result)
        runtime_audit_relative = RUN_RELATIVE / f"C2_REAL_DIAGNOSTIC_RUNTIME_AUDIT_{index:03d}.json"
        _write_new(WORKSPACE / runtime_audit_relative, runtime.audit())
        full_qmt_fk_artifact_count = len(render_result["artifacts"])
        attempt_status = (
            "DIAGNOSTIC_QMT_FK_TRAJECTORY_RENDERED_NOT_QUALIFIED_NOT_PASS"
            if full_qmt_fk_artifact_count
            else "DIAGNOSTIC_PARTIAL_SENSOR_AXIS_RENDERED_NO_QMT_FK_NOT_PASS"
        )
        _write_new(WORKSPACE / attempt_relative, {
            "schema": "biospur-c2-real-training-range-diagnostic-attempt-v1",
            "attempt_index": index,
            "started_utc": started,
            "completed_utc": _now(),
            "status": attempt_status,
            "full_qmt_fk_artifact_count": full_qmt_fk_artifact_count,
            "running_manifest": _binding(running_relative),
            "read_audit": _binding(read_audit_relative),
            "prefix_artifacts": prefix_bindings,
            "frozen_manifest": _binding(manifest_relative),
            "runtime_audit": _binding(runtime_audit_relative),
            "render_audit": _binding(render_audit_relative),
            "actual_image_count": (
                len(render_result["artifacts"])
                + len(render_result["partial_sensor_axis_artifacts"])
            ),
            "actual_partial_sensor_axis_image_count": len(
                render_result["partial_sensor_axis_artifacts"]
            ),
            "unavailable_checkpoint_count": len(
                render_result["unavailable_diagnostic_artifacts"]
            ),
            "heldout_opened": False,
            "fresh_verification_claimed": False,
            "full_qualification_complete": False,
            "scientific_acceptance_pass": False,
            "exit_status": 0,
        })
        print(json.dumps({
            "attempt": _binding(attempt_relative),
            "render_audit": _binding(render_audit_relative),
            "actual_image_count": (
                len(render_result["artifacts"])
                + len(render_result["partial_sensor_axis_artifacts"])
            ),
            "actual_partial_sensor_axis_image_count": len(
                render_result["partial_sensor_axis_artifacts"]
            ),
            "unavailable_checkpoint_count": len(
                render_result["unavailable_diagnostic_artifacts"]
            ),
            "heldout_opened": False,
            "scientific_acceptance_pass": False,
        }, indent=2, sort_keys=True))
        return 0
    except BaseException as exc:
        failure = {
            "schema": "biospur-c2-real-training-range-diagnostic-attempt-v1",
            "attempt_index": index,
            "started_utc": started,
            "failed_utc": _now(),
            "status": "DIAGNOSTIC_FAIL_PRESERVED",
            "running_manifest": _binding(running_relative) if running_written else None,
            "progress": progress,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback.format_exc(),
            "heldout_opened": False,
            "fresh_verification_claimed": False,
            "scientific_acceptance_pass": False,
            "exit_status": 1,
        }
        _write_new(WORKSPACE / attempt_relative, failure)
        print(json.dumps({
            "attempt": _binding(attempt_relative),
            "status": failure["status"],
            "stage": progress["stage"],
            "exception": f"{type(exc).__name__}:{exc}",
            "heldout_opened": False,
        }, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
