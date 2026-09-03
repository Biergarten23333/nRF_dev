#!/usr/bin/env python3
"""Qualified frozen-state C2 heldout scientific evaluator.

Only heldout rows enter metrics. Exact training ranges are reread solely to
reconstruct one continuous VQF state per node. Frozen geometry, frames, clock
calibration, branch posterior, heading priors, settings, and thresholds cannot
be updated. The scientific path is official QMT -> rooted tree -> direct FK.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
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
AMENDMENT_RELATIVE = RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_010.json"
SEAL_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_010.json"
ACTIVATION_RELATIVE = RUN_RELATIVE / "P2_REAL_TRAINING_FIT_ACTIVATION_001.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _semantic_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _array_binding(value: np.ndarray) -> Mapping[str, Any]:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
    ).encode("utf-8")
    return {
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "sha256": hashlib.sha256(header + array.tobytes()).hexdigest(),
    }


def _frozen_digest(arrays: Mapping[str, np.ndarray]) -> str:
    return _semantic_sha({
        name: dict(_array_binding(value)) for name, value in sorted(arrays.items())
    })


def _load_immutable(relative: Path) -> Mapping[str, Any]:
    path = (WORKSPACE / relative).resolve()
    path.relative_to(WORKSPACE)
    if not path.is_file() or path.stat().st_mode & 0o222:
        raise RuntimeError(f"heldout authority is missing or mutable: {relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def _binding(relative: Path) -> dict[str, str]:
    return {"path": str(relative), "sha256": _sha(WORKSPACE / relative)}


def _write_new_immutable(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _write_npz_immutable(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    if path.exists():
        raise FileExistsError(path)
    with path.open("xb") as handle:
        np.savez_compressed(handle, **{
            name: np.asarray(value) for name, value in sorted(arrays.items())
        })
    path.chmod(0o444)


def _relative_transition(value: str) -> Path:
    relative = Path(value)
    if (
        relative.is_absolute()
        or relative.parent != RUN_RELATIVE
        or not relative.name.startswith("P2_REAL_HOLDOUT_TRANSITION_")
        or relative.suffix != ".json"
    ):
        raise argparse.ArgumentTypeError(
            "transition must be one P2_REAL_HOLDOUT_TRANSITION_*.json in the sealed run directory"
        )
    return relative


def _validate_authorities(
    transition_relative: Path,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    amendment = dict(_load_immutable(AMENDMENT_RELATIVE))
    seal = dict(_load_immutable(SEAL_RELATIVE))
    activation = dict(_load_immutable(ACTIVATION_RELATIVE))
    settings = amendment["effective_settings"]
    transition = dict(_load_immutable(transition_relative))
    exact_seal_binding = _binding(SEAL_RELATIVE)
    exact_activation_binding = _binding(ACTIVATION_RELATIVE)
    if (
        amendment.get("schema") != "biospur-c2-active-parameter-registry-prefit-amendment-v2"
        or seal.get("schema") != "biospur-c2-p2-prefit-registry-seal-v2"
        or seal.get("amendment") != _binding(AMENDMENT_RELATIVE)
        or seal.get("settings_semantic_sha256") != _semantic_sha(settings)
        or seal.get("qualified_source_hashes") != amendment.get("qualified_source_hashes")
        or activation.get("schema") != "biospur-c2-real-training-fit-activation-v1"
        or activation.get("prefit_registry_seal") != exact_seal_binding
        or activation.get("settings_semantic_sha256") != seal.get("settings_semantic_sha256")
        or activation.get("qualified_source_hashes") != seal.get("qualified_source_hashes")
        or activation.get("execution_authorized") is not True
        or activation.get("heldout_opened") is not False
        or transition.get("schema") != "biospur-c2-post-fresh-holdout-transition-v1"
        or transition.get("prefit_registry_seal") != exact_seal_binding
        or transition.get("real_fit_activation") != exact_activation_binding
        or transition.get("settings_semantic_sha256") != seal.get("settings_semantic_sha256")
        or transition.get("qualified_source_hashes") != seal.get("qualified_source_hashes")
        or transition.get("open_holdout_called_after_fresh_gate_was_immutable") is not True
        or transition.get("post_holdout_refit_mutation_rejected") is not True
        or transition.get("geometry_frames_heading_progressive_arrays_unchanged") is not True
        or transition.get("settings_and_threshold_semantic_hash_unchanged") is not True
        or transition.get("heldout_owner_opened") is not True
        or transition.get("heldout_payload_bytes_decoded") is not False
    ):
        raise RuntimeError("heldout transition is not the exact post-fresh frozen-owner authority")
    fresh_relative = Path(str(transition["fresh_gate"]["path"]))
    manifest_relative = Path(str(
        transition["reloadable_frozen_scientific_state"]["path"]
    ))
    if (
        _binding(fresh_relative) != transition["fresh_gate"]
        or _binding(manifest_relative) != transition["reloadable_frozen_scientific_state"]
    ):
        raise RuntimeError("heldout transition artifact hashes changed")
    fresh = dict(_load_immutable(fresh_relative))
    manifest = dict(_load_immutable(manifest_relative))
    verification = fresh.get("verification")
    if (
        fresh.get("schema") != "biospur-c2-real-progressive-distinct-raw-fresh-gate-v1"
        or fresh.get("prefit_registry_seal") != exact_seal_binding
        or fresh.get("real_fit_activation") != exact_activation_binding
        or fresh.get("fresh_raw_gate_pass") is not True
        or fresh.get("heldout_opened") is not False
        or fresh.get("qualified_source_hashes") != seal["qualified_source_hashes"]
        or fresh.get("settings_semantic_sha256") != seal["settings_semantic_sha256"]
        or fresh.get("reloadable_frozen_scientific_state") != _binding(manifest_relative)
        or not isinstance(verification, Mapping)
        or verification.get("schema")
        != "biospur-c2-final-raw-independent-frozen-owner-comparison-v1"
        or verification.get("scope") != "FINAL_RAW_RANGE_FULL_FROZEN_PIPELINE"
        or verification.get("primary_execution_role") != "PRIMARY_CAUSAL"
        or verification.get("fresh_execution_role") != "FRESH_RAW_RECOMPUTATION"
        or verification.get("caller_attested_scientific_booleans_consumed") is not False
        or verification.get("pass") is not True
        or not isinstance(verification.get("comparisons"), Mapping)
        or not verification["comparisons"]
        or not all(value is True for value in verification["comparisons"].values())
        or not isinstance(verification.get("array_allclose"), Mapping)
        or not verification["array_allclose"]
        or not all(value is True for value in verification["array_allclose"].values())
        or not isinstance(verification.get("causal_prefix_comparison"), Mapping)
        or verification["causal_prefix_comparison"].get("pass") is not True
        or fresh.get("primary", {}).get("reader_session_id")
        != verification.get("primary_reader_session_id")
        or fresh.get("fresh", {}).get("reader_session_id")
        != verification.get("fresh_reader_session_id")
        or verification.get("primary_reader_session_id")
        == verification.get("fresh_reader_session_id")
        or manifest.get("schema")
        != "biospur-c2-reloadable-frozen-scientific-state-manifest-v1"
        or manifest.get("prefit_registry_seal") != exact_seal_binding
        or manifest.get("real_fit_activation") != exact_activation_binding
        or manifest.get("settings_semantic_sha256") != seal["settings_semantic_sha256"]
        or manifest.get("qualified_source_hashes") != seal["qualified_source_hashes"]
        or manifest.get("fresh_verification") != verification
        or manifest.get("scientific_state_mutable") is not False
        or manifest.get("threshold_or_parameter_override_allowed") is not False
        or manifest.get("fit_refit_rebase_ik_or_anthropometric_geometry_allowed") is not False
        or manifest.get("heldout_opened_when_exported") is not False
        or transition.get("frozen_state_digest_before")
        != transition.get("frozen_state_digest_after")
    ):
        raise RuntimeError("heldout fresh gate is not the matching immutable PASS")
    for relative, expected in seal["qualified_source_hashes"].items():
        path = (WORKSPACE / relative).resolve()
        path.relative_to(WORKSPACE)
        if not path.is_file() or _sha(path) != expected:
            raise RuntimeError(f"heldout evaluator qualified source changed: {relative}")
    return amendment, seal, manifest_relative


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout-transition", required=True, type=_relative_transition)
    args = parser.parse_args()
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("heldout evaluator must run only from canonical Fusion_Part")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    run_tmp = (WORKSPACE / RUN_RELATIVE / "tmp").resolve()
    run_tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(run_tmp)
    suffix = args.holdout_transition.stem.rsplit("_", 1)[-1]
    running_relative = RUN_RELATIVE / f"P2_HELDOUT_EVALUATION_RUNNING_{suffix}.json"
    attempt_relative = RUN_RELATIVE / f"P2_HELDOUT_EVALUATION_ATTEMPT_{suffix}.json"
    running_path = WORKSPACE / running_relative
    attempt_path = WORKSPACE / attempt_relative
    started = _utc_now()
    stage = "VALIDATE_POST_FRESH_AUTHORITIES_BEFORE_HELDOUT_OPEN"
    progress: dict[str, Any] = {
        "heldout_actions_read": 0,
        "heldout_actions_scientifically_processed": 0,
        "per_action_read_evidence": [],
        "per_action_scientific_evidence": [],
    }
    running_written = False
    try:
        amendment, seal, manifest_relative = _validate_authorities(
            args.holdout_transition
        )
        settings = amendment["effective_settings"]
        registered = settings.get("heldout_evaluation", {})
        if (
            registered.get("authoritative_entrypoint")
            != "tools/evaluate_c2_progressive_holdout.py"
            or registered.get(
                "fit_refit_branch_reweight_threshold_override_or_feedback_allowed"
            ) is not False
        ):
            raise RuntimeError("heldout metric/criterion settings are not registered")
        initial_relative = Path(str(
            settings["execution_contract"]["initial_stochastic_state_relative_path"]
        ))
        initial = dict(_load_immutable(initial_relative))
        if (
            _sha(WORKSPACE / initial_relative)
            != settings["execution_contract"]["initial_stochastic_state_file_sha256"]
            or _semantic_sha(initial)
            != settings["execution_contract"]["initial_stochastic_state_semantic_sha256"]
        ):
            raise RuntimeError("heldout initial stochastic authority changed")
        from biospur_fusion.v0.c2_progressive.scientific_renderer import (
            load_frozen_render_authority,
        )

        manifest, frozen_arrays = load_frozen_render_authority(
            WORKSPACE / manifest_relative, settings=settings,
        )
        for value in frozen_arrays.values():
            value.setflags(write=False)
        frozen_before = _frozen_digest(frozen_arrays)
        _write_new_immutable(running_path, {
            "schema": "biospur-c2-heldout-scientific-evaluation-running-v2",
            "started_utc": started,
            "status": "RUNNING_APPEND_ONLY_TERMINAL_RECORD_SEPARATE",
            "holdout_transition": _binding(args.holdout_transition),
            "frozen_scientific_state": _binding(manifest_relative),
            "prefit_registry_seal": _binding(SEAL_RELATIVE),
            "settings_semantic_sha256": seal["settings_semantic_sha256"],
            "qualified_source_hashes": seal["qualified_source_hashes"],
            "registered_scientific_metrics_and_criteria": registered,
            "fit_refit_branch_reweight_calibration_update_or_threshold_override_allowed": False,
            "heldout_payload_opened_when_running_manifest_written": False,
        })
        running_written = True
        stage = "DUAL_RANGE_ORIENTATION_THEN_HELDOUT_QMT_TREE_FK_METRICS"
        from biospur_fusion.v0.c2_progressive.heldout_evaluation import (
            FrozenScientificHeldoutEvaluationOwner,
        )
        from biospur_fusion.v0.c2_progressive.range_reader import (
            SealedFrozenEvaluationRangeReader,
        )

        execution = settings["execution_contract"]
        nodes = tuple(
            str(row["hardware_id"])
            for row in settings["segment_frames"]["wear_authority"]["rows"]
        )
        reader = SealedFrozenEvaluationRangeReader(
            root=WORKSPACE,
            plan_path=WORKSPACE / str(execution["payload_byte_access_plan_relative_path"]),
            expected_plan_sha256=str(execution["payload_byte_access_plan_sha256"]),
            nodes=nodes,
        )
        owner = FrozenScientificHeldoutEvaluationOwner(
            settings=settings,
            initial_stochastic_state=initial,
            frozen_manifest=manifest,
            frozen_arrays=frozen_arrays,
        )
        for index, action in enumerate(execution["chronological_actions"]):
            try:
                decoded = reader.read_action(index)
            except BaseException:
                if reader.last_read_attempt_audit is not None:
                    read_relative = RUN_RELATIVE / f"P2_HELDOUT_READ_{index:02d}_{suffix}.json"
                    _write_new_immutable(WORKSPACE / read_relative, {
                        "schema": "biospur-c2-heldout-dual-range-read-decode-evidence-v2",
                        "reader_session_id": reader.reader_session_id,
                        "chronological_index": index,
                        "action": action,
                        **dict(reader.last_read_attempt_audit),
                        "calibration_fit_or_parameter_update_allowed": False,
                    })
                    progress["per_action_read_evidence"].append(_binding(read_relative))
                raise
            input_health: dict[str, Any] = {}
            for node, rows in decoded.combined_action.rows_by_node.items():
                heldout_rows = rows[np.asarray(
                    decoded.heldout_source_indices_by_node[node], dtype=np.int64,
                )]
                acc = np.asarray(heldout_rows["acc_raw"], dtype=float)
                gyro = np.asarray(heldout_rows["gyro_raw"], dtype=float)
                input_health[node] = {
                    "heldout_decoded_rows": int(len(heldout_rows)),
                    "accelerometer_raw_norm_median": (
                        None if not len(heldout_rows) else float(np.median(np.linalg.norm(acc, axis=1)))
                    ),
                    "gyroscope_raw_rms": (
                        None if not len(heldout_rows) else float(np.sqrt(np.mean(np.square(gyro))))
                    ),
                    "nonfinite_decoded_values": int(
                        np.count_nonzero(~np.isfinite(acc))
                        + np.count_nonzero(~np.isfinite(gyro))
                    ),
                }
            read_relative = RUN_RELATIVE / f"P2_HELDOUT_READ_{index:02d}_{suffix}.json"
            _write_new_immutable(WORKSPACE / read_relative, {
                "schema": "biospur-c2-heldout-dual-range-read-decode-evidence-v2",
                "reader_session_id": reader.reader_session_id,
                "chronological_index": index,
                "action": action,
                "access_audit": dict(decoded.access_audit),
                "decode_audit": dict(decoded.decode_audit),
                "heldout_input_health_diagnostic": input_health,
                "input_health_diagnostic_is_scientific_metric_or_verdict": False,
                "calibration_fit_or_parameter_update_allowed": False,
            })
            progress["per_action_read_evidence"].append(_binding(read_relative))
            progress["heldout_actions_read"] = index + 1
            result = owner.evaluate_action(decoded)
            arrays_relative = RUN_RELATIVE / f"P2_HELDOUT_SCIENTIFIC_ARRAYS_{index:02d}_{suffix}.npz"
            _write_npz_immutable(WORKSPACE / arrays_relative, result.arrays)
            scientific_relative = RUN_RELATIVE / f"P2_HELDOUT_SCIENTIFIC_{index:02d}_{suffix}.json"
            _write_new_immutable(WORKSPACE / scientific_relative, {
                **dict(result.report),
                "read_decode_evidence": _binding(read_relative),
                "trajectory_arrays": _binding(arrays_relative),
                "trajectory_array_bindings": {
                    name: dict(_array_binding(value))
                    for name, value in sorted(result.arrays.items())
                },
                "frozen_scientific_state": _binding(manifest_relative),
            })
            progress["per_action_scientific_evidence"].append(
                _binding(scientific_relative)
            )
            progress["heldout_actions_scientifically_processed"] = index + 1
        verdict = dict(owner.finalize())
        frozen_after = _frozen_digest(frozen_arrays)
        if (
            frozen_after != frozen_before
            or verdict["aggregate_metrics"]["calibration_digest_unchanged"] is not True
        ):
            raise RuntimeError("heldout evaluation altered frozen calibration state")
        _write_new_immutable(attempt_path, {
            "schema": "biospur-c2-heldout-scientific-evaluation-attempt-v2",
            "started_utc": started,
            "completed_utc": _utc_now(),
            "status": (
                "SCIENTIFIC_HELDOUT_VERDICT_PASS" if verdict["pass"]
                else "SCIENTIFIC_HELDOUT_VERDICT_FAIL_PRESERVED_NO_REFIT"
            ),
            "running_manifest": _binding(running_relative),
            "reader_session_id": reader.reader_session_id,
            "per_action_read_evidence": progress["per_action_read_evidence"],
            "per_action_scientific_evidence": progress["per_action_scientific_evidence"],
            "capture_wide_dual_range_continuity": reader.state.audit(),
            "global_verdict": verdict,
            "frozen_state_digest_before": frozen_before,
            "frozen_state_digest_after": frozen_after,
            "frozen_state_unchanged": True,
            "fit_refit_branch_reweight_threshold_tuning_or_feedback_used": False,
            "input_health_diagnostic_is_scientific_metric": False,
            "scientific_acceptance_pass": bool(verdict["pass"]),
            "exit_status": 0,
        })
        print(json.dumps({
            "attempt": _binding(attempt_relative),
            "heldout_actions_read": len(execution["chronological_actions"]),
            "frozen_state_unchanged": True,
            "scientific_acceptance_pass": bool(verdict["pass"]),
            "global_status": verdict["status"],
        }, indent=2, sort_keys=True))
        return 0
    except BaseException as exc:
        failure = {
            "schema": "biospur-c2-heldout-scientific-evaluation-attempt-v2",
            "started_utc": started,
            "completed_utc": _utc_now(),
            "status": "FAIL_EXCEPTION_PRESERVED",
            "failure_stage": stage,
            "progress": progress,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback.format_exc(),
            "running_manifest": _binding(running_relative) if running_written else None,
            "fit_refit_branch_reweight_threshold_tuning_or_feedback_used": False,
            "scientific_acceptance_pass": False,
            "exit_status": 2,
        }
        failure_path = attempt_path
        if failure_path.exists():
            failure_path = WORKSPACE / RUN_RELATIVE / (
                f"P2_HELDOUT_EVALUATION_EXCEPTION_AFTER_ATTEMPT_{suffix}.json"
            )
        _write_new_immutable(failure_path, failure)
        print(json.dumps({
            "failure": {
                "path": str(failure_path.relative_to(WORKSPACE)),
                "sha256": _sha(failure_path),
            },
            "failure_stage": stage,
            "exception_type": type(exc).__name__,
            "scientific_acceptance_pass": False,
        }, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
