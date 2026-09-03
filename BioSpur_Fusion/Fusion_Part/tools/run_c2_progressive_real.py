#!/usr/bin/env python3
"""Authoritative C2 progressive training plus independent fresh-raw rerun.

This entrypoint has no scientific command-line parameters.  It consumes the
exact amendment/seal/activation authorities, reads only the sealed half-open
prefit ranges, and drives ``C2PipelineRuntime`` twice: first as
``PRIMARY_CAUSAL`` and then from a new ``SealedPrefitRangeReader`` session as
``FRESH_RAW_RECOMPUTATION``.  It never imports the legacy ``c2_basis`` path and
does not open or decode held-out bytes.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import traceback
from typing import Any, Mapping, Sequence

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
    if hasattr(value, "__dict__") and value.__class__.__module__.startswith(
        "biospur_fusion.v0.c2_progressive"
    ):
        return {str(key): _jsonable(item) for key, item in vars(value).items()}
    return value


def _write_new_immutable(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(_jsonable(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _binding(relative: Path) -> dict[str, str]:
    return {"path": str(relative), "sha256": _sha(WORKSPACE / relative)}


def _next_attempt_index() -> int:
    run_dir = WORKSPACE / RUN_RELATIVE
    stems = (
        "P2_REAL_PROGRESSIVE_RUNNING",
        "P2_REAL_PROGRESSIVE_ATTEMPT",
        "P2_REAL_PROGRESSIVE_FRESH_GATE",
    )
    for index in range(1, 1000):
        if not any((run_dir / f"{stem}_{index:03d}.json").exists() for stem in stems):
            return index
    raise RuntimeError("real progressive attempt namespace exhausted")


def _load_immutable(relative: Path) -> Mapping[str, Any]:
    path = (WORKSPACE / relative).resolve()
    path.relative_to(WORKSPACE)
    if not path.is_file() or path.stat().st_mode & 0o222:
        raise RuntimeError(f"real runner authority is missing or mutable: {relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validated_static_authority() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any],
]:
    amendment = dict(_load_immutable(AMENDMENT_RELATIVE))
    seal = dict(_load_immutable(SEAL_RELATIVE))
    activation = dict(_load_immutable(ACTIVATION_RELATIVE))
    settings = amendment.get("effective_settings")
    if (
        amendment.get("schema") != "biospur-c2-active-parameter-registry-prefit-amendment-v2"
        or seal.get("schema") != "biospur-c2-p2-prefit-registry-seal-v2"
        or activation.get("schema") != "biospur-c2-real-training-fit-activation-v1"
        or seal.get("amendment") != _binding(AMENDMENT_RELATIVE)
        or seal.get("settings_semantic_sha256") != _semantic_sha(settings)
        or activation.get("prefit_registry_seal") != _binding(SEAL_RELATIVE)
        or activation.get("settings_semantic_sha256") != seal.get("settings_semantic_sha256")
        or activation.get("qualified_source_hashes") != seal.get("qualified_source_hashes")
        or activation.get("execution_authorized") is not True
        or activation.get("heldout_opened") is not False
    ):
        raise RuntimeError("real runner amendment/seal/activation authority is inconsistent")
    execution = settings["execution_contract"]
    runner = execution.get("real_runner", {})
    if (
        runner.get("authoritative_entrypoint") != "tools/run_c2_progressive_real.py"
        or runner.get("primary_execution_role") != "PRIMARY_CAUSAL"
        or runner.get("fresh_execution_role") != "FRESH_RAW_RECOMPUTATION"
        or runner.get("distinct_reader_sessions_required") is not True
        or runner.get("heldout_decode_in_this_entrypoint") is not False
        or runner.get("legacy_c2_basis_or_ik_rebase_anthropometric_fit_allowed") is not False
        or runner.get("scientific_cli_parameter_overrides_allowed") is not False
    ):
        raise RuntimeError("registered real-runner execution policy is incomplete")
    if execution.get("static_validator_execution_authorized_literal") is not False:
        raise RuntimeError("real runner must preserve the static validator's execution_authorized:false")
    policy = execution.get("qualified_source_runtime_revalidation", {})
    if (
        policy.get("before_any_payload_open") is not True
        or policy.get("existence_and_regular_file_required") is not True
        or policy.get("exact_sha256_recomputed") is not True
        or policy.get("source_mode_recorded") is not True
    ):
        raise RuntimeError("real runner source revalidation policy is incomplete")
    read_only_required = bool(policy.get("read_only_required"))
    source_rows: list[dict[str, Any]] = []
    for relative, expected_sha256 in sorted(seal["qualified_source_hashes"].items()):
        path = (WORKSPACE / str(relative)).resolve()
        inside_workspace = True
        try:
            path.relative_to(WORKSPACE)
        except ValueError:
            inside_workspace = False
        exists = bool(inside_workspace and path.exists())
        is_regular_file = bool(exists and path.is_file() and stat.S_ISREG(path.stat().st_mode))
        mode = int(path.stat().st_mode & 0o777) if exists else None
        writable_mode_bits = None if mode is None else int(mode & 0o222)
        observed_sha256 = _sha(path) if is_regular_file else None
        row_pass = bool(
            inside_workspace
            and is_regular_file
            and observed_sha256 == expected_sha256
            and (not read_only_required or writable_mode_bits == 0)
        )
        source_rows.append({
            "path": str(relative),
            "inside_canonical_workspace": inside_workspace,
            "exists": exists,
            "is_regular_file": is_regular_file,
            "mode_octal": None if mode is None else f"{mode:04o}",
            "writable_mode_bits_octal": (
                None if writable_mode_bits is None else f"{writable_mode_bits:04o}"
            ),
            "read_only_required_by_registered_policy": read_only_required,
            "expected_sha256": expected_sha256,
            "observed_sha256": observed_sha256,
            "pass": row_pass,
        })
    source_audit = {
        "schema": "biospur-c2-real-prepayload-qualified-source-revalidation-v1",
        "performed_before_reader_construction_or_payload_open": True,
        "registered_policy": dict(policy),
        "expected_source_count": len(seal["qualified_source_hashes"]),
        "observed_source_count": len(source_rows),
        "sources": source_rows,
        "pass": bool(source_rows and all(row["pass"] for row in source_rows)),
    }
    return amendment, seal, activation, source_audit


def _array_binding(value: np.ndarray) -> dict[str, Any]:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
    ).encode("utf-8")
    return {
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "sha256": hashlib.sha256(header + array.tobytes()).hexdigest(),
    }


def _frozen_export_digest(export: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "array_bindings": {
            name: _array_binding(value)
            for name, value in sorted(export["arrays"].items())
        },
        "structure_semantic_sha256": _semantic_sha(export["structure"]),
        "settings_semantic_sha256": export["settings_semantic_sha256"],
    }


def _write_frozen_npz(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    with path.open("xb") as handle:
        np.savez_compressed(
            handle,
            **{name: np.asarray(value) for name, value in sorted(arrays.items())},
        )
    path.chmod(0o444)


def _snapshot_evidence(snapshot: Any) -> dict[str, Any]:
    return {
        "schema": "biospur-c2-real-causal-prefix-evidence-v1",
        "chronological_index": int(snapshot.chronological_index),
        "action": str(snapshot.action),
        "prequential_status": str(snapshot.prequential_status),
        "prequential_observed_rank": int(snapshot.prequential_observed_rank),
        "prediction_nll_before_ingest": float(snapshot.prediction_nll_before_ingest),
        "data_information_rank": int(snapshot.data_information_rank),
        "data_information_nonzero_eigenvalues": np.asarray(
            snapshot.data_information_nonzero_eigenvalues
        ).tolist(),
        "data_information_pseudologdet": snapshot.data_information_pseudologdet,
        "uncertainty_trace": float(snapshot.uncertainty_trace),
        "physical_validity": float(snapshot.physical_validity),
        "branch_concentration": float(snapshot.branch_concentration),
        "branch_ids": list(snapshot.branch_ids),
        "branch_weights": np.asarray(snapshot.branch_weights).tolist(),
        "posterior_mean": _array_binding(snapshot.posterior_mean),
        "total_posterior_covariance": _array_binding(snapshot.posterior_covariance),
        "measurement_statistical_covariance": _array_binding(
            snapshot.measurement_statistical_covariance
        ),
        "temporal_migration_covariance": _array_binding(
            snapshot.temporal_migration_covariance
        ),
        "shared_systematic_covariance": _array_binding(snapshot.shared_systematic_covariance),
        "status": "SCIENTIFIC_PREFIX_DIAGNOSTIC_NOT_FINAL_PASS",
        "heldout_opened": False,
    }


@dataclass
class _OperationFailure(RuntimeError):
    category: str
    edge: str | None
    branch_id: str | None
    original_type: str
    original_message: str
    original_diagnostic: Mapping[str, Any] | None = None

    def __str__(self) -> str:
        target = "/".join(value for value in (self.edge, self.branch_id) if value)
        return f"{self.category}:{target}:{self.original_type}:{self.original_message}"


class _ClassARealAuthorityError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(f"{self.code}: {self.detail}")


def _raise_operation_failure(
    category: str,
    exc: BaseException,
    *,
    edge: str | None = None,
    branch_id: str | None = None,
) -> None:
    raise _OperationFailure(
        category=category,
        edge=edge,
        branch_id=branch_id,
        original_type=type(exc).__name__,
        original_message=str(exc),
        original_diagnostic=(
            dict(exc.audit) if hasattr(exc, "audit") else None
        ),
    ) from exc


def _is_registered_local_timing_no_update(exc: BaseException) -> bool:
    """Recognize only the production timing owner's explicit ordinary outcome."""

    return bool(
        isinstance(exc, ValueError)
        and str(exc).startswith("timing observation local no-update:")
    )


def _record_registered_timing_heading_no_update(
    runtime: Any,
    *,
    branch_ids: Sequence[str],
    edge: str,
    hard_support_token: str,
) -> int:
    """Carry one explicit timing no-update through every retained branch."""

    for branch_id in branch_ids:
        runtime.record_current_heading_no_update(
            branch_id=branch_id,
            edge=edge,
            hard_support_token=hard_support_token,
            cause="REGISTERED_TIMING_OWNER_LOCAL_NO_UPDATE",
        )
    return len(branch_ids)


def _run_episode(
    runtime: Any,
    *,
    chronological_index: int,
    action: str,
    role_label: str,
    run_attempt_index: int,
    retry_limit: int,
) -> tuple[Any, list[dict[str, Any]]]:
    from biospur_fusion.v0.c2_progressive.architecture_guard import ClassAGuardViolation
    from biospur_fusion.v0.c2_progressive.functional_geometry import (
        EDGE_ACTIONS,
        EDGE_SPECS,
        HINGE_EDGES,
    )

    suppressed_pairs: set[str] = set()
    suppressed_centers: set[str] = set()
    suppressed_axes: set[str] = set()
    suppressed_qmt_edges: set[str] = set()
    retry_evidence: list[dict[str, Any]] = []
    for retry_index in range(retry_limit + 1):
        local_diagnostics: list[dict[str, Any]] = []
        local_timing_no_update_edges: set[str] = set()
        try:
            with runtime.calibration_episode_transaction(
                chronological_index,
                action,
            ):
                runtime.score_current_prequential()
                pair_by_edge: dict[str, Any] = {}

                def owned_pair(edge: str) -> Any:
                    if edge in suppressed_pairs:
                        return None
                    if edge in pair_by_edge:
                        return pair_by_edge[edge]
                    try:
                        pair = runtime.align_current_pair(edge=edge)
                    except ClassAGuardViolation:
                        raise
                    except (ValueError, RuntimeError) as exc:
                        if _is_registered_local_timing_no_update(exc):
                            local_timing_no_update_edges.add(edge)
                            local_diagnostics.append({
                                "kind": "PAIR_ALIGNMENT",
                                "edge": edge,
                                "status": "EXPLICIT_LOCAL_TIMING_NO_UPDATE",
                                "exception_type": type(exc).__name__,
                                "exception_message": str(exc),
                                "pair_clock_observation_appended": False,
                                "geometry_information_added": False,
                                "capture_continues": True,
                            })
                            return None
                        _raise_operation_failure("PAIR_ALIGNMENT", exc, edge=edge)
                    pair_by_edge[edge] = pair
                    return pair

                # Every downstream heading edge must reuse the exact pair that
                # was owned while the episode was still in the local-factor
                # stage.  Lazy first alignment after frame construction is a
                # stage-order bypass and can append a second clock observation.
                for edge, _, _ in EDGE_SPECS:
                    owned_pair(edge)

                for edge, _, _ in EDGE_SPECS:
                    if action not in EDGE_ACTIONS[edge]:
                        continue
                    pair = owned_pair(edge)
                    if pair is None:
                        continue
                    if edge not in suppressed_centers:
                        try:
                            estimate = runtime.estimate_current_local_center(edge, [pair])
                            local_diagnostics.append({
                                "kind": "CENTER", "edge": edge,
                                "status": "OWNER_ESTIMATE_RETURNED",
                                "owner_update_eligible": bool(
                                    estimate.report.get("owner_update_eligible", False)
                                ),
                            })
                        except ClassAGuardViolation:
                            raise
                        except (ValueError, RuntimeError) as exc:
                            _raise_operation_failure("CENTER_ESTIMATE", exc, edge=edge)
                    else:
                        no_update = runtime.record_current_center_no_update(
                            edge,
                            pair,
                            cause=(
                                "BOUNDED_ORDINARY_CENTER_ESTIMATOR_FAILURE_"
                                "ROLLED_BACK_THEN_CURRENT_PAIR_COMMITTED_WITH_ZERO_INFORMATION"
                            ),
                        )
                        local_diagnostics.append({
                            "kind": "CENTER",
                            "edge": edge,
                            "status": "EXPLICIT_LOCAL_NO_UPDATE_AFTER_BOUNDED_RETRY",
                            "owner_update_eligible": False,
                            "causal_prefix_no_update": dict(no_update),
                        })
                    if edge in HINGE_EDGES and edge not in suppressed_axes:
                        try:
                            estimate = runtime.estimate_current_local_axis(edge, [pair])
                            local_diagnostics.append({
                                "kind": "AXIS", "edge": edge,
                                "status": "OWNER_ESTIMATE_RETURNED",
                                "owner_update_eligible": bool(
                                    estimate.report.get("owner_update_eligible", False)
                                ),
                            })
                        except ClassAGuardViolation:
                            raise
                        except (ValueError, RuntimeError) as exc:
                            _raise_operation_failure("AXIS_ESTIMATE", exc, edge=edge)

                runtime.finish_current_geometry_update()
                branches = runtime.update_current_frame_branches()
                hard_support = runtime.current_heading_hard_support()
                edge_readiness = runtime.current_heading_edge_readiness()
                qmt_ready_edges = set(edge_readiness["ready_edges"])
                supported_branch_ids = list(hard_support["branch_ids"])
                hard_support_token = hard_support["owner_token"]
                if branches and not supported_branch_ids:
                    raise RuntimeError("no branch remains in the authoritative cumulative hard support")
                runtime.validate_heading_execution_branch_ids(
                    supported_branch_ids,
                    hard_support_token=hard_support_token,
                )
                for edge, _, _ in EDGE_SPECS:
                    if not branches:
                        break
                    if edge not in qmt_ready_edges:
                        for branch_id in supported_branch_ids:
                            runtime.record_current_heading_no_update(
                                branch_id=branch_id,
                                edge=edge,
                                hard_support_token=hard_support_token,
                                cause="EDGE_LOCAL_FUNCTIONAL_FRAMES_NOT_YET_MATURE",
                            )
                        continue
                    if (
                        edge in suppressed_qmt_edges
                        or edge in suppressed_pairs
                        or edge in local_timing_no_update_edges
                    ):
                        for branch_id in supported_branch_ids:
                            runtime.record_current_heading_no_update(
                                branch_id=branch_id,
                                edge=edge,
                                hard_support_token=hard_support_token,
                                cause=(
                                    "BOUNDED_ORDINARY_QMT_EDGE_FAILURE_LOCAL_NO_UPDATE"
                                    if edge in suppressed_qmt_edges
                                    else (
                                        "REGISTERED_TIMING_OWNER_LOCAL_NO_UPDATE"
                                        if edge in local_timing_no_update_edges
                                        else "BOUNDED_ORDINARY_PAIR_ALIGNMENT_FAILURE_LOCAL_NO_UPDATE"
                                    )
                                ),
                            )
                        continue
                    pair = owned_pair(edge)
                    if pair is None:
                        _record_registered_timing_heading_no_update(
                            runtime,
                            branch_ids=supported_branch_ids,
                            edge=edge,
                            hard_support_token=hard_support_token,
                        )
                        continue
                    valid_span_indices = [
                        span_index for span_index, span in enumerate(pair.contiguous_spans)
                        if int(span.stop - span.start) >= 3
                    ]
                    for branch_id in supported_branch_ids:
                        if not valid_span_indices:
                            runtime.record_current_heading_no_update(
                                branch_id=branch_id,
                                edge=edge,
                                hard_support_token=hard_support_token,
                                cause="NO_OWNER_PRODUCED_GAP_SAFE_SPAN_WITH_AT_LEAST_THREE_ROWS",
                            )
                            continue
                        try:
                            for span_index in valid_span_indices:
                                runtime.process_current_heading_span(
                                    branch_id=branch_id,
                                    pair=pair,
                                    span_index=span_index,
                                    hard_support_token=hard_support_token,
                                )
                        except ClassAGuardViolation:
                            raise
                        except (ValueError, RuntimeError) as exc:
                            _raise_operation_failure(
                                "QMT_EDGE", exc, edge=edge, branch_id=branch_id,
                            )
                runtime.finish_current_heading()
                runtime.assess_current_physical_candidates()
                snapshot = runtime.commit_current_progressive()
            return snapshot, retry_evidence + [{
                "retry_index": retry_index,
                "status": "COMMITTED_ONCE",
                "suppressed_pair_edges": sorted(suppressed_pairs),
                "suppressed_center_edges": sorted(suppressed_centers),
                "suppressed_axis_edges": sorted(suppressed_axes),
                "suppressed_qmt_edges": sorted(suppressed_qmt_edges),
                "local_diagnostics": local_diagnostics,
            }]
        except ClassAGuardViolation:
            raise
        except _OperationFailure as exc:
            if exc.category == "PAIR_ALIGNMENT" and exc.edge is not None:
                target = suppressed_pairs
            elif exc.category == "CENTER_ESTIMATE" and exc.edge is not None:
                target = suppressed_centers
            elif exc.category == "AXIS_ESTIMATE" and exc.edge is not None:
                target = suppressed_axes
            elif exc.category == "QMT_EDGE" and exc.edge is not None:
                target = suppressed_qmt_edges
            else:
                raise
            if exc.edge in target:
                raise RuntimeError("bounded real-runner pivot repeated an already suppressed cause") from exc
            target.add(str(exc.edge))
            evidence = {
                "schema": "biospur-c2-real-ordinary-episode-pivot-v1",
                "execution_role": role_label,
                "run_attempt_index": run_attempt_index,
                "chronological_index": chronological_index,
                "action": action,
                "retry_index": retry_index,
                "failure_category": exc.category,
                "edge": exc.edge,
                "branch_id": exc.branch_id,
                "exception_type": exc.original_type,
                "exception_message": exc.original_message,
                "original_owner_diagnostic": _jsonable(exc.original_diagnostic),
                "causal_pivot": (
                    "CURRENT_EDGE_ALL_BRANCHES_LOCAL_NO_UPDATE"
                    if exc.category == "QMT_EDGE"
                    else "CURRENT_LOCAL_FACTOR_OR_PAIR_NO_UPDATE"
                ),
                "transaction_rollback_event": _jsonable(runtime.audit()["transaction_events"][-1]),
                "future_episode_used": False,
                "heldout_opened": False,
            }
            retry_relative = RUN_RELATIVE / (
                f"P2_REAL_{role_label}_EPISODE_{chronological_index:02d}_"
                f"PIVOT_{retry_index:02d}_RUN_{run_attempt_index:03d}.json"
            )
            _write_new_immutable(WORKSPACE / retry_relative, evidence)
            retry_evidence.append({**evidence, "artifact": _binding(retry_relative)})
    raise RuntimeError("registered ordinary episode retry limit exhausted")


def _drive_runtime(
    *,
    settings: Mapping[str, Any],
    initial_state: Mapping[str, Any],
    execution_role: str,
    role_label: str,
    run_attempt_index: int,
    progress: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    from biospur_fusion.v0.c2_progressive.pipeline_runtime import C2PipelineRuntime
    from biospur_fusion.v0.c2_progressive.range_reader import SealedPrefitRangeReader

    execution = settings["execution_contract"]
    plan_relative = Path(str(execution["payload_byte_access_plan_relative_path"]))
    plan_path = (WORKSPACE / plan_relative).resolve()
    nodes = tuple(
        str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    )
    runtime = C2PipelineRuntime(
        settings,
        initial_state,
        prefit_registry_seal_path=WORKSPACE / SEAL_RELATIVE,
        real_fit_activation_path=WORKSPACE / ACTIVATION_RELATIVE,
        execution_role=execution_role,
    )
    reader = SealedPrefitRangeReader(
        root=WORKSPACE,
        plan_path=plan_path,
        expected_plan_sha256=str(execution["payload_byte_access_plan_sha256"]),
        nodes=nodes,
    )
    access_rows: list[dict[str, Any]] = []
    chronology = tuple(str(value) for value in execution["chronological_actions"])
    for index, expected_action in enumerate(chronology):
        progress.update({
            "execution_role": execution_role,
            "stage": "BOUNDED_PREFIT_ORIENTATION_READ",
            "chronological_index": index,
            "action": expected_action,
            "primary_actions_read": (
                index if execution_role == "PRIMARY_CAUSAL" else progress["primary_actions_read"]
            ),
            "fresh_actions_read": (
                index if execution_role == "FRESH_RAW_RECOMPUTATION" else progress["fresh_actions_read"]
            ),
        })
        try:
            decoded = reader.read_action(index)
        except BaseException:
            failed = reader.last_read_attempt_audit
            if failed is not None:
                per_action_relative = RUN_RELATIVE / (
                    f"P2_REAL_{role_label}_READ_{index:02d}_RUN_{run_attempt_index:03d}.json"
                )
                _write_new_immutable(WORKSPACE / per_action_relative, {
                    "schema": "biospur-c2-real-prefit-per-action-read-decode-evidence-v1",
                    "execution_role": execution_role,
                    "run_attempt_index": run_attempt_index,
                    "reader_session_id": reader.reader_session_id,
                    "chronological_index": index,
                    "action": expected_action,
                    **dict(failed),
                    "heldout_opened": False,
                })
                progress[f"{role_label.lower()}_per_action_read_evidence"].append(
                    _binding(per_action_relative)
                )
            raise
        if decoded.action != expected_action:
            raise RuntimeError("bounded reader action differs from exact registered chronology")
        access_rows.append(dict(decoded.access_audit))
        per_action_relative = RUN_RELATIVE / (
            f"P2_REAL_{role_label}_READ_{index:02d}_RUN_{run_attempt_index:03d}.json"
        )
        _write_new_immutable(WORKSPACE / per_action_relative, {
            "schema": "biospur-c2-real-prefit-per-action-read-decode-evidence-v1",
            "execution_role": execution_role,
            "run_attempt_index": run_attempt_index,
            "reader_session_id": reader.reader_session_id,
            "chronological_index": index,
            "action": expected_action,
            "access_audit": dict(decoded.access_audit),
            "decode_audit": dict(decoded.decode_audit),
            "heldout_opened": False,
        })
        access_rows[-1]["per_action_evidence"] = _binding(per_action_relative)
        progress[f"{role_label.lower()}_per_action_read_evidence"].append(
            _binding(per_action_relative)
        )
        runtime.ingest_orientation_episode(decoded)
        if execution_role == "PRIMARY_CAUSAL":
            progress["primary_actions_read"] = index + 1
        else:
            progress["fresh_actions_read"] = index + 1
    runtime.finish_orientation_and_begin_calibration()
    access_relative = RUN_RELATIVE / (
        f"P2_REAL_{role_label}_EXACT_READ_AUDIT_{run_attempt_index:03d}.json"
    )
    _write_new_immutable(WORKSPACE / access_relative, {
        "schema": "biospur-c2-real-prefit-exact-read-audit-v1",
        "execution_role": execution_role,
        "reader_session_id": reader.reader_session_id,
        "plan": {"path": str(plan_relative), "sha256": reader.plan_sha256},
        "action_access": access_rows,
        "capture_wide_continuity": reader.state.audit(),
        "per_action_evidence": [row["per_action_evidence"] for row in access_rows],
        "geometry_reference_time_owner": "C2PipelineRuntime._derive_current_geometry_reference",
        "heldout_opened": False,
        "whole_file_stat_hash_or_traversal": False,
    })
    progress[f"{role_label.lower()}_read_audit"] = _binding(access_relative)

    retry_limit = int(execution["real_runner"]["ordinary_episode_retry_limit"])
    prefix_bindings: list[dict[str, str]] = []
    episode_retry_evidence: list[list[dict[str, Any]]] = []
    for index, action in enumerate(chronology):
        progress.update({
            "execution_role": execution_role,
            "stage": "CAUSAL_CALIBRATION_EPISODE",
            "chronological_index": index,
            "action": action,
        })
        snapshot, retries = _run_episode(
            runtime,
            chronological_index=index,
            action=action,
            role_label=role_label,
            run_attempt_index=run_attempt_index,
            retry_limit=retry_limit,
        )
        prefix_relative = RUN_RELATIVE / (
            f"P2_REAL_{role_label}_PREFIX_{index:02d}_RUN_{run_attempt_index:03d}.json"
        )
        _write_new_immutable(WORKSPACE / prefix_relative, {
            **_snapshot_evidence(snapshot),
            "execution_role": execution_role,
            "run_attempt_index": run_attempt_index,
            "ordinary_pivots": retries,
        })
        prefix_bindings.append(_binding(prefix_relative))
        episode_retry_evidence.append(retries)
    runtime.freeze_fit()
    return runtime, {
        "execution_role": execution_role,
        "reader_session_id": reader.reader_session_id,
        "exact_read_audit": _binding(access_relative),
        "prefix_artifacts": prefix_bindings,
        "episode_retry_evidence": episode_retry_evidence,
        "heldout_opened": False,
    }


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("real progressive runner must run only from canonical Fusion_Part")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    run_tmp = (WORKSPACE / RUN_RELATIVE / "tmp").resolve()
    run_tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(run_tmp)
    index = _next_attempt_index()
    running_relative = RUN_RELATIVE / f"P2_REAL_PROGRESSIVE_RUNNING_{index:03d}.json"
    attempt_relative = RUN_RELATIVE / f"P2_REAL_PROGRESSIVE_ATTEMPT_{index:03d}.json"
    gate_relative = RUN_RELATIVE / f"P2_REAL_PROGRESSIVE_FRESH_GATE_{index:03d}.json"
    running_path = WORKSPACE / running_relative
    attempt_path = WORKSPACE / attempt_relative
    gate_path = WORKSPACE / gate_relative
    started = _utc_now()
    progress: dict[str, Any] = {
        "stage": "VALIDATE_STATIC_AUTHORITY_BEFORE_PAYLOAD",
        "primary_actions_read": 0,
        "fresh_actions_read": 0,
        "primary_per_action_read_evidence": [],
        "fresh_per_action_read_evidence": [],
        "heldout_opened": False,
    }
    running_written = False
    try:
        amendment, seal, activation, source_revalidation = _validated_static_authority()
        settings = amendment["effective_settings"]
        initial_relative = Path(
            str(settings["execution_contract"]["initial_stochastic_state_relative_path"])
        )
        initial_path = WORKSPACE / initial_relative
        if (
            not initial_path.is_file()
            or _sha(initial_path)
            != settings["execution_contract"]["initial_stochastic_state_file_sha256"]
        ):
            raise RuntimeError("registered initial stochastic state file authority changed")
        initial_state = json.loads(initial_path.read_text(encoding="utf-8"))
        if _semantic_sha(initial_state) != settings["execution_contract"][
            "initial_stochastic_state_semantic_sha256"
        ]:
            raise RuntimeError("registered initial stochastic state semantic hash changed")
        _write_new_immutable(running_path, {
            "schema": "biospur-c2-real-progressive-running-v1",
            "started_utc": started,
            "run_attempt_index": index,
            "status": "RUNNING_APPEND_ONLY_TERMINAL_RECORD_SEPARATE",
            "prefit_registry_seal": _binding(SEAL_RELATIVE),
            "real_fit_activation": _binding(ACTIVATION_RELATIVE),
            "settings_semantic_sha256": seal["settings_semantic_sha256"],
            "qualified_source_hashes": seal["qualified_source_hashes"],
            "qualified_source_runtime_revalidation": source_revalidation,
            "initial_stochastic_state": {
                "path": str(initial_relative), "sha256": _sha(initial_path),
            },
            "execution_roles": ["PRIMARY_CAUSAL", "FRESH_RAW_RECOMPUTATION"],
            "distinct_bounded_reader_sessions_required": True,
            "heldout_decode_planned_in_this_entrypoint": False,
            "legacy_c2_basis_ik_rebase_anthropometric_fit_imported": False,
            "failure_evidence_policy": "ONE_UNIQUE_TERMINAL_RECORD_PLUS_PREFIX_AND_PIVOT_ARTIFACTS",
            "bytecode_write_disabled_before_project_import": bool(sys.dont_write_bytecode),
            "tmpdir": str(run_tmp.relative_to(WORKSPACE)),
        })
        running_written = True
        progress["qualified_source_runtime_revalidation"] = source_revalidation
        if source_revalidation["pass"] is not True:
            raise _ClassARealAuthorityError(
                "QUALIFIED_SOURCE_CLOSURE_CHANGED_AFTER_ACTIVATION",
                "one or more sealed qualification sources failed the exact pre-payload regular-file/SHA/mode policy",
            )
        progress["stage"] = "PRIMARY_CAUSAL"
        primary, primary_evidence = _drive_runtime(
            settings=settings,
            initial_state=initial_state,
            execution_role="PRIMARY_CAUSAL",
            role_label="PRIMARY",
            run_attempt_index=index,
            progress=progress,
        )
        progress["stage"] = "FRESH_RAW_RECOMPUTATION"
        fresh, fresh_evidence = _drive_runtime(
            settings=settings,
            initial_state=initial_state,
            execution_role="FRESH_RAW_RECOMPUTATION",
            role_label="FRESH",
            run_attempt_index=index,
            progress=progress,
        )
        progress["stage"] = "VERIFY_ALL_CAUSAL_PREFIXES_AND_FROZEN_SCIENTIFIC_OUTPUTS"
        verification = primary.verify_final_raw_fresh_runtime(fresh)
        if primary_evidence["reader_session_id"] == fresh_evidence["reader_session_id"]:
            raise RuntimeError("primary and fresh readers unexpectedly share one session identity")
        frozen_export = primary.export_frozen_scientific_state()
        frozen_before = _frozen_export_digest(frozen_export)
        frozen_npz_relative = RUN_RELATIVE / f"P2_REAL_FROZEN_SCIENTIFIC_STATE_{index:03d}.npz"
        _write_frozen_npz(WORKSPACE / frozen_npz_relative, frozen_export["arrays"])
        frozen_manifest_relative = RUN_RELATIVE / f"P2_REAL_FROZEN_SCIENTIFIC_STATE_{index:03d}.json"
        _write_new_immutable(WORKSPACE / frozen_manifest_relative, {
            "schema": "biospur-c2-reloadable-frozen-scientific-state-manifest-v1",
            "created_utc": _utc_now(),
            "run_attempt_index": index,
            "prefit_registry_seal": _binding(SEAL_RELATIVE),
            "real_fit_activation": _binding(ACTIVATION_RELATIVE),
            "settings_semantic_sha256": seal["settings_semantic_sha256"],
            "qualified_source_hashes": seal["qualified_source_hashes"],
            "fresh_verification": verification,
            "npz": {"path": str(frozen_npz_relative), "sha256": _sha(WORKSPACE / frozen_npz_relative)},
            "array_bindings": frozen_before["array_bindings"],
            "structure": frozen_export["structure"],
            "structure_semantic_sha256": frozen_before["structure_semantic_sha256"],
            "scientific_state_mutable": False,
            "threshold_or_parameter_override_allowed": False,
            "fit_refit_rebase_ik_or_anthropometric_geometry_allowed": False,
            "heldout_opened_when_exported": False,
        })
        _write_new_immutable(gate_path, {
            "schema": "biospur-c2-real-progressive-distinct-raw-fresh-gate-v1",
            "created_utc": _utc_now(),
            "run_attempt_index": index,
            "prefit_registry_seal": _binding(SEAL_RELATIVE),
            "real_fit_activation": _binding(ACTIVATION_RELATIVE),
            "settings_semantic_sha256": seal["settings_semantic_sha256"],
            "qualified_source_hashes": seal["qualified_source_hashes"],
            "primary": primary_evidence,
            "fresh": fresh_evidence,
            "verification": verification,
            "reloadable_frozen_scientific_state": _binding(frozen_manifest_relative),
            "fresh_raw_gate_pass": True,
            "heldout_opened": False,
            "scientific_acceptance_pass": False,
            "status": "TRAINING_FROZEN_AND_FRESH_RAW_AGREEMENT;HELDOUT_STILL_CLOSED;NOT_FINAL_PASS",
        })
        progress["stage"] = "OPEN_HOLDOUT_OWNER_ONLY_AFTER_IMMUTABLE_FRESH_GATE"
        primary.open_holdout()
        progress["heldout_opened"] = True
        from biospur_fusion.v0.c2_progressive.architecture_guard import ClassAGuardViolation

        post_holdout_refit_rejected = False
        try:
            primary.request_refit()
        except ClassAGuardViolation as exc:
            post_holdout_refit_rejected = exc.code == "HELDOUT_LEAK_OR_POST_HELDOUT_REFIT"
        if not post_holdout_refit_rejected:
            raise RuntimeError("post-heldout refit mutation was not rejected by the frozen owner")
        frozen_after = _frozen_export_digest(primary.export_frozen_scientific_state())
        if frozen_after != frozen_before:
            raise RuntimeError("holdout transition or rejected refit altered frozen scientific state")
        holdout_transition_relative = RUN_RELATIVE / (
            f"P2_REAL_HOLDOUT_TRANSITION_{index:03d}.json"
        )
        _write_new_immutable(WORKSPACE / holdout_transition_relative, {
            "schema": "biospur-c2-post-fresh-holdout-transition-v1",
            "created_utc": _utc_now(),
            "run_attempt_index": index,
            "prefit_registry_seal": _binding(SEAL_RELATIVE),
            "real_fit_activation": _binding(ACTIVATION_RELATIVE),
            "settings_semantic_sha256": seal["settings_semantic_sha256"],
            "qualified_source_hashes": seal["qualified_source_hashes"],
            "fresh_gate": _binding(gate_relative),
            "reloadable_frozen_scientific_state": _binding(frozen_manifest_relative),
            "open_holdout_called_after_fresh_gate_was_immutable": True,
            "post_holdout_refit_mutation_rejected": True,
            "geometry_frames_heading_progressive_arrays_unchanged": True,
            "settings_and_threshold_semantic_hash_unchanged": True,
            "frozen_state_digest_before": frozen_before,
            "frozen_state_digest_after": frozen_after,
            "heldout_owner_opened": True,
            "heldout_payload_bytes_decoded": False,
            "scientific_acceptance_pass": False,
        })
        primary_audit_relative = RUN_RELATIVE / f"P2_REAL_PRIMARY_RUNTIME_AUDIT_{index:03d}.json"
        fresh_audit_relative = RUN_RELATIVE / f"P2_REAL_FRESH_RUNTIME_AUDIT_{index:03d}.json"
        _write_new_immutable(WORKSPACE / primary_audit_relative, primary.audit())
        _write_new_immutable(WORKSPACE / fresh_audit_relative, fresh.audit())
        _write_new_immutable(attempt_path, {
            "schema": "biospur-c2-real-progressive-attempt-v1",
            "run_attempt_index": index,
            "started_utc": started,
            "completed_utc": _utc_now(),
            "status": "FRESH_RAW_GATE_PASS_NOT_FINAL_SCIENTIFIC_PASS",
            "running_manifest": _binding(running_relative),
            "fresh_gate": _binding(gate_relative),
            "holdout_transition": _binding(holdout_transition_relative),
            "reloadable_frozen_scientific_state": _binding(frozen_manifest_relative),
            "primary_runtime_audit": _binding(primary_audit_relative),
            "fresh_runtime_audit": _binding(fresh_audit_relative),
            "primary_reader_session_id": primary_evidence["reader_session_id"],
            "fresh_reader_session_id": fresh_evidence["reader_session_id"],
            "distinct_reader_sessions": True,
            "primary_prefix_count": len(primary_evidence["prefix_artifacts"]),
            "fresh_prefix_count": len(fresh_evidence["prefix_artifacts"]),
            "fresh_raw_gate_pass": True,
            "heldout_owner_opened_after_fresh_gate": True,
            "heldout_payload_bytes_decoded": False,
            "scientific_acceptance_pass": False,
            "legacy_c2_basis_ik_rebase_anthropometric_fit_used": False,
            "exit_status": 0,
        })
        print(json.dumps({
            "fresh_raw_gate_pass": True,
            "scientific_acceptance_pass": False,
            "heldout_owner_opened_after_fresh_gate": True,
            "heldout_payload_bytes_decoded": False,
            "attempt": _binding(attempt_relative),
            "fresh_gate": _binding(gate_relative),
        }, indent=2, sort_keys=True))
        return 0
    except BaseException as exc:
        failure = {
            "schema": "biospur-c2-real-progressive-attempt-v1",
            "run_attempt_index": index,
            "started_utc": started,
            "completed_utc": _utc_now(),
            "status": "FAIL_EXCEPTION_PRESERVED",
            "failure_stage": progress.get("stage"),
            "progress": progress,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "class_a_code": (
                exc.code if isinstance(exc, _ClassARealAuthorityError) else None
            ),
            "traceback": traceback.format_exc(),
            "running_manifest": _binding(running_relative) if running_written else None,
            "fresh_raw_gate_pass": False,
            "heldout_opened": bool(progress.get("heldout_opened", False)),
            "scientific_acceptance_pass": False,
            "causal_pivot_required_before_retry": True,
            "exit_status": 2,
        }
        failure_relative = attempt_relative
        if attempt_path.exists():
            failure_relative = RUN_RELATIVE / (
                f"P2_REAL_PROGRESSIVE_EXCEPTION_AFTER_ATTEMPT_{index:03d}.json"
            )
        _write_new_immutable(WORKSPACE / failure_relative, failure)
        print(json.dumps({
            "fresh_raw_gate_pass": False,
            "heldout_opened": bool(progress.get("heldout_opened", False)),
            "failure": _binding(failure_relative),
            "failure_stage": progress.get("stage"),
            "exception_type": type(exc).__name__,
        }, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
