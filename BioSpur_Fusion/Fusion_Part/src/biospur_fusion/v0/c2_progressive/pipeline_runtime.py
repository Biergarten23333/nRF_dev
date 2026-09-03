"""Causal per-episode owner for the complete C2 P2-P6 pipeline."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import fields, is_dataclass, replace
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping, Sequence
from uuid import uuid4

import numpy as np
from scipy.spatial.transform import Rotation

from .architecture_guard import C2ExecutionGuard, ClassAGuardViolation
from .calibration_posterior import (
    marginalize_axis_class_c,
    marginalize_center_class_c,
)
from .center_prefix import CausalCenterPrefixOwner, CenterPrefixSelection
from .functional_geometry import (
    EDGE_ACTIONS,
    EDGE_SPECS,
    HINGE_EDGES,
    AlignedPair,
    AxisEstimate,
    CenterEstimate,
    aligned_pair as build_aligned_pair,
    canonical_hinge_sign_branch_ids,
    estimate_hinge_axis_qmt,
    estimate_joint_center_pair_local,
)
from .geometry_posterior import ProgressiveFunctionalGeometryOwner
from .heading import HeadingSpanResult, HeadingTrajectoryResult, PersistentHeadingOwner
from .orientation import ContinuousVQFState, OrientedAction
from .orientation_uncertainty import (
    cumulative_observed_orientation_terms,
    physical_orientation_covariance,
)
from .progressive import PrequentialPrediction, ProgressiveCalibrationState, ProgressiveSnapshot
from .quaternion_contract import qmt_wxyz_to_scipy_active
from .range_reader import DecodedAction
from .scientific_fk import (
    PhysicalTrajectoryCandidateAssessment,
    ScientificFKResult,
    ScientificForwardKinematicsOwner,
    physical_input_binding_token,
)
from .segment_frames import PhysicalCandidateAssessment, SegmentFrameBranch, SegmentFrameBranchOwner
from .timebase import PersistentPairClockState


P1_MONITOR_TASK_ID = "01a04d0f-58f1-7240-b72f-3bf5b44a2156"
P1_R3_REVISION_SHA256 = "123538a601d0e97163d01bc4ebb13c1be1ec256dc95052821dd5f9d60eb966a6"
P1_R3_WORKER_PIXEL_AUDIT_SHA256 = "0d6fc18cb66a81e91d771792bfb4697e4fdf30e5c7c5eb4851b301856a1e300b"
P1_R2_CORRECTION_SHA256 = "a1c50a684bdbe614a5f246dc5f57f2c4c2e94bfe1f794156964bcebc4402ebb3"
P1_R3_IMAGE_SHA256_BY_NAME = {
    "P1_INPUT_HEALTH_R3.png": "d2bc316b4d6a791befac167ffed64845c5f681b635b11b9f5754688f1a9cd1bf",
    "P1_TIME_AND_GAP_R3.png": "51b79cc0a2cf96d3efd781bea1a87c5567240cb0b5e34802665c0f9b8bd33d06",
    "P1_BIAS_AND_DRIFT_R3.png": "fa733c3aa29679c316992ef56a7872a0d8c9af72d582d831cce5dd7693df99c1",
    "P1_SENSOR_SEGMENT_FRAME_DIAGNOSTIC_R3.png": "495c1a080b5e98525b893033c0c27d2492fb63e53e6725c182dfb743be2c60ff",
}


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(item) for item in value)
    if isinstance(value, np.ndarray):
        array = np.asarray(value).copy()
        array.setflags(write=False)
        return array
    return value


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_semantic_sha256(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _owner_authenticated_orientation_replay_arrays(
    oriented: OrientedAction,
) -> dict[str, np.ndarray]:
    """Serialize one causal oriented episode for later official-QMT replay.

    The export keeps the already calibrated accelerometer and gyro rows, their
    exact gap/boot ownership, and the continuous VQF quaternion rows together.
    It performs no refit, resampling, or heading correction.  Keeping both
    inertial streams under the same owner binding is required for a later
    edge-local nonhinge heading likelihood; exporting only gyro would make
    that likelihood impossible to reproduce without reopening the payload.
    """

    nodes = tuple(sorted(oriented.quat_world_sensor_wxyz_by_node))
    required_node_sets = (
        set(oriented.time_us_by_node),
        set(oriented.derived_boot_epoch_by_node),
        set(oriented.contiguous_span_id_by_node),
        set(oriented.acc_mps2_by_node),
        set(oriented.gyro_rads_by_node),
        set(oriented.gap_only_orientation_covariance_rad2_by_node),
    )
    if any(node_set != set(nodes) for node_set in required_node_sets):
        raise RuntimeError("orientation replay export node ownership is incomplete")
    prefix = f"orientation/{oriented.chronological_index:02d}"
    arrays: dict[str, np.ndarray] = {}
    for node in nodes:
        time = np.asarray(oriented.time_us_by_node[node], dtype=np.int64)
        boot = np.asarray(oriented.derived_boot_epoch_by_node[node], dtype=np.int64)
        span = np.asarray(oriented.contiguous_span_id_by_node[node], dtype=np.int32)
        accelerometer = np.asarray(oriented.acc_mps2_by_node[node], dtype=float)
        gyro = np.asarray(oriented.gyro_rads_by_node[node], dtype=float)
        quaternion = np.asarray(
            oriented.quat_world_sensor_wxyz_by_node[node], dtype=float,
        )
        gap_covariance = np.asarray(
            oriented.gap_only_orientation_covariance_rad2_by_node[node], dtype=float,
        )
        if (
            time.ndim != 1
            or boot.shape != time.shape
            or span.shape != time.shape
            or accelerometer.shape != (len(time), 3)
            or gyro.shape != (len(time), 3)
            or quaternion.shape != (len(time), 4)
            or gap_covariance.shape != (len(time), 3, 3)
            or not np.isfinite(accelerometer).all()
            or not np.isfinite(gyro).all()
            or not np.isfinite(quaternion).all()
            or not np.isfinite(gap_covariance).all()
        ):
            raise RuntimeError("orientation replay export arrays are inconsistent")
        arrays[f"{prefix}/{node}/time_us"] = time.copy()
        arrays[f"{prefix}/{node}/derived_boot_epoch"] = boot.copy()
        arrays[f"{prefix}/{node}/contiguous_span_id"] = span.copy()
        arrays[f"{prefix}/{node}/acc_mps2"] = accelerometer.copy()
        arrays[f"{prefix}/{node}/gyro_rads"] = gyro.copy()
        arrays[f"{prefix}/{node}/quat_world_sensor_wxyz"] = quaternion.copy()
        arrays[f"{prefix}/{node}/gap_only_covariance_rad2"] = gap_covariance.copy()
    return arrays


def _owner_authenticated_aligned_pair_replay_arrays(
    aligned_pair_history: Mapping[int, Mapping[str, AlignedPair]],
) -> dict[str, np.ndarray]:
    """Serialize committed timing-owner row maps without realignment.

    The maps index the owner-authenticated orientation arrays from the same
    causal action.  Runtime capability tokens remain process-local; stable
    source indices and gap-safe half-open spans are the replayable evidence.
    """

    arrays: dict[str, np.ndarray] = {}
    for chronological_index, by_edge in sorted(aligned_pair_history.items()):
        if chronological_index < 0:
            raise RuntimeError("aligned-pair replay history index is invalid")
        for edge, pair in sorted(by_edge.items()):
            provenance = dict(pair.provenance)
            binding = dict(provenance.get("owner_binding_payload", {}))
            parent = np.asarray(pair.alignment.parent_indices, dtype=np.int64)
            child = np.asarray(pair.alignment.child_indices, dtype=np.int64)
            spans = np.asarray(
                [[span.start, span.stop] for span in pair.contiguous_spans],
                dtype=np.int64,
            ).reshape(-1, 2)
            if (
                pair.edge != edge
                or provenance.get("edge") != edge
                or provenance.get("chronological_index") != chronological_index
                or binding.get("edge") != edge
                or binding.get("chronological_index") != chronological_index
                or parent.ndim != 1
                or child.shape != parent.shape
                or not len(parent)
                or np.any(parent < 0)
                or np.any(child < 0)
                or not len(spans)
                or np.any(spans[:, 0] < 0)
                or np.any(spans[:, 1] <= spans[:, 0])
                or np.any(spans[:, 1] > len(parent))
                or np.any(spans[1:, 0] < spans[:-1, 1])
            ):
                raise RuntimeError("aligned-pair replay history ownership is inconsistent")

            def digest(value: np.ndarray) -> str:
                return sha256(
                    np.ascontiguousarray(value).view(np.uint8)
                ).hexdigest()

            expected_hashes = {
                "parent_source_indices_sha256": digest(parent),
                "child_source_indices_sha256": digest(child),
                "parent_acc_sha256": digest(np.asarray(pair.parent_acc, dtype=float)),
                "child_acc_sha256": digest(np.asarray(pair.child_acc, dtype=float)),
                "parent_gyro_sha256": digest(np.asarray(pair.parent_gyro, dtype=float)),
                "child_gyro_sha256": digest(np.asarray(pair.child_gyro, dtype=float)),
            }
            if any(binding.get(key) != value for key, value in expected_hashes.items()):
                raise RuntimeError("aligned-pair replay history array hash changed")
            prefix = f"aligned_pair/{chronological_index:02d}/{edge}"
            arrays[f"{prefix}/parent_source_indices"] = parent.copy()
            arrays[f"{prefix}/child_source_indices"] = child.copy()
            arrays[f"{prefix}/contiguous_span_half_open"] = spans.copy()
    return arrays


def _validated_prefit_seal_authority(
    runtime_settings: Mapping[str, Any],
    seal_path: str | Path,
    *,
    real_diagnostic_source_delta_path: str | Path | None = None,
    fresh_continuation_source_delta_path: str | Path | None = None,
) -> Mapping[str, Any]:
    """Recompute the immutable prefit seal/amendment/source authority chain."""

    workspace = Path(str(runtime_settings["execution_contract"]["canonical_workspace"])).resolve()
    source_workspace = Path(__file__).resolve().parents[4]
    if workspace != source_workspace:
        raise ValueError("runtime settings attempt to redirect authority outside the canonical Fusion_Part source")
    expected_seal = (
        workspace / str(runtime_settings["execution_contract"]["prefit_registry_seal_relative_path"])
    ).resolve()
    observed_seal = Path(seal_path).resolve()
    if observed_seal != expected_seal or not observed_seal.is_file():
        raise ValueError("runtime prefit seal path differs from the exact registered canonical path")
    seal = json.loads(observed_seal.read_text(encoding="utf-8"))
    if seal.get("schema") != "biospur-c2-p2-prefit-registry-seal-v2":
        raise ValueError("runtime prefit seal schema is not the qualified v2 authority")

    def validate_bound_file(binding: Mapping[str, Any], *, label: str) -> Path:
        path = (workspace / str(binding["path"])).resolve()
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"{label} path leaves the canonical workspace") from exc
        if not path.is_file() or _sha256_file(path) != binding.get("sha256"):
            raise ValueError(f"{label} immutable SHA-256 binding failed")
        return path

    validate_bound_file(seal["append_only_parent"], label="prefit parent seal")
    amendment_path = validate_bound_file(seal["amendment"], label="prefit amendment")
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    if amendment.get("schema") != "biospur-c2-active-parameter-registry-prefit-amendment-v2":
        raise ValueError("prefit amendment schema differs from the sealed v2 registry amendment")
    if amendment.get("append_only_parent") != seal.get("amendment_append_only_parent"):
        raise ValueError("prefit seal does not bind the amendment's append-only parent")
    effective_settings = amendment.get("effective_settings")
    settings_hash = _json_semantic_sha256(effective_settings)
    if settings_hash != seal.get("settings_semantic_sha256"):
        raise ValueError("prefit seal settings semantic hash differs from amendment content")
    if _json_semantic_sha256(runtime_settings) != settings_hash:
        raise ValueError("runtime settings are not the exact amendment settings bound by the seal")
    source_hashes = seal.get("qualified_source_hashes")
    if not isinstance(source_hashes, Mapping) or source_hashes != amendment.get("qualified_source_hashes"):
        raise ValueError("prefit seal/amendment qualified source closure differs")
    mandatory_sources = set(runtime_settings["execution_contract"]["mandatory_qualified_source_paths"])
    if set(source_hashes) != mandatory_sources:
        raise ValueError("prefit qualified source closure differs from the exact registered path set")
    base_source_hashes = dict(source_hashes)
    effective_source_hashes = dict(base_source_hashes)
    source_delta_authority: dict[str, Any] | None = None
    if (
        real_diagnostic_source_delta_path is not None
        and fresh_continuation_source_delta_path is not None
    ):
        raise ValueError("only one continuation source-delta authority may be active")
    continuation_delta_path = (
        real_diagnostic_source_delta_path
        if real_diagnostic_source_delta_path is not None
        else fresh_continuation_source_delta_path
    )
    if continuation_delta_path is not None:
        delta_path = Path(continuation_delta_path).resolve()
        delta_path.relative_to(workspace)
        if not delta_path.is_file() or delta_path.stat().st_mode & 0o222:
            raise ValueError("continuation source-delta manifest is absent or mutable")
        delta = json.loads(delta_path.read_text(encoding="utf-8"))
        seal_binding = {
            "path": str(observed_seal.relative_to(workspace)),
            "sha256": _sha256_file(observed_seal),
        }
        if fresh_continuation_source_delta_path is not None:
            overrides = delta.get("source_hash_overrides")
            if not isinstance(overrides, Mapping) or not set(overrides).issubset(
                mandatory_sources
            ):
                raise ValueError("fresh continuation source-hash overrides are invalid")
            effective = {**base_source_hashes, **dict(overrides)}
        else:
            effective = delta.get("effective_qualified_source_hashes")
        changed_paths = sorted(
            relative
            for relative in mandatory_sources
            if isinstance(effective, Mapping)
            and effective.get(relative) != base_source_hashes.get(relative)
        )
        common_valid = bool(
            delta.get("parent_prefit_registry_seal") == seal_binding
            and delta.get("settings_semantic_sha256") == settings_hash
            and (
                delta.get("base_qualified_source_hashes") == base_source_hashes
                if real_diagnostic_source_delta_path is not None
                else delta.get("base_qualified_source_hashes_sha256")
                == _json_semantic_sha256(base_source_hashes)
            )
            and isinstance(effective, Mapping)
            and set(effective) == mandatory_sources
            and delta.get("authorized_changed_source_paths") == changed_paths
            and delta.get("new_seal_created") is False
            and delta.get("heldout_opened") is False
            and delta.get("scientific_thresholds_changed") is False
            and delta.get("seed_action_optimizer_or_physical_gate_changed") is False
            and delta.get("scientific_pass_authorized") is False
        )
        if real_diagnostic_source_delta_path is not None:
            user_owner_path = validate_bound_file(
                delta["user_online_branch_posterior_owner_amendment"],
                label="source-delta user online branch owner amendment",
            )
            focused_gate_path = validate_bound_file(
                delta["focused_owner_and_renderer_gate"],
                label="source-delta focused owner and renderer gate",
            )
            user_owner = json.loads(user_owner_path.read_text(encoding="utf-8"))
            focused_gate = json.loads(focused_gate_path.read_text(encoding="utf-8"))
            specialized_valid = bool(
                delta.get("schema")
                == "biospur-c2-real-diagnostic-authorized-source-delta-v1"
                and delta.get("authority_role")
                == "APPEND_ONLY_USER_AUTHORIZED_NON_SEAL_CONTINUATION_SOURCE_DELTA"
                and user_owner.get("schema")
                == "biospur-c2-user-online-branch-posterior-owner-amendment-v1"
                and user_owner.get("status")
                == "ACTIVE_APPEND_ONLY_USER_AUTHORITY_OWNER_REPLACEMENT"
                and focused_gate.get("schema")
                == "C2_ONLINE_LOW_INFORMATION_AXIS_AND_DISPLAY_TIME_FOCUSED_GATE_V2"
                and focused_gate.get("status") == "PASS_FOCUSED_OWNER_AND_RENDERER_ONLY"
                and focused_gate.get("failed") == 0
                and focused_gate.get("payload_opened") is False
                and focused_gate.get("heldout_opened") is False
            )
            error_label = "real diagnostic source-delta authority is inconsistent"
        else:
            causal_scope_path = validate_bound_file(
                delta["causal_curve_scope_qa"],
                label="fresh continuation causal curve scope QA",
            )
            runner_path = validate_bound_file(
                delta["fresh_gate_runner"],
                label="fresh continuation runner",
            )
            causal_scope = json.loads(causal_scope_path.read_text(encoding="utf-8"))
            parent_activation_path = validate_bound_file(
                delta["parent_real_diagnostic_activation"],
                label="fresh continuation parent diagnostic activation",
            )
            parent_activation = json.loads(
                parent_activation_path.read_text(encoding="utf-8")
            )
            specialized_valid = bool(
                delta.get("schema")
                == "biospur-c2-user-authorized-fresh-continuation-source-delta-v1"
                and delta.get("authority_role")
                == "TRAINING_ONLY_PRIMARY_AND_INDEPENDENT_FRESH_RAW_EQUIVALENCE"
                and delta.get("execution_authorized") is True
                and delta.get("training_ranges_only") is True
                and delta.get("execution_roles")
                == ["PRIMARY_CAUSAL", "FRESH_RAW_RECOMPUTATION"]
                and delta.get("distinct_reader_sessions_required") is True
                and delta.get("retrospective_arrays_enter_comparison") is False
                and delta.get("open_holdout_after_pass") is False
                and causal_scope.get("schema")
                == "biospur-c2-independent-causal-curve-scope-qa-v1"
                and causal_scope.get("scope_limit", {}).get(
                    "full_p5_or_genuine_progressive_qualification_pass"
                )
                is False
                and causal_scope.get("fresh_gate_policy", {}).get(
                    "independently_initialized_fresh_full_chronological_training_only_runtime_required"
                )
                is True
                and parent_activation.get("schema")
                == "biospur-c2-real-training-range-diagnostic-activation-v1"
                and parent_activation.get("heldout_opened") is False
                and runner_path == (workspace / "tools/run_c2_continuation_fresh_gate.py").resolve()
            )
            error_label = "fresh continuation source-delta authority is inconsistent"
        if not common_valid or not specialized_valid:
            raise ValueError(error_label)
        effective_source_hashes = dict(effective)
        source_delta_authority = {
            "path": str(delta_path),
            "sha256": _sha256_file(delta_path),
            "document": delta,
        }
    for relative, expected_hash in effective_source_hashes.items():
        validate_bound_file(
            {"path": relative, "sha256": expected_hash},
            label=f"effective qualified source {relative}",
        )
    return {
        "seal_path": str(observed_seal),
        "seal_sha256": _sha256_file(observed_seal),
        "parent": dict(seal["append_only_parent"]),
        "amendment": dict(seal["amendment"]),
        "settings_semantic_sha256": settings_hash,
        "base_qualified_source_hashes": base_source_hashes,
        "qualified_source_hashes": effective_source_hashes,
        "real_diagnostic_source_delta": source_delta_authority,
        "fresh_continuation_source_delta": (
            source_delta_authority
            if fresh_continuation_source_delta_path is not None
            else None
        ),
        "synthetic_qualification_status_at_seal": seal.get("synthetic_qualification_status"),
    }


def _validated_real_fit_activation(
    runtime_settings: Mapping[str, Any],
    seal_authority: Mapping[str, Any],
    activation_path: str | Path,
) -> Mapping[str, Any]:
    """Validate the post-synthetic append-only authority for real training fit.

    A file hash proves identity, not scientific relevance.  Every bound
    artifact is therefore also required to carry the exact synthetic role,
    prefit seal, settings hash, source closure, schema, and complete registered
    mutation ledger that produced it.
    """

    workspace = Path(str(runtime_settings["execution_contract"]["canonical_workspace"])).resolve()
    expected = (
        workspace / str(runtime_settings["execution_contract"]["real_fit_activation_relative_path"])
    ).resolve()
    observed = Path(activation_path).resolve()
    if observed != expected or not observed.is_file():
        raise ValueError("real-fit activation path differs from the exact registered canonical path")
    if observed.stat().st_mode & 0o222:
        raise ValueError("real-fit activation must be immutable append-only authority")
    activation = json.loads(observed.read_text(encoding="utf-8"))
    if activation.get("schema") != "biospur-c2-real-training-fit-activation-v1":
        raise ValueError("real-fit activation schema is invalid")
    if activation.get("activation_role") != "REAL_TRAINING_RANGE_PRIMARY_AND_FRESH_RAW":
        raise ValueError("real-fit activation role is not the exact real-training authority")
    if activation.get("execution_authorized") is not True:
        raise ValueError("real-fit activation does not explicitly authorize execution")
    if (
        activation.get("static_validator_execution_authorized_literal") is not False
        or activation.get("static_validator_output_mutated") is not False
    ):
        raise ValueError("real-fit activation failed to preserve static/P0 execution_authorized:false")
    if activation.get("heldout_opened") is not False:
        raise ValueError("real-fit activation cannot open heldout")
    if activation.get("prefit_registry_seal") != {
        "path": str(Path(seal_authority["seal_path"]).relative_to(workspace)),
        "sha256": seal_authority["seal_sha256"],
    }:
        raise ValueError("real-fit activation binds a different prefit seal")
    if activation.get("qualified_source_hashes") != dict(seal_authority["qualified_source_hashes"]):
        raise ValueError("real-fit activation source closure differs from the prefit seal")
    if activation.get("settings_semantic_sha256") != seal_authority["settings_semantic_sha256"]:
        raise ValueError("real-fit activation settings hash differs from the prefit seal")
    if activation.get("monitor_task_id") != P1_MONITOR_TASK_ID:
        raise ValueError("real-fit activation is not bound to the required independent monitor task")

    def validate_evidence(binding: Mapping[str, Any], *, label: str) -> Mapping[str, Any]:
        path = (workspace / str(binding["path"])).resolve()
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"{label} evidence path leaves canonical workspace") from exc
        if not path.is_file() or _sha256_file(path) != binding.get("sha256"):
            raise ValueError(f"{label} evidence hash failed")
        return json.loads(path.read_text(encoding="utf-8"))

    exact_seal_binding = {
        "path": str(Path(seal_authority["seal_path"]).relative_to(workspace)),
        "sha256": seal_authority["seal_sha256"],
    }

    def validate_synthetic_authority(
        document: Mapping[str, Any],
        *,
        schema: str,
        label: str,
    ) -> None:
        if document.get("schema") != schema:
            raise ValueError(f"{label} evidence schema is unrelated to the registered gate")
        if document.get("execution_role") != "SYNTHETIC_QUALIFICATION":
            raise ValueError(f"{label} evidence did not run under the synthetic-only role")
        if document.get("prefit_registry_seal") != exact_seal_binding:
            raise ValueError(f"{label} evidence belongs to another prefit seal")
        if document.get("settings_semantic_sha256") != seal_authority["settings_semantic_sha256"]:
            raise ValueError(f"{label} evidence belongs to another settings revision")
        if document.get("qualified_source_hashes") != dict(seal_authority["qualified_source_hashes"]):
            raise ValueError(f"{label} evidence belongs to another qualified source closure")
        if document.get("real_capture_rows_opened") is not False:
            raise ValueError(f"{label} evidence cannot consume real capture rows")
        if document.get("external_holdout_opened") is not False:
            raise ValueError(f"{label} evidence cannot open heldout")

    def validate_bound_artifact(binding: Mapping[str, Any], *, label: str) -> None:
        path = (workspace / str(binding["path"])).resolve()
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"{label} leaves the canonical workspace") from exc
        if not path.is_file() or _sha256_file(path) != binding.get("sha256"):
            raise ValueError(f"{label} exact accepted hash chain failed")

    qualification = validate_evidence(activation["synthetic_qualification"], label="synthetic qualification")
    sensor_mutations = validate_evidence(activation["sensor_mutation_gate"], label="sensor mutation")
    architecture_mutations = validate_evidence(
        activation["architecture_mutation_gate"], label="architecture mutation",
    )
    qualified_test = validate_evidence(
        activation["qualified_owner_test_gate"], label="qualified owner test",
    )
    monitor_acceptance = validate_evidence(
        activation["p1_monitor_acceptance"], label="P1 monitor acceptance",
    )
    synthetic_gate = validate_evidence(activation["synthetic_gate"], label="synthetic gate")
    validate_synthetic_authority(
        qualification,
        schema="biospur-c2-p2-prefit-synthetic-qualification-attempt-v2",
        label="synthetic qualification",
    )
    validate_synthetic_authority(
        sensor_mutations,
        schema="biospur-c2-p2-sensor-numerical-mutation-gate-v1",
        label="sensor mutation",
    )
    validate_synthetic_authority(
        architecture_mutations,
        schema="biospur-c2-p2-architecture-mutation-gate-v1",
        label="architecture mutation",
    )
    validate_synthetic_authority(
        qualified_test,
        schema="biospur-c2-p2-qualified-owner-test-gate-v1",
        label="qualified owner test",
    )
    validate_synthetic_authority(
        synthetic_gate,
        schema="biospur-c2-p2-prefit-synthetic-gate-v2",
        label="synthetic gate",
    )
    if (
        qualification.get("status") != "PASS"
        or qualification.get("pass") is not True
        or qualification.get("qualification_result", {}).get("schema")
        != "biospur-c2-p2-independent-synthetic-qualification-v2"
        or qualification.get("qualification_result", {}).get("pass") is not True
    ):
        raise ValueError("real-fit activation synthetic qualification is not PASS")
    if qualification.get("sensor_mutation_gate") != activation["sensor_mutation_gate"]:
        raise ValueError("synthetic qualification binds a different sensor mutation gate")
    if qualification.get("architecture_mutation_gate") != activation["architecture_mutation_gate"]:
        raise ValueError("synthetic qualification binds a different architecture mutation gate")
    if qualification.get("qualified_owner_test_gate") != activation["qualified_owner_test_gate"]:
        raise ValueError("synthetic qualification binds a different qualified owner test gate")
    if synthetic_gate.get("qualification") != activation["synthetic_qualification"]:
        raise ValueError("synthetic gate binds a different qualification attempt")
    if synthetic_gate.get("sensor_mutation_gate") != activation["sensor_mutation_gate"]:
        raise ValueError("synthetic gate binds a different sensor mutation artifact")
    if synthetic_gate.get("architecture_mutation_gate") != activation["architecture_mutation_gate"]:
        raise ValueError("synthetic gate binds a different architecture mutation artifact")
    if synthetic_gate.get("qualified_owner_test_gate") != activation["qualified_owner_test_gate"]:
        raise ValueError("synthetic gate binds a different qualified owner test artifact")
    if synthetic_gate.get("pass") is not True or synthetic_gate.get("status") != "PASS_SYNTHETIC_ONLY":
        raise ValueError("synthetic gate is not an exact synthetic-only PASS")
    expected_test_sources = [
        {
            "path": relative,
            "sha256": seal_authority["qualified_source_hashes"][relative],
        }
        for relative in (
            "tests/v0/test_c2_p2_prefit_owners.py",
            "tests/v0/test_c2_progressive_range_reader.py",
        )
    ]
    qualified_test_command = qualified_test.get("command")
    qualified_test_environment = qualified_test.get("environment", {})
    exact_test_paths = tuple(row["path"] for row in expected_test_sources)
    if (
        qualified_test.get("status") != "PASS"
        or qualified_test.get("pass") is not True
        or qualified_test.get("returncode") != 0
        or qualified_test.get("test_sources") != expected_test_sources
        or not isinstance(qualified_test_command, list)
        or len(qualified_test_command) != 9 + len(exact_test_paths)
        or qualified_test_command[1:7]
        != ["-B", "-m", "pytest", "-p", "no:cacheprovider", "--basetemp"]
        or qualified_test_command[8:] != ["-q", *exact_test_paths]
        or not str(qualified_test_command[7]).startswith(
            str(workspace / "logs/c2_basis_progressive_20260829T102836Z/tmp/qualified_pytest_")
        )
        or qualified_test.get("working_directory") != str(workspace)
        or qualified_test_environment.get("PYTHONDONTWRITEBYTECODE") != "1"
        or qualified_test_environment.get("PYTHONPATH") != f"{workspace / 'src'}:{workspace}"
        or qualified_test_environment.get("TMPDIR")
        != "logs/c2_basis_progressive_20260829T102836Z/tmp"
        or qualified_test_environment.get("pytest_cacheprovider_disabled") is not True
        or not str(qualified_test_environment.get("pytest_basetemp", "")).startswith(
            "logs/c2_basis_progressive_20260829T102836Z/tmp/qualified_pytest_"
        )
    ):
        raise ValueError("real-fit activation lacks the exact executed cache-safe qualified owner test")

    expected_sensor = set(runtime_settings["synthetic"]["mandatory_sensor_and_numerical_mutations"])
    expected_architecture = set(runtime_settings["synthetic"]["mandatory_architecture_negative_mutations"])
    sensor_rows = sensor_mutations.get("mutations")
    architecture_rows = architecture_mutations.get("mutations")
    if not isinstance(sensor_rows, Mapping) or set(sensor_rows) != expected_sensor:
        raise ValueError("sensor mutation artifact omits or adds a registered mandatory mutation")
    if not isinstance(architecture_rows, Mapping) or set(architecture_rows) != expected_architecture:
        raise ValueError("architecture mutation artifact omits or adds a registered mandatory mutation")
    for name, row in sensor_rows.items():
        if (
            row.get("coverage_class") != "EXECUTED_OWNER_LEVEL"
            or row.get("pass") is not True
            or not row.get("owner_call_path")
            or "injected" not in row
            or "expected" not in row
            or "observed" not in row
        ):
            raise ValueError(f"sensor mutation {name} lacks executed owner-level caught evidence")
    for name, row in architecture_rows.items():
        if (
            row.get("coverage_class") != "EXECUTED_OWNER_LEVEL"
            or row.get("caught") is not True
            or row.get("expected_rejection") != name
            or row.get("observed_rejection") != name
            or not row.get("owner")
            or not row.get("callable")
        ):
            raise ValueError(f"architecture mutation {name} lacks executed owner-level rejection evidence")
    if (
        sensor_mutations.get("registered_names_match") is not True
        or sensor_mutations.get("pass") is not True
        or architecture_mutations.get("registered_names_match") is not True
        or architecture_mutations.get("declarative_only_counted_as_pass") is not False
        or architecture_mutations.get("owner_not_implemented") != []
        or architecture_mutations.get("pass") is not True
    ):
        raise ValueError("real-fit activation mutation matrix is not completely owner-executed PASS")

    if (
        monitor_acceptance.get("schema") != "biospur-c2-p1-r3-monitor-acceptance-v1"
        or monitor_acceptance.get("monitor_task_id") != P1_MONITOR_TASK_ID
        or monitor_acceptance.get("verdict")
        != "ACCEPTED_DIAGNOSTIC_ONLY_NON_ANATOMICAL_NOT_PASS"
        or monitor_acceptance.get("real_fit_authorized") is not False
    ):
        raise ValueError("real-fit activation lacks the exact P1 diagnostic monitor acceptance")
    p1_chain = monitor_acceptance.get("accepted_hash_chain", {})
    expected_chain_hashes = {
        "visual_revision_003": P1_R3_REVISION_SHA256,
        "worker_pixel_audit_003": P1_R3_WORKER_PIXEL_AUDIT_SHA256,
        "r2_correction_001": P1_R2_CORRECTION_SHA256,
    }
    if set(p1_chain) != set(expected_chain_hashes):
        raise ValueError("P1 monitor acceptance does not bind the complete R2/R3 audit chain")
    for name, expected_hash in expected_chain_hashes.items():
        binding = p1_chain[name]
        if binding.get("sha256") != expected_hash:
            raise ValueError(f"P1 monitor acceptance {name} hash differs from accepted evidence")
        validate_bound_artifact(binding, label=f"P1 {name}")
    images = monitor_acceptance.get("accepted_r3_images")
    if not isinstance(images, Sequence) or isinstance(images, (str, bytes)):
        raise ValueError("P1 monitor acceptance image bindings are missing")
    image_by_name = {Path(str(row["path"])).name: row for row in images}
    if set(image_by_name) != set(P1_R3_IMAGE_SHA256_BY_NAME):
        raise ValueError("P1 monitor acceptance does not bind the exact four R3 images")
    for name, expected_hash in P1_R3_IMAGE_SHA256_BY_NAME.items():
        binding = image_by_name[name]
        if binding.get("sha256") != expected_hash:
            raise ValueError(f"P1 accepted image {name} hash differs from monitor-reviewed pixels")
        validate_bound_artifact(binding, label=f"P1 accepted image {name}")
    return {
        "activation_path": str(observed),
        "activation_sha256": _sha256_file(observed),
        "execution_authorized": True,
        "heldout_opened": False,
        "synthetic_qualification_sha256": activation["synthetic_qualification"]["sha256"],
        "sensor_mutation_sha256": activation["sensor_mutation_gate"]["sha256"],
        "architecture_mutation_sha256": activation["architecture_mutation_gate"]["sha256"],
        "qualified_owner_test_sha256": activation["qualified_owner_test_gate"]["sha256"],
        "synthetic_gate_sha256": activation["synthetic_gate"]["sha256"],
        "p1_monitor_acceptance_sha256": activation["p1_monitor_acceptance"]["sha256"],
    }


_BOUNDED_CENTER_DIAGNOSTIC_GATE_RELATIVE = Path(
    "logs/c2_basis_progressive_20260829T102836Z/CONTINUATION_SPRINT/"
    "BOUNDED_CENTER_BUDGET_ROLLBACK_GATE_001.json"
)
_BOUNDED_CENTER_DIAGNOSTIC_GATE_SHA256 = (
    "fb89a802fcdf348113618d3c105ec64041a7c75ec7be4d6d863b1131f16d8d62"
)
_BOUNDED_CENTER_GATE_SOURCE_SEAL_BINDING = {
    "path": "logs/c2_basis_progressive_20260829T102836Z/"
    "P2_PREFIT_REGISTRY_SEAL_017.json",
    "sha256": "d94179fba3f10664acc41c49f9f0da0d6d0ebbb784caf8ae459df950e92c84f2",
}


def _validated_bounded_center_diagnostic_gate(
    workspace: Path,
    observed_gate_path: str | Path,
) -> Mapping[str, Any]:
    """Validate the exact mature rollback result, not its extra display predicate."""

    expected = (workspace / _BOUNDED_CENTER_DIAGNOSTIC_GATE_RELATIVE).resolve()
    observed = Path(observed_gate_path).resolve()
    if observed != expected or not observed.is_file() or observed.stat().st_mode & 0o222:
        raise ValueError("bounded-center diagnostic gate path is not the exact immutable authority")
    if _sha256_file(observed) != _BOUNDED_CENTER_DIAGNOSTIC_GATE_SHA256:
        raise ValueError("bounded-center diagnostic gate SHA-256 changed")
    document = json.loads(observed.read_text(encoding="utf-8"))
    before = document.get("mature_state_before_injection", {})
    after = document.get("mature_state_after_rollback", {})
    injected = document.get("injected_transaction", {})
    budget = document.get("budget_exception", {})
    budget_audit = budget.get("audit", {})
    transaction = document.get("transaction_event", {})
    before_hashes = before.get("top_level_component_hashes", {})
    after_hashes = after.get("top_level_component_hashes", {})
    required_mature_components = {
        "clock", "geometry", "frame", "heading", "progressive",
        "progress_snapshots", "heading_trajectory_history",
    }
    empty_mapping_sha256 = sha256(b"{}").hexdigest()
    if (
        document.get("schema")
        != "biospur-c2-bounded-center-budget-rollback-gate-v1"
        or document.get("status") != "FAIL"
        or document.get("pass") is not False
        or document.get("prefit_registry_seal")
        != _BOUNDED_CENTER_GATE_SOURCE_SEAL_BINDING
        or before.get("committed_prefix_count") != 9
        or after.get("committed_prefix_count") != 9
        or before.get("heading_trajectory_history_count") != 9
        or after.get("heading_trajectory_history_count") != 9
        or injected.get("chronological_index") != 9
        or injected.get("action") != "10_knee_left_seated"
        or injected.get("score_completed_before_injection") is not True
        or injected.get("current_center_selection_completed_before_injection") is not True
        or budget.get("type") != "CenterFactorAggregateBudgetExceeded"
        or budget_audit.get("budget_exhaustion_disposition")
        != "LOCAL_NO_UPDATE_BUDGET_EXHAUSTED"
        or budget_audit.get("full_coherent_sensitivity_completed") is not False
        or budget_audit.get("coherent_sensitivity_pass_claimed") is not False
        or transaction.get("status") != "ROLLED_BACK"
        or transaction.get("exception_type") != "CenterFactorAggregateBudgetExceeded"
        or transaction.get("owner_state_hashes_equal") is not True
        or transaction.get("mismatched_top_level_components") != []
        or transaction.get("top_level_component_hashes_before")
        != transaction.get("top_level_component_hashes_after")
        or document.get("mature_component_hashes_exact_after_rollback") is not True
        or before_hashes != after_hashes
        or not required_mature_components.issubset(before_hashes)
        or any(
            before_hashes[name] == empty_mapping_sha256
            for name in required_mature_components
        )
        or document.get("payload_opened") is not False
        or document.get("heldout_opened") is not False
        or document.get("scientific_pass_claimed") is not False
    ):
        raise ValueError("bounded-center diagnostic rollback invariants are incomplete")
    return {
        "path": str(observed.relative_to(workspace)),
        "sha256": _BOUNDED_CENTER_DIAGNOSTIC_GATE_SHA256,
        "committed_prefix_count": 9,
        "current_knee_selection_completed": True,
        "transaction_status": "ROLLED_BACK",
        "owner_state_hashes_equal": True,
        "mismatched_top_level_components": [],
        "physical_trajectory_history_nonempty_required": False,
    }


def _validated_real_diagnostic_activation(
    runtime_settings: Mapping[str, Any],
    seal_authority: Mapping[str, Any],
    activation_path: str | Path,
) -> Mapping[str, Any]:
    """Authenticate the user-authorized training-range diagnostic sprint only."""

    workspace = Path(
        str(runtime_settings["execution_contract"]["canonical_workspace"])
    ).resolve()
    observed = Path(activation_path).resolve()
    observed.relative_to(workspace)
    if not observed.is_file() or observed.stat().st_mode & 0o222:
        raise ValueError("real diagnostic activation is absent or mutable")
    activation = json.loads(observed.read_text(encoding="utf-8"))

    def validate_binding(binding: Mapping[str, Any], *, label: str) -> Path:
        path = (workspace / str(binding["path"])).resolve()
        path.relative_to(workspace)
        if not path.is_file() or _sha256_file(path) != binding.get("sha256"):
            raise ValueError(f"real diagnostic {label} SHA-256 binding failed")
        return path

    seal_binding = {
        "path": str(
            Path(seal_authority["seal_path"]).resolve().relative_to(workspace)
        ),
        "sha256": seal_authority["seal_sha256"],
    }
    activation_source_hashes = seal_authority.get(
        "base_qualified_source_hashes", seal_authority["qualified_source_hashes"],
    )
    if (
        activation.get("schema")
        != "biospur-c2-real-training-range-diagnostic-activation-v1"
        or activation.get("activation_role")
        != "REAL_TRAINING_RANGE_DIAGNOSTIC_ONLY"
        or activation.get("prefit_registry_seal") != seal_binding
        or activation.get("settings_semantic_sha256")
        != seal_authority["settings_semantic_sha256"]
        or activation.get("qualified_source_hashes") != activation_source_hashes
        or activation.get("execution_authorized") is not True
        or activation.get("training_ranges_only") is not True
        or activation.get("heldout_opened") is not False
        or activation.get("full_qualification_complete") is not False
        or activation.get("recapture_or_separate_calibration_motion") is not False
        or activation.get("scientific_pass_authorized") is not False
    ):
        raise ValueError("real diagnostic activation content is inconsistent")
    amendment = validate_binding(
        activation["user_calibration_posterior_amendment"],
        label="user amendment",
    )
    focused_gate = validate_binding(
        activation["focused_calibration_owner_test_gate"],
        label="focused owner test gate",
    )
    bounded_center_gate = validate_binding(
        activation["bounded_center_budget_rollback_gate"],
        label="bounded center budget rollback gate",
    )
    amendment_document = json.loads(amendment.read_text(encoding="utf-8"))
    gate_document = json.loads(focused_gate.read_text(encoding="utf-8"))
    bounded_center_validation = _validated_bounded_center_diagnostic_gate(
        workspace,
        bounded_center_gate,
    )
    source_delta = seal_authority.get("real_diagnostic_source_delta")
    if source_delta is not None:
        source_delta_document = source_delta["document"]
        if source_delta_document.get("parent_real_diagnostic_activation") != {
            "path": str(observed.relative_to(workspace)),
            "sha256": _sha256_file(observed),
        }:
            raise ValueError(
                "real diagnostic source-delta does not bind the parent activation"
            )
    if (
        amendment_document.get("schema")
        != "biospur-c2-user-calibration-posterior-amendment-v1"
        or amendment_document.get("decision", {}).get(
            "selected_owner"
        )
        != "CAUSAL_CAPTURE_WIDE_PER_SENSOR_PERSISTENT_MULTI_BRANCH_CALIBRATION_POSTERIOR"
        or amendment_document.get("decision", {}).get(
            "recapture_or_separate_calibration_motion"
        ) != "REJECTED"
        or amendment_document.get("decision", {}).get("heldout_rule")
        != "UNCHANGED"
        or gate_document.get("status") != "PASS_FOCUSED_OWNER_ONLY"
        or gate_document.get("real_fit_authorized_by_this_gate") is not False
        or gate_document.get("payload_opened") is not False
        or gate_document.get("heldout_opened") is not False
        or bounded_center_validation.get("owner_state_hashes_equal") is not True
        or bounded_center_validation.get("mismatched_top_level_components") != []
    ):
        raise ValueError("real diagnostic activation evidence has the wrong role")
    return {
        "activation_path": str(observed),
        "activation_sha256": _sha256_file(observed),
        "execution_authorized": True,
        "training_ranges_only": True,
        "heldout_opened": False,
        "full_qualification_complete": False,
        "scientific_pass_authorized": False,
        "bounded_center_budget_rollback_gate_sha256": (
            bounded_center_validation["sha256"]
        ),
        "authorized_source_delta": (
            None if source_delta is None else {
                "path": source_delta["path"],
                "sha256": source_delta["sha256"],
            }
        ),
        "effective_qualified_source_hashes": dict(
            seal_authority["qualified_source_hashes"]
        ),
    }


class PipelineStage(str, Enum):
    ORIENTATION = "ORIENTATION"
    CALIBRATION_EPISODE_READY = "CALIBRATION_EPISODE_READY"
    AWAITING_PREQUENTIAL_SCORE = "AWAITING_PREQUENTIAL_SCORE"
    CURRENT_EPISODE_LOCAL_FACTORS = "CURRENT_EPISODE_LOCAL_FACTORS"
    CURRENT_EPISODE_GEOMETRY_UPDATED = "CURRENT_EPISODE_GEOMETRY_UPDATED"
    CURRENT_EPISODE_FRAMES_UPDATED = "CURRENT_EPISODE_FRAMES_UPDATED"
    CURRENT_EPISODE_PHYSICAL_CANDIDATES_ASSESSED = "CURRENT_EPISODE_PHYSICAL_CANDIDATES_ASSESSED"
    CURRENT_EPISODE_HEADING_UPDATED = "CURRENT_EPISODE_HEADING_UPDATED"
    FIT_FROZEN = "FIT_FROZEN"
    FINAL_RAW_FRESH_VERIFIED = "FINAL_RAW_FRESH_VERIFIED"
    HOLDOUT_OPEN = "HOLDOUT_OPEN"


class C2PipelineRuntime:
    """Score prefix i-1, then transactionally ingest only episode i."""

    def __init__(
        self,
        settings: Mapping[str, Any],
        initial_stochastic_state: Mapping[str, Any],
        *,
        prefit_registry_seal_path: str | Path,
        real_fit_activation_path: str | Path | None = None,
        real_diagnostic_source_delta_path: str | Path | None = None,
        fresh_continuation_source_delta_path: str | Path | None = None,
        execution_role: str = "PRIMARY_CAUSAL",
    ) -> None:
        if execution_role not in {
            "SYNTHETIC_QUALIFICATION",
            "REAL_DIAGNOSTIC",
            "PRIMARY_CAUSAL",
            "FRESH_RAW_RECOMPUTATION",
        }:
            raise ValueError("unknown C2 pipeline execution role")
        settings_copy = deepcopy(settings)
        try:
            seal_authority = _validated_prefit_seal_authority(
                settings_copy,
                prefit_registry_seal_path,
                real_diagnostic_source_delta_path=(
                    real_diagnostic_source_delta_path
                    if execution_role == "REAL_DIAGNOSTIC"
                    else None
                ),
                fresh_continuation_source_delta_path=(
                    fresh_continuation_source_delta_path
                    if execution_role in {"PRIMARY_CAUSAL", "FRESH_RAW_RECOMPUTATION"}
                    else None
                ),
            )
        except ClassAGuardViolation:
            raise
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
            raise ClassAGuardViolation(
                "FORGED_PREFIT_SEAL_OR_ARBITRARY_SETTINGS",
                f"runtime rejected the immutable prefit seal/settings authority: {exc}",
            ) from exc
        computed_settings_hash = _json_semantic_sha256(settings_copy)
        self.settings = _deep_freeze(settings_copy)
        self._settings_semantic_sha256 = computed_settings_hash
        self._prefit_registry_seal_authority = _deep_freeze(seal_authority)
        if execution_role == "SYNTHETIC_QUALIFICATION":
            if (
                real_fit_activation_path is not None
                or real_diagnostic_source_delta_path is not None
            ):
                raise ClassAGuardViolation(
                    "FORGED_REAL_FIT_ACTIVATION",
                    "synthetic qualification runtime must not claim real-fit activation",
                )
            activation_authority = None
        elif execution_role == "REAL_DIAGNOSTIC":
            if real_fit_activation_path is None:
                raise ClassAGuardViolation(
                    "FORGED_REAL_FIT_ACTIVATION",
                    "real diagnostic runtime requires its append-only activation",
                )
            if real_diagnostic_source_delta_path is None:
                raise ClassAGuardViolation(
                    "FORGED_REAL_FIT_ACTIVATION",
                    "real diagnostic runtime requires its authorized source-delta manifest",
                )
            try:
                activation_authority = _validated_real_diagnostic_activation(
                    settings_copy, seal_authority, real_fit_activation_path,
                )
            except ClassAGuardViolation:
                raise
            except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
                raise ClassAGuardViolation(
                    "FORGED_REAL_FIT_ACTIVATION",
                    f"runtime rejected the diagnostic activation authority: {exc}",
                ) from exc
        else:
            if real_diagnostic_source_delta_path is not None:
                raise ClassAGuardViolation(
                    "FORGED_REAL_FIT_ACTIVATION",
                    "primary/fresh runtime cannot consume a diagnostic-only source delta",
                )
            fresh_authority = seal_authority.get("fresh_continuation_source_delta")
            if fresh_authority is not None:
                if real_fit_activation_path is not None:
                    raise ClassAGuardViolation(
                        "FORGED_REAL_FIT_ACTIVATION",
                        "fresh continuation authority cannot be combined with another real-fit activation",
                    )
                activation_authority = fresh_authority
            elif real_fit_activation_path is None:
                raise ClassAGuardViolation(
                    "FORGED_REAL_FIT_ACTIVATION",
                    "real primary/fresh runtime requires append-only real-fit activation",
                )
            else:
                try:
                    activation_authority = _validated_real_fit_activation(
                        settings_copy, seal_authority, real_fit_activation_path,
                    )
                except ClassAGuardViolation:
                    raise
                except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
                    raise ClassAGuardViolation(
                        "FORGED_REAL_FIT_ACTIVATION",
                        f"runtime rejected the append-only real-fit activation authority: {exc}",
                    ) from exc
        self._real_fit_activation_authority = (
            None if activation_authority is None else _deep_freeze(activation_authority)
        )
        self.execution_role = execution_role
        self._runtime_owner_id = f"C2_PIPELINE_{execution_role}_{uuid4().hex}"
        self._physical_input_binding_secret = uuid4().bytes
        self._initial_stochastic_state = _deep_freeze(deepcopy(initial_stochastic_state))
        self._initial_stochastic_state_semantic_sha256 = sha256(
            json.dumps(
                self._canonical_state(self._initial_stochastic_state),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        expected_initial_hash = self.settings["execution_contract"][
            "initial_stochastic_state_semantic_sha256"
        ]
        if expected_initial_hash != self._initial_stochastic_state_semantic_sha256:
            raise ValueError("runtime initial stochastic state differs from the registered immutable P1 authority")
        self.guard = C2ExecutionGuard(self.settings)
        self.guard.begin_capture("C2")
        self._stage = PipelineStage.ORIENTATION
        self._stage_events: list[dict[str, Any]] = []
        self._orientation_owner = ContinuousVQFState(
            self._initial_stochastic_state,
            execution_guard=self.guard,
            sample_period_s=float(self.settings["orientation"]["sample_period_s"]),
            unknown_boot_orientation_sigma_rad=float(self.settings["orientation"]["unknown_boot_orientation_sigma_rad"]),
            unknown_unusable_episode_orientation_sigma_rad=float(
                self.settings["orientation"]["unknown_unusable_episode_orientation_sigma_rad"]
            ),
            calibration_settings=self.settings["calibration_posterior"],
        )
        self._clock_owner = PersistentPairClockState(
            maximum_abs_drift_ppm=float(self.settings["timing"]["maximum_abs_drift_ppm"]),
            jitter_floor_s=float(self.settings["timing"]["jitter_floor_s"]),
        )
        self._geometry_owner = ProgressiveFunctionalGeometryOwner(self.settings["geometry_progressive"])
        self._center_prefix_owner = CausalCenterPrefixOwner(
            self.settings["joint_center"]["causal_historical_prefix_owner"],
            chronological_actions=self.settings["execution_contract"][
                "chronological_actions"
            ],
            edge_actions=EDGE_ACTIONS,
            execution_guard=self.guard,
        )
        self._frame_owner = SegmentFrameBranchOwner(self.settings["segment_frames"], execution_guard=self.guard)
        self._fk_owner = ScientificForwardKinematicsOwner(
            execution_guard=self.guard,
            physical_settings=self.settings["physical_candidates"],
            expected_runtime_owner_id=self._runtime_owner_id,
            runtime_binding_secret=self._physical_input_binding_secret,
        )
        self._heading_owner: PersistentHeadingOwner | None = None
        self._progressive_owner: ProgressiveCalibrationState | None = None
        self._progressive_layout = self._validated_progressive_layout(self.settings["progressive"])
        self._progressive_dimension = sum(int(row["dimension"]) for row in self._progressive_layout)
        canonical_branch_ids = canonical_hinge_sign_branch_ids()
        registered_branch_ids = tuple(str(value) for value in self.settings["progressive"]["branch_ids"])
        branch_count = int(self.settings["progressive"]["real_branch_count"])
        if registered_branch_ids != canonical_branch_ids or branch_count != len(canonical_branch_ids):
            raise ValueError("progressive branch IDs/order must exactly match canonical hinge sign enumeration")
        self._oriented_actions: list[OrientedAction] = []
        self._input_access_audits: list[dict[str, Any]] = []
        self._current_index: int | None = None
        self._current_action: str | None = None
        self._current_prediction: PrequentialPrediction | None = None
        self._current_reference_time_s: float | None = None
        self._current_reference_time_audit: dict[str, Any] = {}
        self._current_prequential_prior_sync_audit: dict[str, Any] = {}
        self._episode_reference_time_audits: list[dict[str, Any]] = []
        self._local_axes: dict[str, AxisEstimate] = {}
        self._local_centers: dict[str, CenterEstimate] = {}
        self._accepted_local_axes: dict[str, AxisEstimate] = {}
        self._accepted_local_centers: dict[str, CenterEstimate] = {}
        self._geometry_factor_decisions: dict[tuple[str, str], dict[str, Any]] = {}
        self._current_owned_aligned_pairs: dict[str, AlignedPair] = {}
        self._current_center_prefix_selections: dict[
            str, CenterPrefixSelection,
        ] = {}
        self._current_center_prefix_explicit_no_updates: dict[str, str] = {}
        self._current_frame_branches: tuple[SegmentFrameBranch, ...] = ()
        self._current_assessments: dict[str, PhysicalCandidateAssessment] = {}
        self._current_physical_trajectory_assessments: dict[
            str, PhysicalTrajectoryCandidateAssessment,
        ] = {}
        self._current_physical_world_from_segment: dict[
            str, Mapping[str, np.ndarray],
        ] = {}
        self._current_physical_orientation_covariance: dict[
            str, Mapping[str, np.ndarray],
        ] = {}
        self._current_physical_owner_bindings: dict[str, Mapping[str, Any]] = {}
        self._current_physical_frame_branches: dict[str, SegmentFrameBranch] = {}
        self._current_physical_soft_log_likelihood = np.zeros(branch_count, dtype=float)
        self._current_physical_input_audit: dict[str, Any] = {}
        self._current_all_physical_candidates_invalid = False
        self._current_qmt_observation_allowed = False
        self._current_qmt_ready_edges: tuple[str, ...] = ()
        self._current_full_frame_geometry_ready = False
        self._current_physical_gate_completed = False
        self._branch_ids = registered_branch_ids
        self._branch_support = np.ones(branch_count, dtype=bool)
        self._committed_wear_log_likelihood = np.zeros(branch_count, dtype=float)
        self._wear_prior_initialized = False
        self._current_heading_records: set[tuple[str, str]] = set()
        self._current_hard_support_token: str | None = None
        self._current_heading_trajectories: dict[str, HeadingTrajectoryResult] = {}
        self._current_progressive_assembly: dict[str, Any] | None = None
        self._progressive_assemblies: list[dict[str, Any]] = []
        self._progress_snapshots: list[ProgressiveSnapshot] = []
        self._heading_trajectory_history: dict[int, dict[str, HeadingTrajectoryResult]] = {}
        self._physical_trajectory_history: dict[int, dict[str, Mapping[str, Any]]] = {}
        self._geometry_checkpoint_history: dict[int, dict[str, Any]] = {}
        self._aligned_pair_history: dict[int, dict[str, AlignedPair]] = {}
        self._final_raw_fresh_verification: dict[str, Any] | None = None
        self._transaction_active = False
        self._transaction_events: list[dict[str, Any]] = []

    @staticmethod
    def _validated_progressive_layout(settings: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        expected = tuple(
            {"kind": "CENTER", "edge": edge, "dimension": 6}
            for edge, _, _ in EDGE_SPECS
        ) + tuple(
            {"kind": "AXIS_PRODUCT_S2_TANGENT", "edge": edge, "dimension": 4}
            for edge in HINGE_EDGES
        )
        registered = tuple(dict(row) for row in settings["state_layout"])
        if registered != expected or int(settings["state_dimension"]) != 70:
            raise ValueError("progressive state must bind the exact normalized 54D centers plus 16D product-S2 layout")
        if sum(int(row["dimension"]) for row in registered) != int(settings["state_dimension"]):
            raise ValueError("registered progressive state layout dimension is inconsistent")
        return registered

    @property
    def stage(self) -> str:
        return self._stage.value

    def _require(self, operation: str, *allowed: PipelineStage) -> None:
        self.guard.enforce_pipeline_stage(
            actual=self._stage.value,
            allowed=[stage.value for stage in allowed],
            operation=f"C2PipelineRuntime.{operation}",
        )

    def _transition(self, target: PipelineStage, *, cause: str) -> None:
        previous = self._stage
        self._stage = target
        self._stage_events.append({
            "from": previous.value, "to": target.value, "cause": cause,
            "current_chronological_index": self._current_index,
            "current_action": self._current_action,
        })

    @staticmethod
    def _canonical_state(value: Any) -> Any:
        if isinstance(value, np.ndarray):
            array = np.ascontiguousarray(value)
            return {
                "__ndarray__": True,
                "dtype": str(array.dtype),
                "shape": list(array.shape),
                "sha256": sha256(array.tobytes()).hexdigest(),
            }
        if is_dataclass(value):
            return {
                field.name: C2PipelineRuntime._canonical_state(getattr(value, field.name))
                for field in fields(value)
            }
        if isinstance(value, Mapping):
            return {
                str(key): C2PipelineRuntime._canonical_state(item)
                for key, item in sorted(value.items(), key=lambda row: str(row[0]))
            }
        if isinstance(value, (list, tuple)):
            return [C2PipelineRuntime._canonical_state(item) for item in value]
        if isinstance(value, set):
            canonical = [C2PipelineRuntime._canonical_state(item) for item in value]
            return sorted(canonical, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        if isinstance(value, slice):
            return {
                "__slice__": {
                    "start": value.start,
                    "stop": value.stop,
                    "step": value.step,
                }
            }
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, np.generic):
            return value.item()
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        # VQF instance tokens are intentionally opaque lifecycle identities.
        # They are hashed by type and process-local identity so rollback can
        # prove the exact same capture-wide instance survived.
        return {
            "__opaque_identity__": id(value),
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
        }

    def _episode_checkpoint(self) -> dict[str, Any]:
        if self._progressive_owner is None:
            raise RuntimeError("transaction checkpoint requires the progressive owner")
        return {
            "stage": self._stage,
            "stage_events": deepcopy(self._stage_events),
            "clock": self._clock_owner.checkpoint(),
            "geometry": self._geometry_owner.checkpoint(),
            "center_prefix": self._center_prefix_owner.checkpoint(),
            "frame": self._frame_owner.checkpoint(),
            "heading_owner": self._heading_owner,
            "heading": None if self._heading_owner is None else self._heading_owner.checkpoint(),
            "progressive": self._progressive_owner.checkpoint(),
            "guard": self.guard.checkpoint(),
            "current_index": self._current_index,
            "current_action": self._current_action,
            "current_prediction": deepcopy(self._current_prediction),
            "current_reference_time_s": self._current_reference_time_s,
            "current_reference_time_audit": deepcopy(self._current_reference_time_audit),
            "current_prequential_prior_sync_audit": deepcopy(
                self._current_prequential_prior_sync_audit
            ),
            "episode_reference_time_audits": deepcopy(self._episode_reference_time_audits),
            "local_axes": deepcopy(self._local_axes),
            "local_centers": deepcopy(self._local_centers),
            "accepted_local_axes": deepcopy(self._accepted_local_axes),
            "accepted_local_centers": deepcopy(self._accepted_local_centers),
            "geometry_factor_decisions": deepcopy(self._geometry_factor_decisions),
            "current_owned_aligned_pairs": deepcopy(self._current_owned_aligned_pairs),
            "current_center_prefix_selections": deepcopy(
                self._current_center_prefix_selections
            ),
            "current_center_prefix_explicit_no_updates": deepcopy(
                self._current_center_prefix_explicit_no_updates
            ),
            "current_frame_branches": deepcopy(self._current_frame_branches),
            "current_assessments": deepcopy(self._current_assessments),
            "current_physical_trajectory_assessments": deepcopy(
                self._current_physical_trajectory_assessments
            ),
            "current_physical_world_from_segment": deepcopy(
                self._current_physical_world_from_segment
            ),
            "current_physical_orientation_covariance": deepcopy(
                self._current_physical_orientation_covariance
            ),
            "current_physical_owner_bindings": deepcopy(
                self._current_physical_owner_bindings
            ),
            "current_physical_frame_branches": deepcopy(
                self._current_physical_frame_branches
            ),
            "current_physical_soft_log_likelihood": self._current_physical_soft_log_likelihood.copy(),
            "current_physical_input_audit": deepcopy(self._current_physical_input_audit),
            "current_all_physical_candidates_invalid": self._current_all_physical_candidates_invalid,
            "current_qmt_observation_allowed": self._current_qmt_observation_allowed,
            "current_qmt_ready_edges": self._current_qmt_ready_edges,
            "current_full_frame_geometry_ready": self._current_full_frame_geometry_ready,
            "current_physical_gate_completed": self._current_physical_gate_completed,
            "branch_support": self._branch_support.copy(),
            "committed_wear_log_likelihood": self._committed_wear_log_likelihood.copy(),
            "wear_prior_initialized": self._wear_prior_initialized,
            "current_heading_records": set(self._current_heading_records),
            "current_hard_support_token": self._current_hard_support_token,
            "current_heading_trajectories": deepcopy(self._current_heading_trajectories),
            "current_progressive_assembly": deepcopy(self._current_progressive_assembly),
            "progressive_assemblies": deepcopy(self._progressive_assemblies),
            "progress_snapshots": deepcopy(self._progress_snapshots),
            "heading_trajectory_history": deepcopy(self._heading_trajectory_history),
            "physical_trajectory_history": deepcopy(self._physical_trajectory_history),
            "geometry_checkpoint_history": deepcopy(self._geometry_checkpoint_history),
            "aligned_pair_history": deepcopy(self._aligned_pair_history),
        }

    def _restore_episode_checkpoint(self, checkpoint: Mapping[str, Any]) -> None:
        assert self._progressive_owner is not None
        self._stage = checkpoint["stage"]
        self._stage_events = deepcopy(checkpoint["stage_events"])
        self._clock_owner.restore(checkpoint["clock"])
        self._geometry_owner.restore(checkpoint["geometry"])
        self._center_prefix_owner.restore(checkpoint["center_prefix"])
        self._frame_owner.restore(checkpoint["frame"])
        self._heading_owner = checkpoint["heading_owner"]
        if self._heading_owner is not None:
            self._heading_owner.restore(checkpoint["heading"])
        self._progressive_owner.restore(checkpoint["progressive"])
        self.guard.restore(checkpoint["guard"])
        self._current_index = checkpoint["current_index"]
        self._current_action = checkpoint["current_action"]
        self._current_prediction = deepcopy(checkpoint["current_prediction"])
        self._current_reference_time_s = checkpoint["current_reference_time_s"]
        self._current_reference_time_audit = deepcopy(checkpoint["current_reference_time_audit"])
        self._current_prequential_prior_sync_audit = deepcopy(
            checkpoint["current_prequential_prior_sync_audit"]
        )
        self._episode_reference_time_audits = deepcopy(checkpoint["episode_reference_time_audits"])
        self._local_axes = deepcopy(checkpoint["local_axes"])
        self._local_centers = deepcopy(checkpoint["local_centers"])
        self._accepted_local_axes = deepcopy(checkpoint["accepted_local_axes"])
        self._accepted_local_centers = deepcopy(checkpoint["accepted_local_centers"])
        self._geometry_factor_decisions = deepcopy(checkpoint["geometry_factor_decisions"])
        self._current_owned_aligned_pairs = deepcopy(checkpoint["current_owned_aligned_pairs"])
        self._current_center_prefix_selections = deepcopy(
            checkpoint["current_center_prefix_selections"]
        )
        self._current_center_prefix_explicit_no_updates = deepcopy(
            checkpoint["current_center_prefix_explicit_no_updates"]
        )
        self._current_frame_branches = deepcopy(checkpoint["current_frame_branches"])
        self._current_assessments = deepcopy(checkpoint["current_assessments"])
        self._current_physical_trajectory_assessments = deepcopy(
            checkpoint["current_physical_trajectory_assessments"]
        )
        self._current_physical_world_from_segment = deepcopy(
            checkpoint["current_physical_world_from_segment"]
        )
        self._current_physical_orientation_covariance = deepcopy(
            checkpoint["current_physical_orientation_covariance"]
        )
        self._current_physical_owner_bindings = deepcopy(
            checkpoint["current_physical_owner_bindings"]
        )
        self._current_physical_frame_branches = deepcopy(
            checkpoint["current_physical_frame_branches"]
        )
        self._current_physical_soft_log_likelihood = np.asarray(
            checkpoint["current_physical_soft_log_likelihood"], dtype=float,
        ).copy()
        self._current_physical_input_audit = deepcopy(checkpoint["current_physical_input_audit"])
        self._current_all_physical_candidates_invalid = bool(
            checkpoint["current_all_physical_candidates_invalid"]
        )
        self._current_qmt_observation_allowed = bool(
            checkpoint["current_qmt_observation_allowed"]
        )
        self._current_qmt_ready_edges = tuple(checkpoint["current_qmt_ready_edges"])
        self._current_full_frame_geometry_ready = bool(
            checkpoint["current_full_frame_geometry_ready"]
        )
        self._current_physical_gate_completed = bool(checkpoint["current_physical_gate_completed"])
        self._branch_support = np.asarray(checkpoint["branch_support"], dtype=bool).copy()
        self._committed_wear_log_likelihood = np.asarray(
            checkpoint["committed_wear_log_likelihood"], dtype=float,
        ).copy()
        self._wear_prior_initialized = bool(checkpoint["wear_prior_initialized"])
        self._current_heading_records = set(checkpoint["current_heading_records"])
        self._current_hard_support_token = checkpoint["current_hard_support_token"]
        self._current_heading_trajectories = deepcopy(checkpoint["current_heading_trajectories"])
        self._current_progressive_assembly = deepcopy(checkpoint["current_progressive_assembly"])
        self._progressive_assemblies = deepcopy(checkpoint["progressive_assemblies"])
        self._progress_snapshots = deepcopy(checkpoint["progress_snapshots"])
        self._heading_trajectory_history = deepcopy(checkpoint["heading_trajectory_history"])
        self._physical_trajectory_history = deepcopy(checkpoint["physical_trajectory_history"])
        self._geometry_checkpoint_history = deepcopy(
            checkpoint["geometry_checkpoint_history"]
        )
        self._aligned_pair_history = deepcopy(checkpoint["aligned_pair_history"])

    def _episode_state_hash(self, checkpoint: Mapping[str, Any] | None = None) -> str:
        state = self._episode_checkpoint() if checkpoint is None else checkpoint
        # Object identity is lifecycle bookkeeping, not owner state. Its own
        # checkpoint is included and hashed above.
        state = dict(state)
        state["heading_owner"] = self._heading_owner is not None if checkpoint is None else state["heading_owner"] is not None
        payload = json.dumps(self._canonical_state(state), sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()

    def _episode_checkpoint_component_hashes(
        self, checkpoint: Mapping[str, Any] | None = None,
    ) -> Mapping[str, str]:
        """Hash every top-level transaction component for mismatch diagnosis."""

        state = self._episode_checkpoint() if checkpoint is None else checkpoint
        output: dict[str, str] = {}
        for key, value in sorted(state.items()):
            canonical = (
                value is not None if key == "heading_owner"
                else self._canonical_state(value)
            )
            output[str(key)] = sha256(json.dumps(
                canonical, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
        return output

    @contextmanager
    def calibration_episode_transaction(
        self,
        chronological_index: int,
        action: str,
        *,
        physical_reference_time_s: float | None = None,
    ) -> Iterator["C2PipelineRuntime"]:
        """Rollback every mutable owner unless one complete causal prefix commits."""

        if self._transaction_active:
            raise RuntimeError("nested calibration episode transactions are forbidden")
        checkpoint = self._episode_checkpoint()
        before_hash = self._episode_state_hash(checkpoint)
        before_component_hashes = self._episode_checkpoint_component_hashes(checkpoint)
        before_snapshot_count = len(self._progress_snapshots)
        self._transaction_active = True
        try:
            # Stage validation appends a guard audit event, so it belongs inside
            # the transaction boundary.  A later ordinary failure must restore
            # that event together with every other owner; otherwise retry state
            # retains one failed-attempt event that a clean run never receives.
            self._require(
                "calibration_episode_transaction",
                PipelineStage.CALIBRATION_EPISODE_READY,
            )
            if physical_reference_time_s is not None:
                self.guard.reject_caller_time_substitution(
                    "calibration diffusion reference is derived only from sealed OrientedAction time/boot arrays"
                )
            self.begin_calibration_episode(chronological_index, action)
            yield self
            if self._stage is not PipelineStage.CALIBRATION_EPISODE_READY:
                raise RuntimeError("calibration episode transaction exited without one complete progressive commit")
            if len(self._progress_snapshots) != before_snapshot_count + 1:
                raise RuntimeError("calibration episode transaction did not commit exactly one immutable prefix")
        except BaseException as exc:
            failed_stage = self._stage.value
            physical_diagnostic_before_rollback = deepcopy({
                "input_audit": self._current_physical_input_audit,
                "assessments": {
                    branch_id: assessment.report
                    for branch_id, assessment in self._current_physical_trajectory_assessments.items()
                },
                "all_physical_candidates_invalid": self._current_all_physical_candidates_invalid,
                "qmt_observation_allowed": self._current_qmt_observation_allowed,
                "physical_gate_completed": self._current_physical_gate_completed,
            })
            self._restore_episode_checkpoint(checkpoint)
            after_hash = self._episode_state_hash()
            after_component_hashes = self._episode_checkpoint_component_hashes()
            mismatched_components = sorted(
                key for key in before_component_hashes
                if before_component_hashes[key] != after_component_hashes.get(key)
            )
            if after_hash != before_hash:
                self._transaction_events.append({
                    "status": "ROLLBACK_MISMATCH_CLASS_A_STOP",
                    "chronological_index": int(chronological_index),
                    "action": str(action),
                    "failure_stage": failed_stage,
                    "original_exception_type": type(exc).__name__,
                    "original_exception_message": str(exc),
                    "owner_state_hash_before": before_hash,
                    "owner_state_hash_after_rollback": after_hash,
                    "owner_state_hashes_equal": False,
                    "top_level_component_hashes_before": before_component_hashes,
                    "top_level_component_hashes_after": after_component_hashes,
                    "mismatched_top_level_components": mismatched_components,
                    "mismatch_guard_bypassed": False,
                })
                raise RuntimeError(
                    "transaction rollback mismatch after "
                    f"{type(exc).__name__}: {exc}; mismatched components="
                    f"{mismatched_components}"
                ) from exc
            self._transaction_events.append({
                "status": "ROLLED_BACK",
                "chronological_index": int(chronological_index),
                "action": str(action),
                "failure_stage": failed_stage,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "owner_state_hash_before": before_hash,
                "owner_state_hash_after_rollback": after_hash,
                "owner_state_hashes_equal": True,
                "top_level_component_hashes_before": before_component_hashes,
                "top_level_component_hashes_after": after_component_hashes,
                "mismatched_top_level_components": mismatched_components,
                "physical_candidate_diagnostic_before_rollback": physical_diagnostic_before_rollback,
            })
            raise
        else:
            self._transaction_events.append({
                "status": "COMMITTED_ONCE",
                "chronological_index": int(chronological_index),
                "action": str(action),
                "prefix_snapshot_count_before": before_snapshot_count,
                "prefix_snapshot_count_after": len(self._progress_snapshots),
            })
        finally:
            self._transaction_active = False

    def ingest_orientation_episode(self, action: DecodedAction) -> OrientedAction:
        self._require("ingest_orientation_episode", PipelineStage.ORIENTATION)
        oriented = self._orientation_owner.process(action)
        self._oriented_actions.append(oriented)
        self._input_access_audits.append(deepcopy(dict(action.access_audit)))
        return oriented

    def finish_orientation_and_begin_calibration(self) -> None:
        self._require("finish_orientation_and_begin_calibration", PipelineStage.ORIENTATION)
        expected = tuple(self.settings["execution_contract"]["chronological_actions"])
        if tuple(row.action for row in self._oriented_actions) != expected:
            raise RuntimeError("causal calibration requires all exact 19 capture-wide orientation episodes")
        branch_count = int(self.settings["progressive"]["real_branch_count"])
        self._progressive_owner = ProgressiveCalibrationState(
            self._progressive_dimension,
            execution_guard=self.guard,
            initial_sigma=float(self.settings["progressive"]["initial_sigma"]),
            branch_count=branch_count,
            chronological_actions=expected,
            rank_relative_tolerance=float(self.settings["progressive"]["rank_relative_tolerance"]),
            fresh_absolute_tolerance=float(self.settings["progressive"]["fresh_absolute_tolerance"]),
            fresh_relative_tolerance=float(self.settings["progressive"]["fresh_relative_tolerance"]),
            branch_ids=self._branch_ids,
            authoritative_sync_owner_id=self._runtime_owner_id,
        )
        self._transition(
            PipelineStage.CALIBRATION_EPISODE_READY,
            cause="CONTINUOUS_ORIENTATION_COMPLETE;CALIBRATION_STATE_EMPTY_NO_GEOMETRY_OR_HEADING_FIT",
        )

    def _pelvis_root_hardware_node(self) -> str:
        pelvis_rows = [
            row for row in self.settings["segment_frames"]["wear_authority"]["rows"]
            if row["body_segment"] == "pelvis"
        ]
        if len(pelvis_rows) != 1:
            raise ValueError("sealed wear/identity mapping must identify exactly one pelvis root")
        return str(pelvis_rows[0]["hardware_id"])

    def _derive_current_geometry_reference(self, chronological_index: int) -> Mapping[str, Any]:
        """Own the geometry diffusion coordinate without fabricating reset time.

        The raw timer midpoint is used only when both adjacent actions have a
        single identical derived boot epoch.  Otherwise the monotone internal
        coordinate is carried unchanged and the geometry owner applies its
        registered unknown-interval covariance floor.
        """

        root_node = self._pelvis_root_hardware_node()

        def anchor(index: int) -> Mapping[str, Any]:
            oriented = self._oriented_actions[index]
            timer = np.asarray(oriented.time_us_by_node[root_node], dtype=np.int64)
            boot = np.asarray(oriented.derived_boot_epoch_by_node[root_node], dtype=np.int64)
            if timer.shape != boot.shape or timer.ndim != 1:
                raise ValueError("sealed pelvis time and derived-boot arrays differ")
            unique_boot = np.unique(boot)
            usable = bool(len(timer) > 0 and len(unique_boot) == 1)
            midpoint_index = len(timer) // 2 if usable else None
            return {
                "timer": timer,
                "boot": boot,
                "usable": usable,
                "midpoint_index": midpoint_index,
                "timer_us": None if midpoint_index is None else int(timer[midpoint_index]),
                "derived_boot_epoch": None if midpoint_index is None else int(boot[midpoint_index]),
                "unique_derived_boot_epochs": unique_boot.tolist(),
            }

        current = anchor(chronological_index)
        previous = None if chronological_index == 0 else anchor(chronological_index - 1)
        prior_coordinate = 0.0 if not self._episode_reference_time_audits else float(
            self._episode_reference_time_audits[-1]["geometry_diffusion_coordinate_s"]
        )
        if chronological_index == 0 and current["usable"]:
            elapsed_status = "EXACT_SAME_DERIVED_BOOT_EPOCH"
            elapsed_s = 0.0
        elif (
            previous is not None
            and previous["usable"]
            and current["usable"]
            and previous["derived_boot_epoch"] == current["derived_boot_epoch"]
            and int(current["timer_us"]) > int(previous["timer_us"])
        ):
            elapsed_status = "EXACT_SAME_DERIVED_BOOT_EPOCH"
            elapsed_s = (int(current["timer_us"]) - int(previous["timer_us"])) * 1e-6
        elif not current["usable"]:
            elapsed_status = "UNKNOWN_CURRENT_ACTION_EMPTY_OR_WITHIN_ACTION_BOOT_TRANSITION"
            elapsed_s = None
        elif previous is None or not previous["usable"]:
            elapsed_status = "UNKNOWN_PREVIOUS_ACTION_EMPTY_OR_BOOT_TRANSITION"
            elapsed_s = None
        elif previous["derived_boot_epoch"] != current["derived_boot_epoch"]:
            elapsed_status = "UNKNOWN_INTER_ACTION_DERIVED_BOOT_TRANSITION_OR_TIMER_RESET"
            elapsed_s = None
        else:
            elapsed_status = "UNKNOWN_NONINCREASING_TIMER_WITHIN_SAME_DERIVED_BOOT_LABEL"
            elapsed_s = None
        coordinate = prior_coordinate + (0.0 if elapsed_s is None else float(elapsed_s))
        return {
            "schema": "biospur-c2-owner-derived-geometry-diffusion-reference-v1",
            "chronological_index": int(chronological_index),
            "action": self._oriented_actions[chronological_index].action,
            "root_hardware_id": root_node,
            "selection_rule": "RESULT_INDEPENDENT_MIDDLE_OBSERVED_PELVIS_ROW_IF_SINGLE_BOOT_EPOCH",
            "current_time_us_sha256": self._array_sha256(current["timer"]),
            "current_derived_boot_epoch_sha256": self._array_sha256(current["boot"]),
            "current_timer_us": current["timer_us"],
            "current_derived_boot_epoch": current["derived_boot_epoch"],
            "current_unique_derived_boot_epochs": current["unique_derived_boot_epochs"],
            "previous_timer_us": None if previous is None else previous["timer_us"],
            "previous_derived_boot_epoch": (
                None if previous is None else previous["derived_boot_epoch"]
            ),
            "elapsed_time_status": elapsed_status,
            "exact_elapsed_s": elapsed_s,
            "geometry_diffusion_coordinate_s": coordinate,
            "unknown_duration_fabricated_as_exact_elapsed": False,
            "caller_reference_time_consumed": False,
        }

    def _geometry_authoritative_state(self) -> Mapping[str, np.ndarray]:
        """Map the geometry owner into the registered normalized state layout."""

        dimension = self._progressive_dimension
        mean = np.zeros(dimension, dtype=float)
        measurement = np.zeros((dimension, dimension), dtype=float)
        migration = np.zeros((dimension, dimension), dtype=float)
        systematic = np.zeros((dimension, dimension), dtype=float)
        centers = self._geometry_owner.posterior_centers()
        axes = self._geometry_owner.posterior_axes()
        center_scale = float(
            self.settings["progressive"]["normalization"]["center_coordinate_scale_m"]
        )
        axis_scale = float(
            self.settings["progressive"]["normalization"]["axis_tangent_scale_rad"]
        )
        initial_variance = float(self.settings["progressive"]["initial_sigma"]) ** 2
        offset = 0
        for row in self._progressive_layout:
            width = int(row["dimension"])
            block = slice(offset, offset + width)
            edge = str(row["edge"])
            if row["kind"] == "CENTER" and edge in centers:
                posterior = centers[edge]
                mean[block] = np.r_[
                    posterior.joint_to_parent_sensor_m,
                    posterior.joint_to_child_sensor_m,
                ] / center_scale
                measurement[block, block] = np.asarray(
                    posterior.report["measurement_statistical_covariance_m2"], dtype=float,
                ) / center_scale**2
                migration[block, block] = np.asarray(
                    posterior.report["temporal_migration_covariance_m2"], dtype=float,
                ) / center_scale**2
                systematic[block, block] = np.asarray(
                    posterior.report["systematic_shared_model_covariance_m2"], dtype=float,
                ) / center_scale**2
            elif row["kind"] == "AXIS_PRODUCT_S2_TANGENT" and edge in axes:
                value, measurement_block, migration_block, systematic_block, _ = (
                    self._geometry_owner.posterior_axis_in_fixed_gauge(edge)
                )
                mean[block] = value / axis_scale
                measurement[block, block] = measurement_block / axis_scale**2
                migration[block, block] = migration_block / axis_scale**2
                systematic[block, block] = systematic_block / axis_scale**2
            else:
                measurement[block, block] = np.eye(width) * initial_variance
            offset += width
        return {
            "mean": mean,
            "measurement": measurement,
            "migration": migration,
            "systematic": systematic,
        }

    def _synchronize_prequential_prior_from_geometry(self) -> Mapping[str, Any]:
        if self._progressive_owner is None or self._current_index is None or self._current_action is None:
            raise RuntimeError("geometry-to-progressive prior sync requires a current episode")
        state = self._geometry_authoritative_state()
        hashes = {
            name: ProgressiveCalibrationState._array_sha256(value)
            for name, value in state.items()
        }
        payload = {
            "owner_id": self._runtime_owner_id,
            "chronological_index": int(self._current_index),
            "action": self._current_action,
            "array_sha256": hashes,
        }
        token = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return self._progressive_owner.synchronize_authoritative_prior_from_geometry(
            chronological_index=self._current_index,
            action=self._current_action,
            authoritative_mean=state["mean"],
            measurement_covariance=state["measurement"],
            temporal_migration_covariance=state["migration"],
            shared_systematic_covariance=state["systematic"],
            owner_binding={**payload, "owner_token": token},
        )

    def _validate_prequential_time_advance(
        self,
        *,
        reference: Mapping[str, Any],
        total_covariance_before: np.ndarray,
        total_covariance_after: np.ndarray,
        data_information_before: np.ndarray,
        data_information_after: np.ndarray,
        geometry_state_present: bool,
    ) -> Mapping[str, Any]:
        """Bind the time advance to the prediction boundary, not post-ingest."""

        before_covariance = np.asarray(total_covariance_before, dtype=float)
        after_covariance = np.asarray(total_covariance_after, dtype=float)
        before_information = np.asarray(data_information_before, dtype=float)
        after_information = np.asarray(data_information_after, dtype=float)
        if before_covariance.shape != after_covariance.shape or before_covariance.ndim != 2:
            raise ValueError("prequential time-advance covariance shapes differ")
        if before_information.shape != after_information.shape or before_information.ndim != 2:
            raise ValueError("prequential time-advance information shapes differ")
        covariance_delta = 0.5 * (
            after_covariance - before_covariance
            + (after_covariance - before_covariance).T
        )
        covariance_delta_eigenvalues = np.linalg.eigvalsh(covariance_delta)
        covariance_delta_psd = bool(float(np.min(covariance_delta_eigenvalues)) >= -1e-12)
        covariance_trace_increment = float(np.trace(covariance_delta))
        exact_elapsed = reference["exact_elapsed_s"]
        widening_required = bool(
            geometry_state_present
            and (
                str(reference["elapsed_time_status"]).startswith("UNKNOWN_")
                or (exact_elapsed is not None and float(exact_elapsed) > 0.0)
            )
        )
        widened = bool(covariance_delta_psd and covariance_trace_increment > 0.0)
        information_equal = bool(np.array_equal(before_information, after_information))

        def information_rank(value: np.ndarray) -> int:
            eigenvalues = np.maximum(np.linalg.eigvalsh(0.5 * (value + value.T)), 0.0)
            maximum = float(np.max(eigenvalues)) if len(eigenvalues) else 0.0
            if maximum == 0.0:
                return 0
            tolerance = maximum * float(
                self.settings["progressive"]["rank_relative_tolerance"]
            )
            return int(np.count_nonzero(eigenvalues >= tolerance))

        rank_before = information_rank(before_information)
        rank_after = information_rank(after_information)
        rank_equal = rank_before == rank_after
        self.guard.validate_prequential_time_advance(
            elapsed_time_status=str(reference["elapsed_time_status"]),
            widening_required=widening_required,
            total_covariance_widened=widened,
            data_information_exactly_unchanged=information_equal,
            data_information_rank_exactly_unchanged=rank_equal,
            exact_elapsed_s=(None if exact_elapsed is None else float(exact_elapsed)),
        )
        return {
            "schema": "biospur-c2-prequential-time-advance-gate-v1",
            "elapsed_time_status": str(reference["elapsed_time_status"]),
            "exact_elapsed_s": None if exact_elapsed is None else float(exact_elapsed),
            "geometry_state_present_before_current_episode": bool(geometry_state_present),
            "widening_required": widening_required,
            "total_covariance_trace_before": float(np.trace(before_covariance)),
            "total_covariance_trace_after": float(np.trace(after_covariance)),
            "total_covariance_trace_increment": covariance_trace_increment,
            "total_covariance_delta_psd": covariance_delta_psd,
            "total_covariance_widened_before_score": widened,
            "data_information_before_sha256": self._array_sha256(before_information),
            "data_information_after_sha256": self._array_sha256(after_information),
            "data_information_matrix_exactly_unchanged": information_equal,
            "data_information_rank_before": rank_before,
            "data_information_rank_after": rank_after,
            "data_information_rank_exactly_unchanged": rank_equal,
            "current_episode_factor_consumed": False,
            "prediction_scored_yet": False,
        }

    def begin_calibration_episode(
        self,
        chronological_index: int,
        action: str,
    ) -> None:
        self._require("begin_calibration_episode", PipelineStage.CALIBRATION_EPISODE_READY)
        if not self._transaction_active:
            raise RuntimeError("calibration episodes must be opened by calibration_episode_transaction")
        expected_index = len(self._progress_snapshots)
        chronology = tuple(self.settings["execution_contract"]["chronological_actions"])
        if chronological_index != expected_index or action != chronology[chronological_index]:
            raise ValueError("calibration episode differs from exact next sealed action")
        self._current_index = int(chronological_index)
        self._current_action = str(action)
        reference = self._derive_current_geometry_reference(chronological_index)
        self._current_reference_time_s = float(reference["geometry_diffusion_coordinate_s"])
        self._current_reference_time_audit = dict(reference)
        geometry_before = self._geometry_authoritative_state()
        total_covariance_before = (
            geometry_before["measurement"]
            + geometry_before["migration"]
            + geometry_before["systematic"]
        )
        data_information_before, owner_rank_before = (
            self._progressive_owner.data_information_state()
        )
        geometry_state_present = bool(
            self._geometry_owner.posterior_centers()
            or self._geometry_owner.posterior_axes()
        )
        if reference["elapsed_time_status"] == "EXACT_SAME_DERIVED_BOOT_EPOCH":
            advance_event = self._geometry_owner.advance_to_reference(
                chronological_index=chronological_index,
                action=action,
                reference_time_s=self._current_reference_time_s,
            )
            self._current_reference_time_audit["exact_advance_event"] = dict(advance_event)
        else:
            floor_event = self._geometry_owner.apply_unknown_interval_floor(
                chronological_index=chronological_index,
                action=action,
                cause=str(reference["elapsed_time_status"]),
            )
            self._current_reference_time_audit["conservative_floor_event"] = dict(floor_event)
        self._current_prequential_prior_sync_audit = dict(
            self._synchronize_prequential_prior_from_geometry()
        )
        geometry_after = self._geometry_authoritative_state()
        total_covariance_after = (
            geometry_after["measurement"]
            + geometry_after["migration"]
            + geometry_after["systematic"]
        )
        data_information_after, owner_rank_after = (
            self._progressive_owner.data_information_state()
        )
        if owner_rank_before != owner_rank_after:
            self.guard.validate_prequential_time_advance(
                elapsed_time_status=str(reference["elapsed_time_status"]),
                widening_required=True,
                total_covariance_widened=False,
                data_information_exactly_unchanged=False,
                data_information_rank_exactly_unchanged=False,
                exact_elapsed_s=reference["exact_elapsed_s"],
            )
        self._current_reference_time_audit["pre_score_time_advance_gate"] = dict(
            self._validate_prequential_time_advance(
                reference=reference,
                total_covariance_before=total_covariance_before,
                total_covariance_after=total_covariance_after,
                data_information_before=data_information_before,
                data_information_after=data_information_after,
                geometry_state_present=geometry_state_present,
            )
        )
        self._current_reference_time_audit["prequential_prior_sync"] = deepcopy(
            self._current_prequential_prior_sync_audit
        )
        self._current_prediction = None
        self._local_axes = {}
        self._local_centers = {}
        self._accepted_local_axes = {}
        self._accepted_local_centers = {}
        self._geometry_factor_decisions = {}
        self._current_owned_aligned_pairs = {}
        self._current_center_prefix_selections = {}
        self._current_center_prefix_explicit_no_updates = {}
        self._current_heading_records = set()
        self._current_hard_support_token = None
        self._current_heading_trajectories = {}
        self._current_frame_branches = ()
        self._current_assessments = {}
        self._current_physical_trajectory_assessments = {}
        self._current_physical_world_from_segment = {}
        self._current_physical_orientation_covariance = {}
        self._current_physical_owner_bindings = {}
        self._current_physical_frame_branches = {}
        self._current_physical_soft_log_likelihood = np.zeros(len(self._branch_ids), dtype=float)
        self._current_physical_input_audit = {}
        self._current_all_physical_candidates_invalid = False
        self._current_qmt_observation_allowed = False
        self._current_qmt_ready_edges = ()
        self._current_full_frame_geometry_ready = False
        self._current_physical_gate_completed = False
        self._current_progressive_assembly = None
        self._transition(PipelineStage.AWAITING_PREQUENTIAL_SCORE, cause="OPEN_CAUSAL_EPISODE_TRANSACTION")

    def score_current_prequential(
        self,
    ) -> PrequentialPrediction:
        self._require("score_current_prequential", PipelineStage.AWAITING_PREQUENTIAL_SCORE)
        if self._progressive_owner is None or self._current_index is None or self._current_action is None:
            raise RuntimeError("calibration episode is not initialized")
        self._current_prediction = self._progressive_owner.score_episode_before_ingest(
            chronological_index=self._current_index,
            action=self._current_action,
        )
        # The prediction above is frozen from prefix i-1.  Only now may the
        # result-independent timer grids of episode i update the capture-wide
        # node clock posterior used by its local factors.
        oriented = self._current_oriented()
        self._current_reference_time_audit["capture_wide_node_clock_update"] = dict(
            self._clock_owner.observe_episode_node_grids(
                action=self._current_action,
                chronological_index=self._current_index,
                root_node=self._pelvis_root_hardware_node(),
                time_us_by_node=oriented.time_us_by_node,
                boot_epoch_by_node=oriented.derived_boot_epoch_by_node,
                contiguous_span_id_by_node=oriented.contiguous_span_id_by_node,
            )
        )
        self._transition(
            PipelineStage.CURRENT_EPISODE_LOCAL_FACTORS,
            cause="PREDICTION_SCORED_FROM_PREFIX_I_MINUS_1_BEFORE_ANY_EPISODE_I_FACTOR_UPDATE",
        )
        return self._current_prediction

    def _current_oriented(self) -> OrientedAction:
        if self._current_index is None:
            raise RuntimeError("no causal episode transaction")
        return self._oriented_actions[self._current_index]

    @staticmethod
    def _array_sha256(value: np.ndarray) -> str:
        array = np.ascontiguousarray(np.asarray(value))
        return sha256(array.view(np.uint8)).hexdigest()

    def _oriented_for_owned_pair(
        self,
        pair: AlignedPair,
        *,
        allow_historical_center_prefix: bool,
    ) -> OrientedAction:
        if self._current_index is None:
            raise RuntimeError("no causal episode transaction")
        pair_index = int(pair.provenance.get("chronological_index", -1))
        if pair_index < 0 or pair_index >= len(self._oriented_actions):
            if allow_historical_center_prefix:
                self.guard.reject_center_prefix_future_or_heldout(
                    "center prefix pair index leaves oriented training episodes"
                )
            raise ValueError("aligned pair index leaves oriented training episodes")
        if pair_index > self._current_index:
            if allow_historical_center_prefix:
                self.guard.reject_center_prefix_future_or_heldout(
                    "future oriented action reached the causal center prefix"
                )
            raise ValueError("aligned pair was copied from a future episode")
        if pair_index < self._current_index:
            if (
                not allow_historical_center_prefix
                or not self._center_prefix_owner.owns_historical_pair(pair)
            ):
                self.guard.reject_center_prefix_nuisance_bypass_or_reingestion(
                    "historical aligned pair is not owned by the current causal center prefix"
                )
        return self._oriented_actions[pair_index]

    def _aligned_pair_binding_payload(
        self,
        pair: AlignedPair,
        *,
        allow_historical_center_prefix: bool = False,
    ) -> dict[str, Any]:
        oriented = self._oriented_for_owned_pair(
            pair,
            allow_historical_center_prefix=allow_historical_center_prefix,
        )
        provenance = dict(pair.provenance)
        parent_node = str(provenance["parent_node"])
        child_node = str(provenance["child_node"])
        parent_indices = np.asarray(pair.alignment.parent_indices, dtype=np.int64)
        child_indices = np.asarray(pair.alignment.child_indices, dtype=np.int64)
        if pair.action != oriented.action or provenance.get("action") != oriented.action:
            raise ValueError("aligned pair was relabeled or copied from another action")
        if provenance.get("chronological_index") != oriented.chronological_index:
            raise ValueError("aligned pair was copied from a future/past episode")
        if parent_node not in oriented.time_us_by_node or child_node not in oriented.time_us_by_node:
            raise ValueError("aligned pair node identity is absent from current sealed OrientedAction")
        if np.any(parent_indices < 0) or np.any(parent_indices >= len(oriented.time_us_by_node[parent_node])):
            raise ValueError("aligned pair parent source indices leave current OrientedAction")
        if np.any(child_indices < 0) or np.any(child_indices >= len(oriented.time_us_by_node[child_node])):
            raise ValueError("aligned pair child source indices leave current OrientedAction")
        expected_parent_time_s = np.asarray(
            oriented.time_us_by_node[parent_node][parent_indices], dtype=float,
        ) * 1e-6
        expected_child_time_s = np.asarray(
            oriented.time_us_by_node[child_node][child_indices], dtype=float,
        ) * 1e-6
        expected_parent_boot = np.asarray(
            oriented.derived_boot_epoch_by_node[parent_node][parent_indices],
            dtype=np.int64,
        )
        expected_child_boot = np.asarray(
            oriented.derived_boot_epoch_by_node[child_node][child_indices],
            dtype=np.int64,
        )
        if (
            not np.array_equal(
                np.asarray(pair.parent_observed_time_s, dtype=float),
                expected_parent_time_s,
            )
            or not np.array_equal(
                np.asarray(pair.child_observed_time_s, dtype=float),
                expected_child_time_s,
            )
        ):
            self.guard.reject_caller_aligned_physical_time_substitution(
                "aligned pair observed physical time was substituted"
            )
        if (
            not np.array_equal(
                np.asarray(pair.parent_boot_epoch, dtype=np.int64),
                expected_parent_boot,
            )
            or not np.array_equal(
                np.asarray(pair.child_boot_epoch, dtype=np.int64),
                expected_child_boot,
            )
        ):
            self.guard.reject_caller_aligned_boot_epoch_substitution(
                "aligned pair boot epoch was substituted"
            )
        payload = {
            "schema": "biospur-c2-runtime-owned-aligned-pair-binding-v1",
            "runtime_owner_id": self._runtime_owner_id,
            "chronological_index": int(oriented.chronological_index),
            "action": oriented.action,
            "edge": pair.edge,
            "parent_node": parent_node,
            "child_node": child_node,
            "parent_source_indices_sha256": self._array_sha256(parent_indices),
            "child_source_indices_sha256": self._array_sha256(child_indices),
            "parent_time_us_sha256": self._array_sha256(oriented.time_us_by_node[parent_node][parent_indices]),
            "child_time_us_sha256": self._array_sha256(oriented.time_us_by_node[child_node][child_indices]),
            "parent_observed_time_s_sha256": self._array_sha256(
                expected_parent_time_s
            ),
            "child_observed_time_s_sha256": self._array_sha256(
                expected_child_time_s
            ),
            "parent_boot_epoch_sha256": self._array_sha256(
                expected_parent_boot
            ),
            "child_boot_epoch_sha256": self._array_sha256(
                expected_child_boot
            ),
            "parent_quaternion_wxyz_sha256": self._array_sha256(
                oriented.quat_world_sensor_wxyz_by_node[parent_node][parent_indices]
            ),
            "child_quaternion_wxyz_sha256": self._array_sha256(
                oriented.quat_world_sensor_wxyz_by_node[child_node][child_indices]
            ),
            "parent_calibration_posterior_sha256": (
                oriented.calibration_posterior_by_node[parent_node]["semantic_sha256"]
            ),
            "child_calibration_posterior_sha256": (
                oriented.calibration_posterior_by_node[child_node]["semantic_sha256"]
            ),
            "parent_acc_sha256": self._array_sha256(pair.parent_acc),
            "child_acc_sha256": self._array_sha256(pair.child_acc),
            "parent_gyro_sha256": self._array_sha256(pair.parent_gyro),
            "child_gyro_sha256": self._array_sha256(pair.child_gyro),
            "contiguous_span_half_open": [[span.start, span.stop] for span in pair.contiguous_spans],
        }
        for key in (
            "parent_source_indices_sha256", "child_source_indices_sha256",
            "parent_time_us_sha256", "child_time_us_sha256",
            "parent_quaternion_wxyz_sha256", "child_quaternion_wxyz_sha256",
            "parent_calibration_posterior_sha256",
            "child_calibration_posterior_sha256",
        ):
            if provenance.get(key) != payload[key]:
                raise ValueError(f"aligned pair provenance hash mismatch: {key}")
        return payload

    def _validate_owned_aligned_pair(
        self,
        pair: AlignedPair,
        *,
        expected_edge: str,
        allow_historical_center_prefix: bool = False,
    ) -> Mapping[str, Any]:
        provenance = dict(pair.provenance)
        expected_parent_node, expected_child_node = self._sealed_edge_hardware_nodes(expected_edge)
        if (
            provenance.get("parent_node") != expected_parent_node
            or provenance.get("child_node") != expected_child_node
        ):
            self.guard.reject_wrong_node_mapping(
                f"{expected_edge}: expected {expected_parent_node}->{expected_child_node}, "
                f"observed {provenance.get('parent_node')}->{provenance.get('child_node')}"
            )
        token = str(provenance.get("runtime_owner_token", ""))
        if not token or provenance.get("runtime_owner_id") != self._runtime_owner_id:
            self.guard.reject_caller_heading_array_substitution(
                "aligned pair lacks this runtime's owner token"
            )
        owned_as_current = self._current_owned_aligned_pairs.get(token) is pair
        owned_as_historical = bool(
            allow_historical_center_prefix
            and self._center_prefix_owner.owns_historical_pair(pair)
        )
        if not owned_as_current and not owned_as_historical:
            self.guard.reject_caller_heading_array_substitution(
                "copied, relabeled, future, or caller-constructed aligned pair rejected"
            )
        payload = self._aligned_pair_binding_payload(
            pair,
            allow_historical_center_prefix=allow_historical_center_prefix,
        )
        expected_token = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if token != expected_token or pair.edge != expected_edge:
            self.guard.reject_caller_heading_array_substitution(
                "aligned pair runtime token, arrays, or edge binding changed"
            )
        return payload

    def _owned_pair_stochastic_covariances(
        self,
        pair: AlignedPair,
        *,
        expected_edge: str,
        allow_historical_center_prefix: bool = False,
    ) -> tuple[
        np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray,
        Mapping[str, Any],
    ]:
        payload = self._validate_owned_aligned_pair(
            pair,
            expected_edge=expected_edge,
            allow_historical_center_prefix=allow_historical_center_prefix,
        )
        current_initial_hash = sha256(
            json.dumps(
                self._canonical_state(self._initial_stochastic_state),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if current_initial_hash != self._initial_stochastic_state_semantic_sha256:
            self.guard.reject_caller_covariance_substitution(
                "immutable P1 stochastic state changed after runtime construction"
            )
        parent_node = str(payload["parent_node"])
        child_node = str(payload["child_node"])
        nodes = self._initial_stochastic_state["nodes"]
        if parent_node not in nodes or child_node not in nodes:
            raise ValueError("owned pair node lacks immutable P1 stochastic state")
        parent_acc = np.asarray(nodes[parent_node]["accelerometer_observation_covariance_m2_s4"], dtype=float)
        child_acc = np.asarray(nodes[child_node]["accelerometer_observation_covariance_m2_s4"], dtype=float)
        parent_gyro = np.asarray(nodes[parent_node]["gyro_observation_covariance_rad2_s2"], dtype=float)
        child_gyro = np.asarray(nodes[child_node]["gyro_observation_covariance_rad2_s2"], dtype=float)
        parent_gyro_bias = np.asarray(
            nodes[parent_node]["gyro_bias_covariance_rad2_s2"], dtype=float,
        )
        child_gyro_bias = np.asarray(
            nodes[child_node]["gyro_bias_covariance_rad2_s2"], dtype=float,
        )
        accelerometer_calibration_nuisance = {
            key: self.settings["joint_center"][key]
            for key in (
                "accelerometer_unresolved_bias_sigma_mps2",
                "accelerometer_bias_drift_rate_sigma_mps3",
                "accelerometer_bias_drift_horizon_s",
                "accelerometer_scale_cross_axis_fraction_sigma",
                "accelerometer_gyro_shared_scale_cross_axis_fraction_sigma",
                "accelerometer_calibration_covariance_sensitivity_multipliers",
                "accelerometer_bias_or_gravity_estimation_policy",
                "accelerometer_calibration_nuisance_provenance",
            )
        }
        accelerometer_calibration_nuisance_sha256 = sha256(
            json.dumps(
                self._canonical_state(accelerometer_calibration_nuisance),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        axis_calibration_nuisance = {
            key: self.settings["hinge_axis"][key]
            for key in (
                "accelerometer_unresolved_bias_sigma_mps2",
                "accelerometer_bias_drift_rate_sigma_mps3",
                "accelerometer_bias_drift_horizon_s",
                "accelerometer_scale_cross_axis_fraction_sigma",
                "gyro_scale_cross_axis_fraction_sigma",
                "accelerometer_gyro_shared_scale_cross_axis_fraction_sigma",
                "gyro_bias_drift_correlation_time_s",
                "calibration_nuisance_covariance_multiplier",
                "accelerometer_calibration_nuisance_multiplier",
                "gyro_calibration_nuisance_multiplier",
                "calibration_nuisance_covariance_sensitivity_multipliers",
                "centered_selection_transform",
                "fixed_point_systematic_push_forward",
                "observation_covariance_push_forward",
            )
        }
        for key in (
            "accelerometer_unresolved_bias_sigma_mps2",
            "accelerometer_bias_drift_rate_sigma_mps3",
            "accelerometer_bias_drift_horizon_s",
            "accelerometer_scale_cross_axis_fraction_sigma",
            "gyro_scale_cross_axis_fraction_sigma",
            "accelerometer_gyro_shared_scale_cross_axis_fraction_sigma",
            "gyro_bias_drift_correlation_time_s",
        ):
            if float(axis_calibration_nuisance[key]) != float(
                self.settings["joint_center"][key]
            ):
                raise ValueError("axis and center calibration nuisance authority diverged")
        axis_calibration_nuisance_sha256 = sha256(
            json.dumps(
                self._canonical_state(axis_calibration_nuisance),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        values = (
            parent_acc, child_acc, parent_gyro, child_gyro,
            parent_gyro_bias, child_gyro_bias,
        )
        for label, value in zip(
            (
                "parent_acc", "child_acc", "parent_gyro", "child_gyro",
                "parent_gyro_bias", "child_gyro_bias",
            ),
            values,
        ):
            if value.shape != (3, 3) or not np.isfinite(value).all() or not np.allclose(value, value.T, atol=1e-12):
                raise ValueError(f"{label} immutable P1 covariance must be one finite symmetric 3x3 matrix")
            if float(np.min(np.linalg.eigvalsh(value))) < -1e-12:
                raise ValueError(f"{label} immutable P1 covariance is not positive semidefinite")
        binding = {
            "schema": "biospur-c2-runtime-owned-p1-stochastic-covariance-binding-v1",
            "runtime_owner_token": pair.provenance["runtime_owner_token"],
            "initial_stochastic_state_semantic_sha256": self._initial_stochastic_state_semantic_sha256,
            "parent_node": parent_node,
            "child_node": child_node,
            "parent_accelerometer_covariance_sha256": self._array_sha256(parent_acc),
            "child_accelerometer_covariance_sha256": self._array_sha256(child_acc),
            "parent_gyro_covariance_sha256": self._array_sha256(parent_gyro),
            "child_gyro_covariance_sha256": self._array_sha256(child_gyro),
            "parent_gyro_bias_covariance_sha256": self._array_sha256(
                parent_gyro_bias
            ),
            "child_gyro_bias_covariance_sha256": self._array_sha256(
                child_gyro_bias
            ),
            "accelerometer_calibration_nuisance_semantic_sha256": (
                accelerometer_calibration_nuisance_sha256
            ),
            "accelerometer_calibration_nuisance": (
                accelerometer_calibration_nuisance
            ),
            "axis_calibration_nuisance_semantic_sha256": (
                axis_calibration_nuisance_sha256
            ),
            "axis_calibration_nuisance": axis_calibration_nuisance,
            "p1_initial_stochastic_state_accelerometer_bias_point_estimated": False,
            "capture_wide_gravity_norm_informed_calibration_posterior_active": True,
            "gravity_vector_or_magnitude_point_estimated": False,
            "per_action_accelerometer_calibration_profile_allowed": False,
            "parent_gyro_quantization_variance_rad2_s2": float(
                nodes[parent_node]["gyro_quantization_variance_rad2_s2"]
            ),
            "child_gyro_quantization_variance_rad2_s2": float(
                nodes[child_node]["gyro_quantization_variance_rad2_s2"]
            ),
            "quantization_already_in_gyro_observation_covariance": True,
            "parent_accelerometer_quantization_variance_m2_s4": float(
                nodes[parent_node]["accelerometer_quantization_variance_m2_s4"]
            ),
            "child_accelerometer_quantization_variance_m2_s4": float(
                nodes[child_node]["accelerometer_quantization_variance_m2_s4"]
            ),
            "quantization_already_in_accelerometer_observation_covariance": True,
            "parent_initial_still_effective_rows": float(
                nodes[parent_node]["effective_rows"]
            ),
            "child_initial_still_effective_rows": float(
                nodes[child_node]["effective_rows"]
            ),
            "accelerometer_covariance_units": "m^2 s^-4",
            "gyroscope_covariance_units": "rad^2 s^-2",
            "caller_covariance_override_allowed": False,
        }
        return (
            parent_acc, child_acc, parent_gyro, child_gyro,
            parent_gyro_bias, child_gyro_bias, binding,
        )

    def _sealed_edge_hardware_nodes(self, edge: str) -> tuple[str, str]:
        endpoints = {name: (parent, child) for name, parent, child in EDGE_SPECS}
        if edge not in endpoints:
            raise ValueError("aligned pair edge is outside the official nine-edge rooted tree")
        parent, child = endpoints[edge]
        return (
            self._frame_owner.hardware_node_for_segment(parent),
            self._frame_owner.hardware_node_for_segment(child),
        )

    def align_current_pair(self, *, edge: str) -> AlignedPair:
        self._require("align_current_pair", PipelineStage.CURRENT_EPISODE_LOCAL_FACTORS)
        return self._align_current_pair_owned(edge)

    def _align_current_pair_owned(self, edge: str) -> AlignedPair:
        """Return the sole runtime-owned current-action pair for one sealed edge.

        The private form is also used by the physical-candidate owner after
        local factor fitting.  Reusing an existing pair prevents a second
        observation of the same action from entering the persistent clock
        nuisance state merely because another downstream owner needs its
        source-row mapping.
        """

        existing = [
            pair for pair in self._current_owned_aligned_pairs.values()
            if pair.edge == edge
        ]
        if len(existing) > 1:
            raise RuntimeError(f"{edge}: more than one current runtime-owned aligned pair")
        if existing:
            self._validate_owned_aligned_pair(existing[0], expected_edge=edge)
            return existing[0]
        parent_node, child_node = self._sealed_edge_hardware_nodes(edge)
        pair = build_aligned_pair(
            self._current_oriented(), edge=edge,
            parent_node=parent_node, child_node=child_node,
            timing=self.settings["timing"], clock_state=self._clock_owner,
            execution_guard=self.guard,
        )
        payload = self._aligned_pair_binding_payload(pair)
        token = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        provenance = dict(pair.provenance)
        provenance.update({
            "runtime_owner_id": self._runtime_owner_id,
            "runtime_owner_token": token,
            "owner_binding_payload": payload,
            "caller_supplied_or_copied": False,
        })
        owned = replace(pair, provenance=provenance)
        for array in (
            owned.parent_acc, owned.child_acc, owned.parent_gyro, owned.child_gyro,
            owned.parent_observed_time_s, owned.child_observed_time_s,
            owned.parent_boot_epoch, owned.child_boot_epoch,
            owned.alignment.parent_indices, owned.alignment.child_indices,
        ):
            np.asarray(array).setflags(write=False)
        self._current_owned_aligned_pairs[token] = owned
        return owned

    def estimate_current_local_axis(
        self,
        edge: str,
        pairs: Sequence[AlignedPair],
        *,
        caller_covariance_override: Mapping[str, np.ndarray] | None = None,
    ) -> AxisEstimate:
        self._require("estimate_current_local_axis", PipelineStage.CURRENT_EPISODE_LOCAL_FACTORS)
        if caller_covariance_override is not None:
            self.guard.reject_caller_covariance_substitution(
                "axis covariance must come only from immutable P1 hardware-node stochastic state"
            )
        if edge in self._local_axes or edge not in HINGE_EDGES:
            raise ValueError("current episode axis factor is duplicate or not a hinge")
        if self._current_action not in EDGE_ACTIONS[edge] or any(pair.action != self._current_action for pair in pairs):
            raise ValueError("local axis factor may consume only the current preregistered action")
        if not pairs:
            raise ValueError("local axis estimation requires owner-produced aligned pairs")
        covariance_rows = [
            self._owned_pair_stochastic_covariances(pair, expected_edge=edge)
            for pair in pairs
        ]
        (
            parent_acc_covariance,
            child_acc_covariance,
            parent_gyro_covariance,
            child_gyro_covariance,
            parent_gyro_bias_covariance,
            child_gyro_bias_covariance,
            _,
        ) = covariance_rows[0]
        if any(
            not all(
                np.array_equal(left, right)
                for left, right in zip(covariance_rows[0][:6], row[:6])
            )
            for row in covariance_rows[1:]
        ):
            raise ValueError("multi-action axis pairs changed sealed endpoint covariance ownership")
        estimate = estimate_hinge_axis_qmt(
            edge, pairs, settings=self.settings["hinge_axis"],
            parent_acc_covariance=parent_acc_covariance,
            child_acc_covariance=child_acc_covariance,
            parent_gyro_covariance=parent_gyro_covariance,
            child_gyro_covariance=child_gyro_covariance,
            parent_gyro_bias_covariance=parent_gyro_bias_covariance,
            child_gyro_bias_covariance=child_gyro_bias_covariance,
            execution_guard=self.guard,
        )
        parent_node, child_node = self._sealed_edge_hardware_nodes(edge)
        calibration = self._current_oriented().calibration_posterior_by_node
        if parent_node not in calibration or child_node not in calibration:
            raise RuntimeError("current axis factor lacks capture-wide calibration snapshots")
        estimate = marginalize_axis_class_c(
            estimate,
            parent_snapshot=calibration[parent_node],
            child_snapshot=calibration[child_node],
        )
        report = dict(estimate.report)
        report["runtime_owned_stochastic_covariance_binding"] = dict(covariance_rows[0][6])
        estimate = replace(estimate, report=report)
        self._local_axes[edge] = estimate
        return estimate

    def _select_current_center_prefix(
        self,
        edge: str,
        current_pair: AlignedPair,
    ) -> CenterPrefixSelection:
        if (
            self._current_prediction is None
            or self._current_index is None
            or self._current_action is None
        ):
            self.guard.reject_center_prefix_future_or_heldout(
                "center prefix was requested before the pre-ingest prequential prediction"
            )
        if edge in self._current_center_prefix_selections:
            self.guard.reject_center_prefix_edge_pooling_or_backward_smoothing(
                "current action attempted a duplicate center-prefix selection"
            )
        self._validate_owned_aligned_pair(current_pair, expected_edge=edge)
        prediction_sha256 = sha256(
            json.dumps(
                self._canonical_state(self._current_prediction),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        selection = self._center_prefix_owner.select(
            edge=edge,
            current_pair=current_pair,
            chronological_index=self._current_index,
            action=self._current_action,
            prequential_prediction_sha256=prediction_sha256,
            geometry_has_accepted_center=(
                edge in self._geometry_owner.posterior_centers()
            ),
        )
        self._current_center_prefix_selections[edge] = selection
        return selection

    def record_current_center_no_update(
        self,
        edge: str,
        pair: AlignedPair,
        *,
        cause: str,
    ) -> Mapping[str, Any]:
        """Commit current pair provenance with zero center information.

        This is used only on a bounded retry after an ordinary local center
        estimator failure.  The pair can support a later causal prefix, while
        this action itself contributes no geometry or progressive information.
        """

        self._require(
            "record_current_center_no_update",
            PipelineStage.CURRENT_EPISODE_LOCAL_FACTORS,
        )
        if self._current_action not in EDGE_ACTIONS.get(edge, ()):
            raise ValueError("explicit center no-update is outside the registered edge route")
        selection = self._select_current_center_prefix(edge, pair)
        self._current_center_prefix_explicit_no_updates[edge] = str(cause)
        return {
            "schema": "biospur-c2-causal-center-prefix-explicit-local-no-update-v1",
            "edge": edge,
            "action": self._current_action,
            "selection_token": selection.selection_token,
            "ordered_prefix_membership_sha256": selection.report[
                "ordered_prefix_membership_sha256"
            ],
            "cause": str(cause),
            "geometry_information_added": False,
            "progressive_information_added": False,
            "current_pair_committed_to_zero_information_evidence_ledger": True,
            "current_pair_retained_for_later_estimator_prefix": False,
        }

    def estimate_current_local_center(
        self,
        edge: str,
        pairs: Sequence[AlignedPair],
        *,
        parent: str | None = None,
        child: str | None = None,
        caller_covariance_override: Mapping[str, np.ndarray] | None = None,
    ) -> CenterEstimate:
        self._require("estimate_current_local_center", PipelineStage.CURRENT_EPISODE_LOCAL_FACTORS)
        if parent is not None or child is not None:
            self.guard.reject_caller_endpoint_label_substitution(
                "center endpoint labels are derived only from the exact EDGE_SPECS owner"
            )
        if caller_covariance_override is not None:
            self.guard.reject_caller_covariance_substitution(
                "center covariance must come only from immutable P1 hardware-node stochastic state"
            )
        if edge in self._local_centers or self._current_action not in EDGE_ACTIONS[edge]:
            raise ValueError("current episode center factor is duplicate or not preregistered for this edge/action")
        if len(pairs) != 1 or pairs[0].action != self._current_action:
            raise ValueError(
                "center caller supplies exactly one current-action pair; historical prefix membership is owner-derived"
            )
        endpoints = {name: (parent, child) for name, parent, child in EDGE_SPECS}
        if edge not in endpoints:
            raise ValueError("local center edge is outside the official nine-edge rooted tree")
        parent, child = endpoints[edge]
        if not pairs:
            raise ValueError("local center estimation requires owner-produced aligned pairs")
        selection = self._select_current_center_prefix(edge, pairs[0])
        estimator_pairs = selection.pairs
        covariance_rows = [
            self._owned_pair_stochastic_covariances(
                pair,
                expected_edge=edge,
                allow_historical_center_prefix=True,
            )
            for pair in estimator_pairs
        ]
        (
            parent_acc_covariance,
            child_acc_covariance,
            parent_gyro_covariance,
            child_gyro_covariance,
            parent_gyro_bias_covariance,
            child_gyro_bias_covariance,
            _,
        ) = covariance_rows[0]
        if any(
            not all(
                np.array_equal(reference, observed)
                for reference, observed in zip(covariance_rows[0][:6], row[:6])
            )
            for row in covariance_rows[1:]
        ):
            raise ValueError("multi-action center pairs changed sealed endpoint covariance ownership")
        estimate = estimate_joint_center_pair_local(
            edge, parent, child, estimator_pairs, settings=self.settings["joint_center"],
            parent_acc_covariance=parent_acc_covariance,
            child_acc_covariance=child_acc_covariance,
            parent_gyro_observation_covariance=parent_gyro_covariance,
            child_gyro_observation_covariance=child_gyro_covariance,
            parent_gyro_bias_covariance=parent_gyro_bias_covariance,
            child_gyro_bias_covariance=child_gyro_bias_covariance,
            execution_guard=self.guard,
        )
        parent_node, child_node = self._sealed_edge_hardware_nodes(edge)
        calibration = self._current_oriented().calibration_posterior_by_node
        if parent_node not in calibration or child_node not in calibration:
            raise RuntimeError("current center factor lacks capture-wide calibration snapshots")
        estimate = marginalize_center_class_c(
            estimate,
            parent_snapshot=calibration[parent_node],
            child_snapshot=calibration[child_node],
        )
        report = dict(estimate.report)
        report["runtime_owned_stochastic_covariance_binding"] = dict(
            covariance_rows[0][6]
        )
        expected_pair_membership = [
            {
                "pair_index": int(pair_index),
                "action": pair.action,
                "runtime_owner_token": str(
                    pair.provenance.get("runtime_owner_token", "")
                ),
            }
            for pair_index, pair in enumerate(estimator_pairs)
        ]
        if report.get("ordered_pair_action_membership") != expected_pair_membership:
            self.guard.reject_center_prefix_nuisance_bypass_or_reingestion(
                "center estimator did not preserve exact causal pair membership/order"
            )
        if (
            not report.get("per_action_pair_cluster_identity_preserved", False)
            or report.get("historical_rows_treated_as_iid_after_concatenation", True)
            or not report.get("pair_block_cluster_identity_sha256")
        ):
            self.guard.reject_center_prefix_nuisance_bypass_or_reingestion(
                "center estimator lost pair/block cluster correlation ownership"
            )
        report["runtime_causal_center_prefix_binding"] = dict(selection.report)
        report["cumulative_prefix_authority_if_accepted"] = "PAIR_LOCAL_CENTER_ONLY"
        report["segment_frame_qmt_rooted_renderer_or_skeleton_authorized"] = False
        estimate = replace(estimate, report=report)
        self._local_centers[edge] = estimate
        return estimate

    def finish_current_geometry_update(self) -> Mapping[str, Any]:
        self._require("finish_current_geometry_update", PipelineStage.CURRENT_EPISODE_LOCAL_FACTORS)
        assert self._current_index is not None and self._current_action is not None
        assert self._current_reference_time_s is not None
        for edge, estimate in self._local_centers.items():
            accepted = self._geometry_owner.ingest_center(
                estimate, chronological_index=self._current_index, action=self._current_action,
                reference_time_s=self._current_reference_time_s,
            )
            if accepted is not None:
                self._accepted_local_centers[edge] = estimate
            selection = self._current_center_prefix_selections.get(edge)
            if selection is None:
                self.guard.reject_center_prefix_nuisance_bypass_or_reingestion(
                    "center geometry update lacks its causal prefix selection"
                )
            estimator_eligible = bool(
                estimate.report.get("owner_update_eligible", False)
            )
            prefix_commit = self._center_prefix_owner.commit(
                selection,
                estimator_owner_update_eligible=estimator_eligible,
                geometry_update_accepted=accepted is not None,
                estimator_completed=True,
                retain_for_future_prefix=bool(
                    accepted is None
                    and not selection.geometry_had_accepted_center_before_current
                ),
            )
            self._geometry_factor_decisions[("CENTER", edge)] = {
                "owner_update_eligible": accepted is not None,
                "decision_owner": "ProgressiveFunctionalGeometryOwner.ingest_center",
                "reason": (
                    "ACCEPTED_GAUGE_REDUCED_ROBUST_BREAD_INFORMED_SUBSPACE"
                    if accepted is not None else str(estimate.report["owner_update_mode"])
                ),
                "causal_center_prefix_commit": dict(prefix_commit),
            }
        for edge, estimate in self._local_axes.items():
            accepted = self._geometry_owner.ingest_axis(
                estimate, chronological_index=self._current_index, action=self._current_action,
                reference_time_s=self._current_reference_time_s,
            )
            if accepted is not None:
                self._accepted_local_axes[edge] = estimate
            self._geometry_factor_decisions[("AXIS", edge)] = {
                "owner_update_eligible": accepted is not None,
                "decision_owner": "ProgressiveFunctionalGeometryOwner.ingest_axis",
                "reason": (
                    "ACCEPTED_PRODUCT_S2_INFORMED_UPDATE"
                    if accepted is not None else str(
                        estimate.report.get("owner_update_mode", "ANTIPODAL_OR_LOW_INFORMATION_LOCAL_NO_UPDATE")
                    )
                ),
            }
        for edge, _, _ in EDGE_SPECS:
            if edge not in self._local_centers:
                cause = (
                    "CURRENT_EPISODE_LOCAL_FACTOR_UNUSABLE_OR_ABSENT"
                    if self._current_action in EDGE_ACTIONS[edge]
                    else "EDGE_NOT_ROUTED_TO_CURRENT_ACTION_TEMPORAL_NO_UPDATE"
                )
                self._geometry_owner.no_update(
                    edge=edge, kind="CENTER", chronological_index=self._current_index,
                    action=self._current_action, reference_time_s=self._current_reference_time_s,
                    cause=cause,
                )
                decision = {
                    "owner_update_eligible": False,
                    "decision_owner": "ProgressiveFunctionalGeometryOwner.no_update",
                    "reason": cause,
                }
                if edge in self._current_center_prefix_explicit_no_updates:
                    selection = self._current_center_prefix_selections.get(edge)
                    if selection is None:
                        self.guard.reject_center_prefix_nuisance_bypass_or_reingestion(
                            "explicit center no-update lacks its causal prefix selection"
                        )
                    prefix_commit = self._center_prefix_owner.commit(
                        selection,
                        estimator_owner_update_eligible=False,
                        geometry_update_accepted=False,
                        estimator_completed=False,
                        retain_for_future_prefix=False,
                    )
                    decision["reason"] = self._current_center_prefix_explicit_no_updates[edge]
                    decision["causal_center_prefix_commit"] = dict(prefix_commit)
                    decision["current_pair_committed_with_zero_information"] = True
                self._geometry_factor_decisions[("CENTER", edge)] = decision
            if edge in HINGE_EDGES and edge not in self._local_axes:
                cause = (
                    "CURRENT_EPISODE_LOCAL_FACTOR_UNUSABLE_OR_ABSENT"
                    if self._current_action in EDGE_ACTIONS[edge]
                    else "EDGE_NOT_ROUTED_TO_CURRENT_ACTION_TEMPORAL_NO_UPDATE"
                )
                self._geometry_owner.no_update(
                    edge=edge, kind="AXIS", chronological_index=self._current_index,
                    action=self._current_action, reference_time_s=self._current_reference_time_s,
                    cause=cause,
                )
                self._geometry_factor_decisions[("AXIS", edge)] = {
                    "owner_update_eligible": False,
                    "decision_owner": "ProgressiveFunctionalGeometryOwner.no_update",
                    "reason": cause,
                }
        centers = self._geometry_owner.posterior_centers()
        axes = self._geometry_owner.posterior_axes()
        self._transition(
            PipelineStage.CURRENT_EPISODE_GEOMETRY_UPDATED,
            cause=(
                "CURRENT_ACTION_OR_FIRST_ACCEPTED_CAUSAL_SAME_EDGE_PREFIX_FUSED_"
                "ONCE_INTO_PERSISTENT_GEOMETRY"
            ),
        )
        return {
            "posterior_center_edges": sorted(centers),
            "posterior_axis_edges": sorted(axes),
            "full_functional_geometry_available": len(centers) == 9 and len(axes) == 4,
            "future_episode_factors_used": False,
            "causal_center_prefix_owner_audit": self._center_prefix_owner.audit(),
            "historical_center_information_reingested_after_first_acceptance": False,
            "cumulative_center_authority_scope": "PAIR_LOCAL_CENTER_ONLY",
            "segment_frame_qmt_rooted_renderer_or_skeleton_authorized_by_center": False,
        }

    def inject_transaction_failure_after_geometry(self) -> None:
        """Synthetic negative-gate hook; never used by the real runner."""

        self._require("inject_transaction_failure_after_geometry", PipelineStage.CURRENT_EPISODE_GEOMETRY_UPDATED)
        raise RuntimeError("INJECTED_SYNTHETIC_FAILURE_AFTER_GEOMETRY")

    def _sealed_initial_still_orientation_evidence(self) -> Mapping[str, Mapping[str, Any]]:
        if not self._oriented_actions or self._oriented_actions[0].action != "00_initial_still":
            raise RuntimeError("sealed initial-still OrientedAction is unavailable")
        initial = self._oriented_actions[0]
        rows = tuple(self.settings["segment_frames"]["wear_authority"]["rows"])
        evidence: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            node = str(row["hardware_id"])
            segment = str(row["body_segment"])
            if node not in initial.quat_world_sensor_wxyz_by_node:
                raise ValueError("initial-still OrientedAction does not contain the sealed hardware node")
            quaternion = np.asarray(initial.quat_world_sensor_wxyz_by_node[node], dtype=float)
            time = np.asarray(initial.time_us_by_node[node], dtype=np.int64)
            boot = np.asarray(initial.derived_boot_epoch_by_node[node], dtype=np.int64)
            if quaternion.shape != (len(time), 4) or boot.shape != time.shape:
                raise ValueError("initial-still orientation/time/boot evidence shapes differ")
            evidence[segment] = {
                "hardware_id": node,
                "source_action": initial.action,
                "source_chronological_index": int(initial.chronological_index),
                "row_count": int(len(time)),
                "orientation_array_sha256": self._array_sha256(quaternion),
                "time_us_sha256": self._array_sha256(time),
                "derived_boot_epoch_sha256": self._array_sha256(boot),
                "selection": "FULL_SEALED_INITIAL_STILL_ORIENTATION_EVIDENCE_HASH_ONLY",
                "pose_truth_or_caller_matrix_used": False,
            }
        return evidence

    def update_current_frame_branches(self) -> tuple[SegmentFrameBranch, ...]:
        self._require("update_current_frame_branches", PipelineStage.CURRENT_EPISODE_GEOMETRY_UPDATED)
        assert self._current_index is not None and self._current_action is not None
        centers = self._geometry_owner.posterior_centers()
        axes = self._geometry_owner.posterior_axes()
        all_parameter_factors_present = (
            len(centers) == len(EDGE_SPECS)
            and len(axes) == len(HINGE_EDGES)
        )
        branches = self._frame_owner.build_online(
            axes, centers,
            chronological_index=self._current_index,
            action=self._current_action,
        )
        initial_still_evidence = self._sealed_initial_still_orientation_evidence()
        assessments = {
            branch.branch_id: self._frame_owner.assess_low_information_initial_still_candidate(
                branch, initial_still_evidence,
            )
            for branch in branches
        }
        branch_ids = tuple(branch.branch_id for branch in branches)
        if branch_ids != self._branch_ids:
            raise RuntimeError("progressive frame branch identities changed")
        current_legal = np.asarray([
            branch.retained and assessments[branch.branch_id].physically_legal
            for branch in branches
        ], dtype=bool)
        self._branch_support &= current_legal
        if not np.any(self._branch_support):
            raise RuntimeError("trajectory-derived physical evidence eliminated every branch")
        if self._heading_owner is None:
            self._heading_owner = PersistentHeadingOwner(
                self.settings["heading"], branches,
                execution_guard=self.guard,
                first_chronological_index=self._current_index,
            )
        else:
            self._heading_owner.update_frame_branches(branches)
        self._current_frame_branches = branches
        self._current_assessments = assessments
        ready_by_branch = [
            tuple(branch.report["qmt_ready_edges"])
            for branch in branches
        ]
        if any(value != ready_by_branch[0] for value in ready_by_branch[1:]):
            raise RuntimeError("online QMT-ready edge ownership differs across sign branches")
        full_geometry_ready = bool(
            all_parameter_factors_present
            and all(
                bool(branch.report.get("complete_nine_edge_frame_geometry", False))
                for branch in branches
            )
        )
        self._current_qmt_ready_edges = ready_by_branch[0]
        self._current_full_frame_geometry_ready = full_geometry_ready
        self._current_qmt_observation_allowed = bool(self._current_qmt_ready_edges)
        self._transition(
            PipelineStage.CURRENT_EPISODE_FRAMES_UPDATED,
            cause=(
                "CURRENT_PREFIX_COMPLETE_POSTERIOR_SO3_BRANCHES_UPDATED"
                if full_geometry_ready else
                "CURRENT_PREFIX_EDGE_LOCAL_MATURE_SO3_BRANCHES_PLUS_BROAD_UNRESOLVED_LONGITUDINAL_SUPPORT_UPDATED"
            ),
        )
        return branches

    def current_heading_edge_readiness(self) -> Mapping[str, Any]:
        """Owner-issued edge-local QMT maturity; unresolved edges must no-update."""

        self._require(
            "current_heading_edge_readiness",
            PipelineStage.CURRENT_EPISODE_FRAMES_UPDATED,
        )
        assert self._current_index is not None and self._current_action is not None
        return MappingProxyType({
            "schema": "biospur-c2-runtime-edge-local-qmt-readiness-v1",
            "chronological_index": int(self._current_index),
            "action": self._current_action,
            "ready_edges": self._current_qmt_ready_edges,
            "unresolved_edges": tuple(
                edge for edge, _, _ in EDGE_SPECS
                if edge not in self._current_qmt_ready_edges
            ),
            "complete_nine_edge_frame_geometry": self._current_full_frame_geometry_ready,
            "unrelated_unfinished_center_blocks_mature_edge_qmt": False,
            "caller_selected": False,
        })

    def current_heading_hard_support(self) -> Mapping[str, Any]:
        """Return an owner-bound hard support token; soft weights never filter it."""

        self._require(
            "current_heading_hard_support",
            PipelineStage.CURRENT_EPISODE_FRAMES_UPDATED,
        )
        if self._current_index is None or self._current_action is None:
            raise RuntimeError("hard branch support requires a current causal episode")
        if not self._current_frame_branches:
            return MappingProxyType({
                "schema": "biospur-c2-runtime-hard-branch-support-v1",
                "chronological_index": int(self._current_index),
                "action": self._current_action,
                "branch_ids": (),
                "hard_support_mask": tuple(bool(value) for value in self._branch_support),
                "qmt_ready_edges": (),
                "complete_nine_edge_frame_geometry": False,
                "owner_token": None,
                "soft_branch_weights_consulted": False,
            })
        branch_ids = tuple(
            branch_id for branch_id, retained in zip(self._branch_ids, self._branch_support)
            if bool(retained)
        )
        payload = {
            "schema": "biospur-c2-runtime-hard-branch-support-v1",
            "runtime_owner_id": self._runtime_owner_id,
            "chronological_index": int(self._current_index),
            "action": self._current_action,
            "all_branch_ids": list(self._branch_ids),
            "hard_support_mask": self._branch_support.tolist(),
            "branch_ids": list(branch_ids),
            "qmt_ready_edges": list(self._current_qmt_ready_edges),
            "complete_nine_edge_frame_geometry": self._current_full_frame_geometry_ready,
            "soft_branch_weights_consulted": False,
        }
        token = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self._current_hard_support_token = token
        return MappingProxyType({**payload, "owner_token": token})

    def _validate_current_hard_support_token(self, branch_id: str, token: str) -> None:
        support = self.current_heading_hard_support()
        if token != support["owner_token"] or branch_id not in support["branch_ids"]:
            self.guard.reject_soft_weight_candidate_lock(
                "QMT branch selection differs from the runtime-owned cumulative hard-support token"
            )

    def validate_heading_execution_branch_ids(
        self,
        branch_ids: Sequence[str],
        *,
        hard_support_token: str | None,
    ) -> None:
        """Require execution on the exact hard support, irrespective of soft weights."""

        support = self.current_heading_hard_support()
        if (
            hard_support_token != support["owner_token"]
            or tuple(str(value) for value in branch_ids) != tuple(support["branch_ids"])
        ):
            self.guard.reject_soft_weight_candidate_lock(
                "QMT execution branch list omitted or added a branch relative to cumulative hard support"
            )

    def _cumulative_observed_orientation_terms(
        self,
        *,
        node: str,
        current_source_index: int,
    ) -> Mapping[str, float | int]:
        """Integrate only retained observed rows through the current source row.

        No inter-episode interval, timer gap, or boot transition is converted
        into observed samples here.  Those unobserved intervals already live
        in the orientation owner's separate cumulative gap covariance.
        """

        assert self._current_index is not None
        return cumulative_observed_orientation_terms(
            self._oriented_actions,
            node=node,
            current_action_index=self._current_index,
            current_source_index=current_source_index,
            sample_period_s=float(self.settings["orientation"]["sample_period_s"]),
        )

    def _physical_orientation_covariance(
        self,
        *,
        segment: str,
        node: str,
        source_indices: np.ndarray,
        timing_sigma_s: np.ndarray,
    ) -> tuple[np.ndarray, Mapping[str, Any]]:
        """Build time-local observed-span plus no-update orientation uncertainty."""

        assert self._current_index is not None
        return physical_orientation_covariance(
            self._oriented_actions,
            current_action_index=self._current_index,
            segment=segment,
            node=node,
            source_indices=source_indices,
            timing_sigma_s=timing_sigma_s,
            initial_stochastic_state=self._initial_stochastic_state,
            initial_stochastic_state_semantic_sha256=(
                self._initial_stochastic_state_semantic_sha256
            ),
            orientation_settings=self.settings["orientation"],
            uncertainty_settings=self.settings["physical_candidates"][
                "orientation_uncertainty"
            ],
        )

    def _owner_derived_physical_prefix_inputs(
        self,
    ) -> tuple[
        Mapping[str, Mapping[str, np.ndarray]],
        Mapping[str, Mapping[str, np.ndarray]],
        Mapping[str, Mapping[str, Any]],
        Mapping[str, Any],
    ]:
        """Derive QMT-corrected rooted physical samples from the current action."""

        if not self._current_frame_branches or self._current_index is None or self._current_action is None:
            raise RuntimeError("current prefix has no posterior SO(3) branches to assess")
        oriented = self._current_oriented()
        pair_by_edge: dict[str, AlignedPair] = {}
        pair_failures: dict[str, Any] = {}
        for edge, _, _ in EDGE_SPECS:
            try:
                pair_by_edge[edge] = self._align_current_pair_owned(edge)
            except ClassAGuardViolation:
                raise
            except (ValueError, RuntimeError) as exc:
                pair_failures[edge] = {
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "ordinary_local_alignment_failure": True,
                }
        if pair_failures:
            raise ValueError(
                "physical prefix lacks a complete owner-aligned rooted tree: "
                + json.dumps(pair_failures, sort_keys=True)
            )

        root_node = self._frame_owner.hardware_node_for_segment("pelvis")
        root_time = np.asarray(oriented.time_us_by_node[root_node], dtype=np.int64)
        if not len(root_time):
            raise ValueError("physical prefix root has zero retained orientation rows")
        quantiles = np.asarray(
            self.settings["physical_candidates"]["trajectory_sample_quantiles"], dtype=float,
        )
        if quantiles.ndim != 1 or not len(quantiles) or np.any((quantiles < 0.0) | (quantiles > 1.0)):
            raise ValueError("registered physical trajectory quantiles must be a nonempty subset of [0,1]")
        candidate_root_indices = np.unique(np.rint(quantiles * (len(root_time) - 1)).astype(np.int64))
        maximum_projection_error_s = float(
            self.settings["physical_candidates"]["maximum_pair_projection_time_error_s"]
        )
        if maximum_projection_error_s < 0.0:
            raise ValueError("physical pair projection tolerance must be nonnegative")

        accepted_segment_indices: dict[str, list[int]] = {
            segment: [] for segment in self._frame_owner.segment_names
        }
        accepted_timing_variance_s2: dict[str, list[float]] = {
            segment: [] for segment in self._frame_owner.segment_names
        }
        accepted_root_indices: list[int] = []
        rejected_samples: list[dict[str, Any]] = []
        per_sample_pair_rows: list[dict[str, Any]] = []
        root_time_sigma_s = float(self.settings["physical_candidates"]["root_clock_sigma_s"])
        for root_index in candidate_root_indices:
            segment_indices: dict[str, int] = {"pelvis": int(root_index)}
            timing_variance: dict[str, float] = {"pelvis": root_time_sigma_s**2}
            pair_rows: dict[str, Any] = {}
            rejection: str | None = None
            for edge, parent, child in EDGE_SPECS:
                pair = pair_by_edge[edge]
                payload = self._validate_owned_aligned_pair(pair, expected_edge=edge)
                parent_node = str(payload["parent_node"])
                child_node = str(payload["child_node"])
                parent_source = np.asarray(pair.alignment.parent_indices, dtype=np.int64)
                child_source = np.asarray(pair.alignment.child_indices, dtype=np.int64)
                target_index = segment_indices[parent]
                target_time = int(oriented.time_us_by_node[parent_node][target_index])
                target_boot = int(oriented.derived_boot_epoch_by_node[parent_node][target_index])
                eligible = np.flatnonzero(
                    np.asarray(oriented.derived_boot_epoch_by_node[parent_node][parent_source], dtype=np.int64)
                    == target_boot
                )
                if not len(eligible):
                    rejection = f"{edge}:NO_MATCH_IN_SAME_DERIVED_BOOT"
                    break
                distances_s = np.abs(
                    np.asarray(oriented.time_us_by_node[parent_node][parent_source[eligible]], dtype=float)
                    - float(target_time)
                ) * 1e-6
                selected_local = int(eligible[int(np.argmin(distances_s))])
                projection_error_s = float(np.min(distances_s))
                if projection_error_s > maximum_projection_error_s:
                    rejection = f"{edge}:PARENT_PROJECTION_OUTSIDE_REGISTERED_TOLERANCE"
                    break
                segment_indices[child] = int(child_source[selected_local])
                lag_sigma_s = float(pair.alignment.report["lag_uncertainty_s"])
                timing_variance[child] = timing_variance[parent] + lag_sigma_s**2
                pair_rows[edge] = {
                    "runtime_owner_token": pair.provenance["runtime_owner_token"],
                    "selected_aligned_row": selected_local,
                    "parent_source_index": int(parent_source[selected_local]),
                    "child_source_index": int(child_source[selected_local]),
                    "parent_projection_error_s": projection_error_s,
                    "pair_lag_sigma_s": lag_sigma_s,
                }
            if rejection is not None or set(segment_indices) != set(self._frame_owner.segment_names):
                rejected_samples.append({
                    "root_source_index": int(root_index),
                    "reason": rejection or "INCOMPLETE_ROOTED_SEGMENT_INDEX_MAP",
                })
                continue
            accepted_root_indices.append(int(root_index))
            per_sample_pair_rows.append(pair_rows)
            for segment in self._frame_owner.segment_names:
                accepted_segment_indices[segment].append(segment_indices[segment])
                accepted_timing_variance_s2[segment].append(timing_variance[segment])
        if not accepted_root_indices:
            raise ValueError("no registered physical sample quantile maps through all nine gap-safe pairs")

        segment_indices_array = {
            segment: np.asarray(values, dtype=np.int64)
            for segment, values in accepted_segment_indices.items()
        }
        base_common_time_s = root_time[np.asarray(accepted_root_indices, dtype=np.int64)].astype(float) * 1e-6
        orientation_covariance: dict[str, np.ndarray] = {}
        uncertainty_audit: dict[str, Any] = {}
        world_from_sensor: dict[str, np.ndarray] = {}
        for segment in self._frame_owner.segment_names:
            node = self._frame_owner.hardware_node_for_segment(segment)
            indices = segment_indices_array[segment]
            quaternion = np.asarray(oriented.quat_world_sensor_wxyz_by_node[node][indices], dtype=float)
            world_from_sensor[segment] = qmt_wxyz_to_scipy_active(quaternion).as_matrix()
            covariance, audit = self._physical_orientation_covariance(
                segment=segment,
                node=node,
                source_indices=indices,
                timing_sigma_s=np.sqrt(np.asarray(accepted_timing_variance_s2[segment], dtype=float)),
            )
            orientation_covariance[segment] = covariance
            uncertainty_audit[segment] = audit

        trajectory_by_branch: dict[str, Mapping[str, np.ndarray]] = {}
        covariance_by_branch: dict[str, Mapping[str, np.ndarray]] = {}
        binding_by_branch: dict[str, Mapping[str, Any]] = {}
        for branch in self._current_frame_branches:
            if not self._branch_support[self._branch_ids.index(branch.branch_id)]:
                continue
            if branch.branch_id not in self._current_heading_trajectories:
                raise ValueError("physical assessment lacks the owner-produced rooted QMT trajectory")
            heading = self._current_heading_trajectories[branch.branch_id]
            full_base_time_s = root_time.astype(float) * 1e-6
            if (
                heading.report.get("schema")
                != "biospur-c2-action-common-grid-rooted-heading-trajectory-v1"
                or heading.report.get("tree_semantics")
                != "child_global = parent_global + time_varying_edge_deltaFilt"
                or not np.array_equal(heading.common_physical_time_s, full_base_time_s)
            ):
                raise ValueError("physical assessment requires the exact official rooted common-grid heading owner")
            root_selection = np.asarray(accepted_root_indices, dtype=np.int64)
            trajectory: dict[str, np.ndarray] = {}
            total_covariance: dict[str, np.ndarray] = {}
            raw_world_from_segment_hashes: dict[str, str] = {}
            heading_delta_hashes: dict[str, str] = {}
            heading_variance_hashes: dict[str, str] = {}
            for segment in self._frame_owner.segment_names:
                raw_world_from_segment = np.einsum(
                    "nij,jk->nik", world_from_sensor[segment], branch.sensor_from_segment[segment],
                )
                global_delta = np.asarray(
                    heading.segment_global_delta_rad[segment], dtype=float,
                )[root_selection]
                global_variance = np.asarray(
                    heading.segment_global_variance_rad2[segment], dtype=float,
                )[root_selection]
                yaw_correction = Rotation.from_rotvec(
                    np.column_stack((np.zeros(len(global_delta)), np.zeros(len(global_delta)), global_delta))
                ).as_matrix()
                trajectory[segment] = np.einsum(
                    "nij,njk->nik", yaw_correction, raw_world_from_segment,
                )
                heading_covariance = np.zeros_like(orientation_covariance[segment])
                heading_covariance[:, 2, 2] = global_variance
                total_covariance[segment] = orientation_covariance[segment] + heading_covariance
                raw_world_from_segment_hashes[segment] = self._array_sha256(raw_world_from_segment)
                heading_delta_hashes[segment] = self._array_sha256(global_delta)
                heading_variance_hashes[segment] = self._array_sha256(global_variance)
            binding = {
                "schema": "biospur-c2-runtime-owned-physical-prefix-input-v1",
                "runtime_owner_id": self._runtime_owner_id,
                "branch_id": branch.branch_id,
                "chronological_index": int(self._current_index),
                "action": self._current_action,
                "base_common_physical_time_s_sha256": self._array_sha256(base_common_time_s),
                "base_common_physical_time_s": base_common_time_s.tolist(),
                "source_indices_sha256": {
                    segment: self._array_sha256(indices)
                    for segment, indices in segment_indices_array.items()
                },
                "world_from_segment_sha256": {
                    segment: self._array_sha256(value) for segment, value in trajectory.items()
                },
                "orientation_covariance_sha256": {
                    segment: self._array_sha256(value)
                    for segment, value in total_covariance.items()
                },
                "raw_world_from_segment_sha256": raw_world_from_segment_hashes,
                "rooted_qmt_segment_global_delta_sha256": heading_delta_hashes,
                "rooted_qmt_segment_global_variance_sha256": heading_variance_hashes,
                "rooted_qmt_trajectory_common_time_sha256": self._array_sha256(
                    heading.common_physical_time_s
                ),
                "rooted_qmt_trajectory_report": dict(heading.report),
                "pair_runtime_owner_tokens": {
                    edge: pair_by_edge[edge].provenance["runtime_owner_token"]
                    for edge, _, _ in EDGE_SPECS
                },
                "source": "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_CORRECTED_CURRENT_SEALED_ORIENTED_ACTION",
                "raw_unqmt_orientation_allowed_to_drive_physical_gate": False,
                "qmt_branch_evidence_ingested_before_physical_gate": False,
                "future_episode_or_caller_pose_truth_used": False,
            }
            payload_sha256, owner_token = physical_input_binding_token(
                self._physical_input_binding_secret, binding,
            )
            binding.update({
                "runtime_owner_binding_payload_sha256": payload_sha256,
                "runtime_owner_token": owner_token,
            })
            trajectory_by_branch[branch.branch_id] = trajectory
            covariance_by_branch[branch.branch_id] = total_covariance
            binding_by_branch[branch.branch_id] = binding
        common_audit = {
            "schema": "biospur-c2-runtime-owned-physical-prefix-input-audit-v1",
            "chronological_index": int(self._current_index),
            "action": self._current_action,
            "registered_quantiles": quantiles.tolist(),
            "candidate_root_source_indices": candidate_root_indices.tolist(),
            "accepted_root_source_indices": accepted_root_indices,
            "rejected_samples": rejected_samples,
            "base_common_physical_time_s": base_common_time_s.tolist(),
            "base_common_physical_time_s_sha256": self._array_sha256(base_common_time_s),
            "source_indices_by_segment": {
                segment: indices.tolist() for segment, indices in segment_indices_array.items()
            },
            "source_indices_sha256_by_segment": {
                segment: self._array_sha256(indices)
                for segment, indices in segment_indices_array.items()
            },
            "pair_selected_rows": per_sample_pair_rows,
            "pair_tokens": {
                edge: pair_by_edge[edge].provenance["runtime_owner_token"]
                for edge, _, _ in EDGE_SPECS
            },
            "orientation_uncertainty": uncertainty_audit,
            "physical_orientation_source": "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_CORRECTED",
            "raw_unqmt_orientation_allowed_to_drive_physical_gate": False,
            "qmt_branch_evidence_ingested_before_physical_gate": False,
            "common_grid_owner": "SEALED_PELVIS_TIMER_ROWS_PROJECTED_THROUGH_PERSISTENT_PAIR_CLOCK_ALIGNMENTS",
            "union_or_interpolated_grid_used_as_evidence": False,
            "caller_matrix_or_pose_truth_used": False,
        }
        return trajectory_by_branch, covariance_by_branch, binding_by_branch, common_audit

    def assess_current_physical_candidates(
        self,
    ) -> Mapping[str, PhysicalTrajectoryCandidateAssessment]:
        """Hard-gate only official-QMT/rooted owner trajectories before progress."""

        self._require(
            "assess_current_physical_candidates",
            PipelineStage.CURRENT_EPISODE_HEADING_UPDATED,
        )
        assert self._current_index is not None and self._current_action is not None
        self._current_physical_gate_completed = False
        unresolved_nonhinge_heading_by_branch = {
            branch_id: list(
                trajectory.report.get("unobserved_nonhinge_heading_edges", [])
            )
            for branch_id, trajectory in self._current_heading_trajectories.items()
            if trajectory.report.get("unobserved_nonhinge_heading_edges")
        }
        if (
            not self._current_frame_branches
            or not self._current_full_frame_geometry_ready
            or bool(unresolved_nonhinge_heading_by_branch)
        ):
            self._current_physical_world_from_segment = {}
            self._current_physical_orientation_covariance = {}
            self._current_physical_owner_bindings = {}
            self._current_physical_frame_branches = {}
            self._current_physical_input_audit = {
                "schema": "biospur-c2-runtime-owned-physical-prefix-input-audit-v1",
                "chronological_index": int(self._current_index),
                "action": self._current_action,
                "status": (
                    "EDGE_LOCAL_QMT_PRESERVED;FULL_NINE_EDGE_PHYSICAL_GATE_UNRESOLVED"
                    if self._current_qmt_ready_edges else
                    "UNRESOLVED_INCOMPLETE_FUNCTIONAL_GEOMETRY_AND_QMT_LOCAL_NO_UPDATE"
                ),
                "qmt_ready_edges": list(self._current_qmt_ready_edges),
                "complete_nine_edge_frame_geometry": self._current_full_frame_geometry_ready,
                "unobserved_nonhinge_heading_edges_by_branch": (
                    unresolved_nonhinge_heading_by_branch
                ),
                "factorized_unobserved_nonhinge_heading_support_retained": bool(
                    unresolved_nonhinge_heading_by_branch
                ),
                "carried_zero_heading_coordinate_used_as_physical_pose": False,
                "full_body_physical_legality_status": (
                    "UNKNOWN_FACTORIZED_NONHINGE_HEADING_SUPPORT"
                    if unresolved_nonhinge_heading_by_branch else
                    "UNKNOWN_INCOMPLETE_FRAME_GEOMETRY"
                ),
                "full_body_hard_physical_rejection_executed": False,
                "cumulative_branch_hard_support_changed": False,
                "incomplete_partial_rooted_trajectory_treated_as_illegal_full_body": False,
                "observed_edge_local_qmt_evidence_may_enter_progressive": True,
                "unobserved_nonhinge_heading_support_enters_as_information": False,
                "caller_matrix_or_pose_truth_used": False,
            }
            self._transition(
                PipelineStage.CURRENT_EPISODE_PHYSICAL_CANDIDATES_ASSESSED,
                cause=(
                    "EDGE_LOCAL_QMT_RETAINED;FULL_TREE_PHYSICAL_GATE_EXPLICITLY_DEFERRED"
                    if self._current_qmt_ready_edges else
                    "FUNCTIONAL_FRAMES_AND_QMT_UNRESOLVED;POST_QMT_PHYSICAL_LOCAL_NO_UPDATE"
                ),
            )
            return {}
        try:
            trajectories, covariances, bindings, common_audit = (
                self._owner_derived_physical_prefix_inputs()
            )
        except ClassAGuardViolation:
            raise
        except (ValueError, RuntimeError) as exc:
            self._current_physical_world_from_segment = {}
            self._current_physical_orientation_covariance = {}
            self._current_physical_owner_bindings = {}
            self._current_physical_frame_branches = {}
            self._current_physical_input_audit = {
                "schema": "biospur-c2-runtime-owned-physical-prefix-input-audit-v1",
                "chronological_index": int(self._current_index),
                "action": self._current_action,
                "status": "UNRESOLVED_ORDINARY_POST_QMT_OWNER_INPUT_FAILURE;QMT_EVIDENCE_SUPPRESSED",
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "caller_matrix_or_pose_truth_used": False,
            }
            self._transition(
                PipelineStage.CURRENT_EPISODE_PHYSICAL_CANDIDATES_ASSESSED,
                cause="POST_QMT_PHYSICAL_PREFIX_INPUT_UNRESOLVED;QMT_LIKELIHOOD_SUPPRESSED_NOT_HARD_CAPTURE_STOP",
            )
            return {}

        self._current_physical_world_from_segment = deepcopy(trajectories)
        self._current_physical_orientation_covariance = deepcopy(covariances)
        self._current_physical_owner_bindings = deepcopy(bindings)
        self._current_physical_frame_branches = {
            branch.branch_id: deepcopy(branch)
            for branch in self._current_frame_branches
            if branch.branch_id in bindings
        }

        assessments: dict[str, PhysicalTrajectoryCandidateAssessment] = {}
        soft_total = np.zeros(len(self._branch_ids), dtype=float)
        soft_components: dict[str, dict[str, float]] = {}
        current_legal = np.zeros(len(self._branch_ids), dtype=bool)
        for branch in self._current_frame_branches:
            if not self._branch_support[self._branch_ids.index(branch.branch_id)]:
                continue
            assessment = self._fk_owner.assess_prefix_trajectory(
                frame_branch=branch,
                world_from_segment_trajectory=trajectories[branch.branch_id],
                orientation_tangent_covariance_rad2=covariances[branch.branch_id],
                owner_input_binding=bindings[branch.branch_id],
            )
            branch_index = self._branch_ids.index(branch.branch_id)
            assessments[branch.branch_id] = assessment
            current_legal[branch_index] = bool(assessment.physically_legal)
            component_sum = float(
                assessment.rom_log_likelihood
                + assessment.bilateral_log_likelihood
                + assessment.gravity_log_likelihood
            )
            if not np.isclose(
                assessment.soft_total_log_likelihood,
                component_sum,
                rtol=0.0,
                atol=1e-12,
            ):
                raise RuntimeError("physical owner soft-total likelihood differs from its components")
            soft_total[branch_index] = float(assessment.soft_total_log_likelihood)
            soft_components[branch.branch_id] = {
                "rom_log_likelihood": float(assessment.rom_log_likelihood),
                "bilateral_log_likelihood": float(assessment.bilateral_log_likelihood),
                "gravity_log_likelihood": float(assessment.gravity_log_likelihood),
                "soft_total_log_likelihood": float(assessment.soft_total_log_likelihood),
            }
        self._current_physical_trajectory_assessments = assessments
        self._current_physical_soft_log_likelihood = soft_total
        self._current_physical_input_audit = {
            **dict(common_audit),
            "status": "OWNER_DERIVED_QMT_CORRECTED_PHYSICAL_CANDIDATES_ASSESSED_BEFORE_PROGRESSIVE",
            "branch_hard_support_before": self._branch_support.tolist(),
            "current_physical_legal": current_legal.tolist(),
            "soft_physical_log_likelihood": soft_total.tolist(),
            "soft_physical_log_likelihood_components_by_branch": soft_components,
            "soft_total_is_sole_physical_likelihood_entering_progressive": True,
            "qmt_rooted_trajectory_completed_before_hard_gates": True,
            "qmt_likelihood_not_yet_ingested": True,
        }
        combined_support = self._branch_support & current_legal
        if not np.any(combined_support):
            self._current_all_physical_candidates_invalid = True
            self._current_physical_input_audit["status"] = (
                "ALL_CUMULATIVELY_SUPPORTED_PHYSICAL_CANDIDATES_REJECTED_TRANSACTION_MUST_ROLL_BACK"
            )
            self._current_physical_input_audit["branch_hard_support_after"] = combined_support.tolist()
            self.guard.reject_all_invalid_physical_candidate_commit(
                "owner-derived post-QMT physical assessment rejected every cumulatively supported branch; "
                "the episode transaction must restore the immutable prefix"
            )
        self._branch_support = combined_support
        self._current_physical_input_audit["branch_hard_support_after"] = self._branch_support.tolist()
        self._current_physical_gate_completed = True
        self._transition(
            PipelineStage.CURRENT_EPISODE_PHYSICAL_CANDIDATES_ASSESSED,
            cause="OFFICIAL_QMT_ROOTED_TRAJECTORY_HARD_TOPOLOGY_GRAVITY_KNEE_AND_SOFT_ROM_GATES_COMPLETE_BEFORE_PROGRESSIVE",
        )
        return dict(assessments)

    def process_current_heading_span(
        self,
        *,
        branch_id: str,
        pair: AlignedPair,
        span_index: int,
        hard_support_token: str,
    ) -> HeadingSpanResult:
        self._require(
            "process_current_heading_span",
            PipelineStage.CURRENT_EPISODE_FRAMES_UPDATED,
        )
        if self._heading_owner is None or self._current_index is None or self._current_action is None:
            raise RuntimeError("current prefix has no heading owner")
        if not self._current_qmt_observation_allowed:
            raise RuntimeError("QMT evidence is forbidden because current posterior functional frames are unresolved")
        self._validate_current_hard_support_token(branch_id, hard_support_token)
        edge = pair.edge
        if edge not in self._current_qmt_ready_edges:
            raise RuntimeError(
                f"{edge}: QMT evidence is forbidden until both edge-local functional frames mature"
            )
        branch_index = self._branch_ids.index(branch_id)
        if not self._branch_support[branch_index]:
            raise ValueError("heading observation requested for an eliminated physical branch")
        payload = self._validate_owned_aligned_pair(pair, expected_edge=edge)
        if span_index < 0 or span_index >= len(pair.contiguous_spans):
            raise IndexError("heading span index leaves owner-produced gap-safe pair spans")
        span = pair.contiguous_spans[span_index]
        parent_indices = np.asarray(pair.alignment.parent_indices[span], dtype=np.int64)
        child_indices = np.asarray(pair.alignment.child_indices[span], dtype=np.int64)
        if len(parent_indices) < 3 or len(child_indices) != len(parent_indices):
            raise ValueError("owner-produced heading span needs at least three matched rows")
        oriented = self._current_oriented()
        parent_node = str(payload["parent_node"])
        child_node = str(payload["child_node"])
        parent_gyro = np.asarray(pair.parent_gyro[span], dtype=float)
        child_gyro = np.asarray(pair.child_gyro[span], dtype=float)
        parent_quaternion = np.asarray(
            oriented.quat_world_sensor_wxyz_by_node[parent_node][parent_indices], dtype=float,
        )
        child_quaternion = np.asarray(
            oriented.quat_world_sensor_wxyz_by_node[child_node][child_indices], dtype=float,
        )
        common_time = np.asarray(
            oriented.time_us_by_node[parent_node][parent_indices], dtype=float,
        ) * 1e-6
        owner_binding = {
            "schema": "biospur-c2-runtime-owned-heading-span-input-v1",
            "runtime_owner_id": self._runtime_owner_id,
            "runtime_owner_token": pair.provenance["runtime_owner_token"],
            "edge": edge,
            "chronological_index": int(self._current_index),
            "action": self._current_action,
            "span_index": int(span_index),
            "span_half_open_in_aligned_pair": [span.start, span.stop],
            "parent_node": parent_node,
            "child_node": child_node,
            "parent_gyro_sha256": self._array_sha256(parent_gyro),
            "child_gyro_sha256": self._array_sha256(child_gyro),
            "parent_quaternion_wxyz_sha256": self._array_sha256(parent_quaternion),
            "child_quaternion_wxyz_sha256": self._array_sha256(child_quaternion),
            "common_physical_time_s_sha256": self._array_sha256(common_time),
            "selected_source_row_indices_sha256": self._array_sha256(parent_indices),
            "source": "CURRENT_SEALED_ORIENTED_ACTION_PLUS_OWNER_PRODUCED_GAP_SAFE_ALIGNMENT",
            "future_episode_or_pose_truth_used": False,
        }
        result = self._heading_owner.process_span(
            branch_id=branch_id,
            edge=edge,
            chronological_index=self._current_index,
            action=self._current_action,
            parent_gyro_sensor=parent_gyro,
            child_gyro_sensor=child_gyro,
            parent_quaternion_world_sensor_wxyz=parent_quaternion,
            child_quaternion_world_sensor_wxyz=child_quaternion,
            common_physical_time_s=common_time,
            selected_source_row_indices=parent_indices,
            owner_input_binding=owner_binding,
            reset_requested=False,
            profile_stitch_requested=False,
        )
        self._current_heading_records.add((branch_id, edge))
        return result

    def _current_sealed_base_physical_time_s(self) -> np.ndarray:
        oriented = self._current_oriented()
        pelvis_rows = [
            row for row in self.settings["segment_frames"]["wear_authority"]["rows"]
            if row["body_segment"] == "pelvis"
        ]
        if len(pelvis_rows) != 1:
            raise ValueError("sealed wear/identity mapping must identify exactly one pelvis root node")
        root_node = str(pelvis_rows[0]["hardware_id"])
        root_time = np.asarray(oriented.time_us_by_node[root_node], dtype=np.int64)
        if len(root_time):
            return root_time.astype(float) * 1e-6
        if self._current_reference_time_s is None:
            raise RuntimeError("unusable root episode lacks its registered chronological reference time")
        # One carried-state display point is not a fabricated observation.
        return np.asarray([self._current_reference_time_s], dtype=float)

    def record_current_heading_no_update(
        self,
        *,
        branch_id: str,
        edge: str,
        cause: str,
        hard_support_token: str,
    ) -> Mapping[str, Any]:
        self._require(
            "record_current_heading_no_update",
            PipelineStage.CURRENT_EPISODE_FRAMES_UPDATED,
        )
        if self._heading_owner is None or self._current_index is None or self._current_action is None:
            raise RuntimeError("current prefix has no heading owner")
        self._validate_current_hard_support_token(branch_id, hard_support_token)
        branch_index = self._branch_ids.index(branch_id)
        if not self._branch_support[branch_index]:
            raise ValueError("heading no-update requested for an eliminated physical branch")
        result = self._heading_owner.record_action_no_update(
            branch_id=branch_id, edge=edge,
            chronological_index=self._current_index, action=self._current_action,
            base_common_physical_time_s=self._current_sealed_base_physical_time_s(), cause=cause,
        )
        self._current_heading_records.add((branch_id, edge))
        return result

    def finish_current_heading(self) -> Mapping[str, HeadingTrajectoryResult]:
        self._require(
            "finish_current_heading",
            PipelineStage.CURRENT_EPISODE_FRAMES_UPDATED,
        )
        assert self._current_index is not None and self._current_action is not None
        if self._heading_owner is None:
            if self._current_frame_branches:
                raise RuntimeError("frame branches exist without persistent heading owner")
            self._current_heading_trajectories = {}
            self._transition(
                PipelineStage.CURRENT_EPISODE_HEADING_UPDATED,
                cause="CURRENT_PREFIX_FUNCTIONAL_FRAMES_UNRESOLVED;NO_HEADING_EVIDENCE_INVENTED",
            )
            return {}
        supported = [
            branch_id for branch_id, keep in zip(self._branch_ids, self._branch_support) if keep
        ]
        if not self._current_qmt_observation_allowed:
            cause = "CURRENT_POSTERIOR_FUNCTIONAL_FRAMES_UNRESOLVED"
            for branch_id in supported:
                for edge, _, _ in EDGE_SPECS:
                    if (branch_id, edge) not in self._current_heading_records:
                        self._heading_owner.record_action_no_update(
                            branch_id=branch_id,
                            edge=edge,
                            chronological_index=self._current_index,
                            action=self._current_action,
                            base_common_physical_time_s=self._current_sealed_base_physical_time_s(),
                            cause=f"PRE_QMT_FUNCTIONAL_FRAME_LOCAL_NO_UPDATE:{cause}",
                        )
                        self._current_heading_records.add((branch_id, edge))
        expected = {(branch_id, edge) for branch_id in supported for edge, _, _ in EDGE_SPECS}
        if self._current_heading_records != expected:
            raise RuntimeError("every supported branch/edge must record QMT evidence or explicit local no-update")
        base_common_physical_time_s = self._current_sealed_base_physical_time_s()
        trajectories = {
            branch_id: self._heading_owner.assemble_action_rooted_trajectory(
                branch_id,
                chronological_index=self._current_index,
                action=self._current_action,
                base_common_physical_time_s=base_common_physical_time_s,
            ) for branch_id in supported
        }
        self._current_heading_trajectories = trajectories
        self._transition(
            PipelineStage.CURRENT_EPISODE_HEADING_UPDATED,
            cause="CURRENT_ACTION_NINE_EDGE_COMMON_GRID_PARENT_PLUS_CHILD_PROPAGATION_COMPLETE",
        )
        return trajectories

    def inject_transaction_failure_after_qmt(self) -> None:
        """Synthetic negative-gate hook; never used by the real runner."""

        self._require("inject_transaction_failure_after_qmt", PipelineStage.CURRENT_EPISODE_HEADING_UPDATED)
        raise RuntimeError("INJECTED_SYNTHETIC_FAILURE_AFTER_QMT")

    @staticmethod
    def _positive_information_projection(
        information: np.ndarray,
        *,
        relative_tolerance: float,
    ) -> np.ndarray:
        eigenvalues, eigenvectors = np.linalg.eigh(0.5 * (information + information.T))
        maximum = max(float(np.max(eigenvalues)), 0.0)
        keep = (
            (eigenvalues >= maximum * relative_tolerance) & (eigenvalues > 0.0)
            if maximum > 0.0 else np.zeros(len(eigenvalues), dtype=bool)
        )
        return eigenvectors[:, keep]

    def _assemble_owner_progressive_inputs(self) -> dict[str, Any]:
        if self._current_prediction is None or self._current_index is None or self._current_action is None:
            raise RuntimeError("owner-bound progressive assembly requires an open scored episode")
        dimension = self._progressive_dimension
        observation = self._current_prediction.prior_mean.copy()
        covariance = np.zeros((dimension, dimension), dtype=float)
        information = np.zeros((dimension, dimension), dtype=float)
        authoritative_mean = self._current_prediction.prior_mean.copy()
        authoritative_measurement = np.zeros((dimension, dimension), dtype=float)
        authoritative_migration = np.zeros((dimension, dimension), dtype=float)
        authoritative_systematic = np.zeros((dimension, dimension), dtype=float)
        projection_columns: list[np.ndarray] = []
        block_audit: list[dict[str, Any]] = []
        posterior_centers = self._geometry_owner.posterior_centers()
        posterior_axes = self._geometry_owner.posterior_axes()
        center_scale = float(self.settings["progressive"]["normalization"]["center_coordinate_scale_m"])
        axis_scale = float(self.settings["progressive"]["normalization"]["axis_tangent_scale_rad"])
        initial_variance = float(self.settings["progressive"]["initial_sigma"]) ** 2
        if center_scale <= 0.0 or axis_scale <= 0.0:
            raise ValueError("progressive normalization scales must be positive")
        offset = 0
        for row in self._progressive_layout:
            width = int(row["dimension"])
            block = slice(offset, offset + width)
            edge = str(row["edge"])
            local_projection = np.empty((width, 0), dtype=float)
            decision_kind = "CENTER" if row["kind"] == "CENTER" else "AXIS"
            decision = self._geometry_factor_decisions.get((decision_kind, edge))
            authoritative_source = "REGISTERED_BROAD_INITIAL_UNOBSERVED_PRIOR"
            if row["kind"] == "CENTER" and edge in posterior_centers:
                posterior = posterior_centers[edge]
                posterior_report = posterior.report
                authoritative_mean[block] = np.r_[
                    posterior.joint_to_parent_sensor_m,
                    posterior.joint_to_child_sensor_m,
                ] / center_scale
                authoritative_measurement[block, block] = np.asarray(
                    posterior_report["measurement_statistical_covariance_m2"], dtype=float,
                ) / center_scale**2
                authoritative_migration[block, block] = np.asarray(
                    posterior_report["temporal_migration_covariance_m2"], dtype=float,
                ) / center_scale**2
                authoritative_systematic[block, block] = np.asarray(
                    posterior_report["systematic_shared_model_covariance_m2"], dtype=float,
                ) / center_scale**2
                authoritative_source = "PERSISTENT_CENTER_OWNER_STATISTICAL_MIGRATION_SYSTEMATIC_DECOMPOSITION"
            elif row["kind"] == "AXIS_PRODUCT_S2_TANGENT" and edge in posterior_axes:
                posterior_value, measurement_component, migration_component, systematic_component, valid = (
                    self._geometry_owner.posterior_axis_in_fixed_gauge(edge)
                )
                authoritative_mean[block] = posterior_value / axis_scale
                authoritative_measurement[block, block] = measurement_component / axis_scale**2
                authoritative_migration[block, block] = migration_component / axis_scale**2
                authoritative_systematic[block, block] = systematic_component / axis_scale**2
                authoritative_source = (
                    "PERSISTENT_PRODUCT_S2_OWNER_STATISTICAL_MIGRATION_SYSTEMATIC_DECOMPOSITION"
                    if valid else "PERSISTENT_PRODUCT_S2_OWNER_ANTIPODAL_BROAD_UNCERTAINTY_NO_POINT_UPDATE"
                )
            else:
                authoritative_measurement[block, block] = np.eye(width) * initial_variance
            if row["kind"] == "CENTER" and edge in self._accepted_local_centers:
                estimate = self._accepted_local_centers[edge]
                local_value = np.r_[estimate.joint_to_parent_sensor_m, estimate.joint_to_child_sensor_m]
                local_covariance = np.asarray(estimate.covariance_m2, dtype=float)
                local_information = np.asarray(
                    estimate.report["gauge_reduced_robust_bread_information_m2_inv"], dtype=float,
                )
                observation[block] = local_value / center_scale
                covariance[block, block] = local_covariance / center_scale**2
                information[block, block] = local_information * center_scale**2
                local_projection = self._positive_information_projection(
                    information[block, block],
                    relative_tolerance=float(self.settings["joint_center"]["relative_rank_tolerance"]),
                )
                source = "LOCAL_SEEL_ROBUST_BREAD_PLUS_SANDWICH_AND_SHARED_MODEL_PREDICTIVE_COVARIANCE"
            elif row["kind"] == "AXIS_PRODUCT_S2_TANGENT" and edge in self._accepted_local_axes:
                local_value, statistical, systematic, valid = self._geometry_owner.axis_observation_in_fixed_gauge(
                    self._accepted_local_axes[edge]
                )
                if valid:
                    local_statistical_information = np.linalg.pinv(
                        statistical,
                        rcond=float(self.settings["progressive"]["local_information_relative_tolerance"]),
                    )
                    observation[block] = local_value / axis_scale
                    covariance[block, block] = (statistical + systematic) / axis_scale**2
                    information[block, block] = local_statistical_information * axis_scale**2
                    local_projection = self._positive_information_projection(
                        information[block, block],
                        relative_tolerance=float(self.settings["progressive"]["local_information_relative_tolerance"]),
                    )
                    source = "LOCAL_PRODUCT_S2_TRANSPORTED_STATISTICAL_INFORMATION_PLUS_SHARED_MODEL_PREDICTIVE_COVARIANCE"
                else:
                    source = "ANTIPODAL_ILL_CONDITIONED_LOCAL_AXIS_NO_UPDATE"
            else:
                source = (
                    "GEOMETRY_OWNER_REJECTED_LOCAL_FACTOR_ZERO_INFORMATION"
                    if decision is not None else "CURRENT_EPISODE_FACTOR_ABSENT_ZERO_INFORMATION"
                )
            for column in range(local_projection.shape[1]):
                embedded = np.zeros(dimension, dtype=float)
                embedded[block] = local_projection[:, column]
                projection_columns.append(embedded)
            block_audit.append({
                "kind": row["kind"], "edge": edge,
                "normalized_coordinate_slice_half_open": [offset, offset + width],
                "owner_source": source,
                "owner_update_eligible": bool(decision and decision["owner_update_eligible"]),
                "owner_decision_reason": None if decision is None else decision["reason"],
                "owner_decision_source": None if decision is None else decision["decision_owner"],
                "observed_rank": int(local_projection.shape[1]),
                "authoritative_posterior_source": authoritative_source,
                "systematic_floor_added_to_repeated_data_information": False,
                "caller_supplied": False,
            })
            offset += width
        projection = (
            np.column_stack(projection_columns)
            if projection_columns else np.empty((dimension, 0), dtype=float)
        )
        branch_log_likelihood = np.zeros_like(self._branch_support, dtype=float)
        branch_evidence_audit: list[dict[str, Any]] = []
        wear_delta = np.zeros_like(branch_log_likelihood)
        if self._current_frame_branches and self._current_full_frame_geometry_ready:
            current_wear = self._frame_owner.wear_log_likelihood_vector()
            if current_wear.shape != branch_log_likelihood.shape or not np.isfinite(current_wear).all():
                raise RuntimeError("frame-owner wear likelihood differs from canonical branch support")
            wear_delta = current_wear - self._committed_wear_log_likelihood
            branch_log_likelihood += wear_delta
            branch_evidence_audit.append({
                "evidence_owner": "SegmentFrameBranchOwner",
                "evidence_type": "HASH_BOUND_BROAD_QUALITATIVE_WEAR_PRIOR_REPLACEMENT_DELTA",
                "first_wear_prior_commit": not self._wear_prior_initialized,
                "current_wear_log_likelihood": current_wear.tolist(),
                "previously_committed_wear_log_likelihood": self._committed_wear_log_likelihood.tolist(),
                "applied_log_likelihood_delta": wear_delta.tolist(),
                "same_wear_prior_repeated_as_independent_episode_evidence": False,
                "broad_wear_prior_added_as_irreducible_covariance": False,
            })
        elif self._current_frame_branches:
            branch_evidence_audit.append({
                "evidence_owner": "SegmentFrameBranchOwner",
                "evidence_type": "PARTIAL_UNRESOLVED_WEAR_QUADRATURE_SUPPRESSED",
                "complete_nine_edge_frame_geometry": False,
                "mature_functional_frame_wear_subset_scoring_implemented": False,
                "hard_wear_rejection_applied": False,
                "soft_wear_likelihood_delta_applied": False,
                "applied_log_likelihood_delta": wear_delta.tolist(),
                "unresolved_nominal_wear_quadrature_is_evidence": False,
            })
        if self._current_physical_gate_completed and self._current_physical_trajectory_assessments:
            if self._current_physical_soft_log_likelihood.shape != branch_log_likelihood.shape:
                raise RuntimeError("soft physical evidence differs from canonical branch order")
            branch_log_likelihood += self._current_physical_soft_log_likelihood
            for branch_index, branch_id in enumerate(self._branch_ids):
                assessment = self._current_physical_trajectory_assessments.get(branch_id)
                branch_evidence_audit.append({
                    "branch_id": branch_id,
                    "evidence_owner": "ScientificForwardKinematicsOwner.assess_prefix_trajectory",
                    "evidence_type": "POST_QMT_HARD_PHYSICAL_SUPPORT_PLUS_SOFT_UNCERTAINTY_AWARE_TOTAL",
                    "physically_legal": None if assessment is None else assessment.physically_legal,
                    "soft_physical_log_likelihood": float(
                        self._current_physical_soft_log_likelihood[branch_index]
                    ),
                    "rom_log_likelihood": (
                        None if assessment is None else float(assessment.rom_log_likelihood)
                    ),
                    "bilateral_log_likelihood": (
                        None if assessment is None else float(assessment.bilateral_log_likelihood)
                    ),
                    "gravity_log_likelihood": (
                        None if assessment is None else float(assessment.gravity_log_likelihood)
                    ),
                    "soft_total_added_exactly_once": True,
                    "official_qmt_rooted_trajectory_completed_before_hard_gates": True,
                    "caller_matrix_or_pose_truth_used": False,
                })
        if self._heading_owner is not None and self._branch_ids:
            for branch_index, branch_id in enumerate(self._branch_ids):
                if not self._branch_support[branch_index]:
                    branch_log_likelihood[branch_index] = -np.inf
                    branch_evidence_audit.append({
                        "branch_id": branch_id, "status": "TRAJECTORY_DERIVED_PHYSICAL_ELIMINATION",
                        "log_evidence": None,
                    })
                    continue
                evidence = dict(self._heading_owner.action_branch_log_evidence(
                    branch_id=branch_id, chronological_index=self._current_index,
                ))
                evidence["full_body_physical_gate_completed"] = bool(
                    self._current_physical_gate_completed
                )
                evidence["observed_edge_local_qmt_retained_when_full_body_legality_unknown"] = bool(
                    not self._current_physical_gate_completed
                )
                evidence["unobserved_nonhinge_support_counted_as_information"] = False
                branch_log_likelihood[branch_index] += float(evidence["log_evidence"])
                branch_evidence_audit.append(evidence)
        elif np.any(~self._branch_support):
            branch_log_likelihood[~self._branch_support] = -np.inf
        observed_rank = int(projection.shape[1])
        geometry_fraction = observed_rank / dimension
        if self._heading_owner is None or not self._branch_ids:
            heading_fraction = 0.0
        else:
            cap = int(self.settings["heading"]["branch_evidence"]["effective_epoch_cap_per_edge"])
            regular = sum(
                int(edge_row["regular_official_epoch_count"])
                for row in branch_evidence_audit if "edge_evidence" in row
                for edge_row in row["edge_evidence"].values()
            )
            denominator = max(1, int(np.count_nonzero(self._branch_support)) * len(EDGE_SPECS) * cap)
            heading_fraction = min(1.0, regular / denominator)
        physical_fraction = (
            float(np.count_nonzero(self._branch_support) / len(self._branch_support))
            if self._current_physical_gate_completed
            and self._current_physical_trajectory_assessments
            else 0.0
        )
        weights = self.settings["progressive"]["physical_validity_weights"]
        weight_values = np.asarray([
            float(weights["geometry_information_fraction"]),
            float(weights["official_heading_effective_support_fraction"]),
            float(weights["trajectory_legal_branch_fraction"]),
        ])
        if np.any(weight_values < 0.0) or not np.isclose(np.sum(weight_values), 1.0):
            raise ValueError("registered physical-validity component weights must be nonnegative and sum to one")
        physical_validity = float(
            weight_values[0] * geometry_fraction
            + weight_values[1] * heading_fraction
            + weight_values[2] * physical_fraction
        )
        if not 0.0 <= physical_validity <= 1.0:
            raise RuntimeError("registered owner-derived physical-validity formula left [0,1]")
        return {
            "observation": observation,
            "observation_covariance": covariance,
            "data_information": information,
            "observation_projection": projection,
            "branch_log_likelihood": branch_log_likelihood,
            "physical_validity": physical_validity,
            "authoritative_posterior_mean": authoritative_mean,
            "authoritative_measurement_covariance": authoritative_measurement,
            "authoritative_temporal_migration_covariance": authoritative_migration,
            "authoritative_shared_systematic_covariance": authoritative_systematic,
            "audit": {
                "schema": "biospur-c2-owner-bound-progressive-assembly-v1",
                "chronological_index": self._current_index,
                "action": self._current_action,
                "state_dimension": dimension,
                "normalized_state_layout": [dict(row) for row in self._progressive_layout],
                "observed_rank": observed_rank,
                "official_heading_effective_support_fraction": heading_fraction,
                "trajectory_legal_branch_fraction": (
                    physical_fraction
                    if self._current_physical_gate_completed else None
                ),
                "trajectory_legal_branch_fraction_status": (
                    "OBSERVED_POST_QMT_FULL_BODY_HARD_GATE"
                    if self._current_physical_gate_completed else
                    "UNKNOWN_CONSERVATIVE_ZERO_NUMERIC_CONTRIBUTION_NOT_HARD_REJECTION"
                ),
                "rank_zero_local_no_update": observed_rank == 0,
                "block_audit": block_audit,
                "branch_evidence": branch_evidence_audit,
                "physical_candidate_input_audit": deepcopy(self._current_physical_input_audit),
                "physical_soft_log_likelihood_applied": (
                    self._current_physical_soft_log_likelihood.tolist()
                ),
                "wear_prior_delta_applied": wear_delta.tolist(),
                "physical_validity_components": {
                    "geometry_information_fraction": geometry_fraction,
                    "official_heading_effective_support_fraction": heading_fraction,
                    "trajectory_legal_branch_fraction": physical_fraction,
                },
                "physical_validity": physical_validity,
                "authoritative_total_uncertainty_source": (
                    "PERSISTENT_GEOMETRY_OWNER_MEASUREMENT_PLUS_TEMPORAL_MIGRATION_PLUS_SHARED_SYSTEMATIC"
                ),
                "caller_supplied_scientific_progress_fields": False,
                "future_episode_factors_used": False,
            },
        }

    def commit_current_progressive(self) -> ProgressiveSnapshot:
        if self._stage is PipelineStage.CURRENT_EPISODE_HEADING_UPDATED:
            self.guard.reject_physical_gate_stage_bypass(
                "progressive commit requested before owner-derived post-QMT physical assessment"
            )
        self._require(
            "commit_current_progressive",
            PipelineStage.CURRENT_EPISODE_PHYSICAL_CANDIDATES_ASSESSED,
        )
        if self._progressive_owner is None or self._current_prediction is None:
            raise RuntimeError("current episode lacks prequential prediction")
        assert self._current_index is not None and self._current_action is not None
        assembled = self._assemble_owner_progressive_inputs()
        snapshot = self._progressive_owner.ingest_episode(
            chronological_index=self._current_index,
            action=self._current_action,
            observation=assembled["observation"],
            observation_covariance=assembled["observation_covariance"],
            data_information=assembled["data_information"],
            observation_projection=assembled["observation_projection"],
            authoritative_posterior_mean=assembled["authoritative_posterior_mean"],
            authoritative_measurement_covariance=assembled["authoritative_measurement_covariance"],
            authoritative_temporal_migration_covariance=assembled[
                "authoritative_temporal_migration_covariance"
            ],
            authoritative_shared_systematic_covariance=assembled[
                "authoritative_shared_systematic_covariance"
            ],
            branch_log_likelihood=assembled["branch_log_likelihood"],
            physical_validity=assembled["physical_validity"],
            prequential_prediction=self._current_prediction,
        )
        self._current_progressive_assembly = dict(assembled["audit"])
        self._progressive_assemblies.append(dict(assembled["audit"]))
        self._progress_snapshots.append(snapshot)
        self._episode_reference_time_audits.append(deepcopy(self._current_reference_time_audit))
        self._heading_trajectory_history[self._current_index] = deepcopy(
            self._current_heading_trajectories
        )
        committed_pairs: dict[str, AlignedPair] = {}
        for pair in self._current_owned_aligned_pairs.values():
            if pair.edge in committed_pairs:
                raise RuntimeError(
                    f"{pair.edge}: duplicate runtime-owned pair at causal commit"
                )
            self._validate_owned_aligned_pair(pair, expected_edge=pair.edge)
            committed_pairs[pair.edge] = deepcopy(pair)
        self._aligned_pair_history[self._current_index] = committed_pairs
        self._geometry_checkpoint_history[self._current_index] = {
            "action": self._current_action,
            "centers": deepcopy(self._geometry_owner.posterior_centers()),
            "axes": deepcopy(self._geometry_owner.posterior_axes()),
            "owner_authenticated": True,
            "future_episode_factors_used": False,
        }
        if self._current_physical_gate_completed:
            self._physical_trajectory_history[self._current_index] = {
                branch_id: {
                    "world_from_segment": deepcopy(
                        self._current_physical_world_from_segment[branch_id]
                    ),
                    "orientation_covariance": deepcopy(
                        self._current_physical_orientation_covariance[branch_id]
                    ),
                    "owner_binding": deepcopy(
                        self._current_physical_owner_bindings[branch_id]
                    ),
                    "frame_branch": deepcopy(
                        self._current_physical_frame_branches[branch_id]
                    ),
                    "assessment": deepcopy(
                        self._current_physical_trajectory_assessments[branch_id]
                    ),
                }
                for branch_id in self._current_physical_trajectory_assessments
                if branch_id in self._current_physical_world_from_segment
            }
        if self._current_frame_branches and self._current_full_frame_geometry_ready:
            self._committed_wear_log_likelihood = self._frame_owner.wear_log_likelihood_vector().copy()
            self._wear_prior_initialized = True
        if self._current_frame_branches:
            self._frame_owner.bind_authoritative_progressive_posterior(
                snapshot.branch_ids,
                snapshot.branch_weights,
                chronological_index=snapshot.chronological_index,
            )
        self._current_index = None
        self._current_action = None
        self._current_prediction = None
        self._transition(
            PipelineStage.CALIBRATION_EPISODE_READY,
            cause="CAUSAL_EPISODE_COMMITTED;READ_ONLY_PREFIX_SNAPSHOT_EMITTED",
        )
        return snapshot

    def scientific_forward(
        self,
        *,
        branch_id: str,
        root_sensor_position_m: np.ndarray,
        world_from_segment: Mapping[str, np.ndarray],
    ) -> ScientificFKResult:
        self._require(
            "scientific_forward",
            PipelineStage.CURRENT_EPISODE_PHYSICAL_CANDIDATES_ASSESSED,
            PipelineStage.CALIBRATION_EPISODE_READY,
            PipelineStage.FIT_FROZEN,
            PipelineStage.FINAL_RAW_FRESH_VERIFIED,
        )
        return self._fk_owner.forward(
            root_sensor_position_m=root_sensor_position_m,
            world_from_segment=world_from_segment,
            frame_branch=self._frame_owner.get(branch_id),
        )

    def authoritative_render_branch_selection(self) -> Mapping[str, Any]:
        """Expose the registered number of leading branches from the sole posterior vector."""

        if not self._progress_snapshots or not self._frame_owner.branches:
            raise RuntimeError("authoritative render branches require a committed functional-frame prefix")
        snapshot = self._progress_snapshots[-1]
        frame_audit = self._frame_owner.audit()
        frame_weights = np.asarray(frame_audit["posterior_weights"], dtype=float)
        if tuple(frame_audit["branch_ids"]) != snapshot.branch_ids or not np.array_equal(
            frame_weights, snapshot.branch_weights,
        ):
            raise RuntimeError("renderer observed a branch posterior source divergence")
        count = int(self.settings["progressive"]["renderer_branch_display_count"])
        if not 1 <= count <= len(snapshot.branch_ids):
            raise ValueError("registered renderer branch display count is invalid")
        order = sorted(
            range(len(snapshot.branch_ids)),
            key=lambda index: (-float(snapshot.branch_weights[index]), snapshot.branch_ids[index]),
        )
        selected = [index for index in order if bool(self._branch_support[index])][:count]
        if not selected:
            raise RuntimeError("renderer has no cumulatively hard-supported branch")
        return {
            "schema": "biospur-c2-authoritative-render-branch-selection-v1",
            "chronological_index": snapshot.chronological_index,
            "branch_ids": [snapshot.branch_ids[index] for index in selected],
            "branch_weights": [float(snapshot.branch_weights[index]) for index in selected],
            "all_posterior_branch_ids": list(snapshot.branch_ids),
            "all_posterior_branch_weights": snapshot.branch_weights.tolist(),
            "cumulative_hard_support_mask": self._branch_support.tolist(),
            "posterior_source": "PROGRESSIVE_CALIBRATION_STATE_IMMUTABLE_SNAPSHOT",
            "frame_owner_exact_match": True,
            "soft_zero_or_underflow_used_as_hard_candidate_lock": False,
            "caller_selected_branch": False,
        }

    def freeze_fit(self) -> None:
        self._require("freeze_fit", PipelineStage.CALIBRATION_EPISODE_READY)
        if self._progressive_owner is None or len(self._progress_snapshots) != 19:
            raise RuntimeError("fit freeze requires 19 causally committed sealed episodes")
        self._progressive_owner.freeze_fit()
        self._transition(PipelineStage.FIT_FROZEN, cause="FINAL_CAUSAL_PREFIX_FROZEN_BEFORE_FRESH_BATCH")

    def verify_synthetic_sufficient_stat_replay(self) -> Mapping[str, Any]:
        self._require("verify_synthetic_sufficient_stat_replay", PipelineStage.FIT_FROZEN)
        assert self._progressive_owner is not None
        return self._progressive_owner.fresh_recompute_and_compare()

    def _stable_input_access_binding(self) -> dict[str, Any]:
        chronology = tuple(self.settings["execution_contract"]["chronological_actions"])
        if len(self._input_access_audits) != len(chronology):
            raise RuntimeError("fresh verification requires 19 actual bounded raw-range access audits")
        stable_keys = (
            "action", "chronological_index", "payload_path", "requested_half_open_interval",
            "actual_read_intervals", "actual_read_union_bytes", "expected_read_bytes",
            "open_flags", "whole_file_stat_performed", "whole_file_hash_performed",
            "whole_file_traversal_performed", "mmap_used", "bounded_slice_sha256",
            "plan_path", "plan_sha256", "fit_freeze_heldout_interval",
            "heldout_bytes_touched", "opened_record_payload_classes", "other_record_payload_classes",
        )
        output: list[dict[str, Any]] = []
        reader_session_ids: set[str] = set()
        for index, (action, audit) in enumerate(zip(chronology, self._input_access_audits)):
            if "reader_session_id" not in audit or not str(audit["reader_session_id"]).startswith(
                "C2_PREFIT_READER_"
            ):
                raise RuntimeError("raw access audit lacks an immutable bounded-reader session ID")
            reader_session_ids.add(str(audit["reader_session_id"]))
            missing = [key for key in stable_keys if key not in audit]
            if missing:
                raise RuntimeError(f"raw access audit lacks stable fresh-gate fields: {missing}")
            row = {key: deepcopy(audit[key]) for key in stable_keys}
            if row["chronological_index"] != index or row["action"] != action:
                raise RuntimeError("raw access audit differs from sealed chronology")
            if bool(row["heldout_bytes_touched"]):
                raise RuntimeError("held-out bytes appeared in a pre-freeze/fresh raw binding")
            if int(row["actual_read_union_bytes"]) != int(row["expected_read_bytes"]):
                raise RuntimeError("raw access audit read union differs from exact planned range")
            output.append(row)
        if len(reader_session_ids) != 1:
            raise RuntimeError("one runtime must consume all 19 actions from exactly one bounded-reader session")
        return {
            "reader_session_id": next(iter(reader_session_ids)),
            "stable_action_access": output,
        }

    def _validate_distinct_fresh_access_bindings(
        self,
        fresh_runtime: "C2PipelineRuntime",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        primary_access = self._stable_input_access_binding()
        fresh_access = fresh_runtime._stable_input_access_binding()
        self.guard.validate_fresh_reader_sessions(
            str(primary_access["reader_session_id"]),
            str(fresh_access["reader_session_id"]),
        )
        return primary_access, fresh_access

    @staticmethod
    def _array_digest(value: np.ndarray) -> str:
        array = np.ascontiguousarray(np.asarray(value))
        header = json.dumps({"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True)
        return sha256(header.encode("utf-8") + array.tobytes()).hexdigest()

    def _causal_prefix_arrays(self) -> dict[str, np.ndarray]:
        arrays: dict[str, np.ndarray] = {}
        for index, by_branch in sorted(self._heading_trajectory_history.items()):
            for branch_id, trajectory in sorted(by_branch.items()):
                prefix = f"trajectories/{index:02d}/{branch_id}"
                arrays[f"{prefix}/common_physical_time_s"] = np.asarray(trajectory.common_physical_time_s)
                for group, values in (
                    ("edge_delta", trajectory.edge_delta_filt_rad),
                    ("edge_variance", trajectory.edge_variance_rad2),
                    ("edge_observed", trajectory.edge_direct_observation_mask),
                    ("segment_global_delta", trajectory.segment_global_delta_rad),
                    ("segment_global_variance", trajectory.segment_global_variance_rad2),
                ):
                    for name, value in sorted(values.items()):
                        arrays[f"{prefix}/{group}/{name}"] = np.asarray(value)
        for index, by_branch in sorted(self._physical_trajectory_history.items()):
            for branch_id, row in sorted(by_branch.items()):
                prefix = f"physical_trajectory/{index:02d}/{branch_id}"
                binding = row["owner_binding"]
                arrays[f"{prefix}/common_physical_time_s"] = np.asarray(
                    binding["base_common_physical_time_s"], dtype=float,
                )
                for segment, value in sorted(row["world_from_segment"].items()):
                    arrays[f"{prefix}/world_from_segment/{segment}"] = np.asarray(value)
                for segment, value in sorted(row["orientation_covariance"].items()):
                    arrays[f"{prefix}/orientation_covariance/{segment}"] = np.asarray(value)
                frame_branch = row["frame_branch"]
                for segment, value in sorted(frame_branch.segment_from_sensor.items()):
                    arrays[f"{prefix}/segment_from_sensor/{segment}"] = np.asarray(value)
                for edge, connection in sorted(frame_branch.connection_vectors_by_edge.items()):
                    arrays[f"{prefix}/connection/{edge}/parent"] = np.asarray(
                        connection.parent_sensor_to_joint_m
                    )
                    arrays[f"{prefix}/connection/{edge}/child"] = np.asarray(
                        connection.child_sensor_to_joint_m
                    )
                    arrays[f"{prefix}/connection/{edge}/covariance"] = np.asarray(
                        connection.covariance_m2
                    )
        for snapshot in self._progress_snapshots:
            prefix = f"progressive_prefix/{snapshot.chronological_index:02d}"
            for key in (
                "prequential_prior_mean", "prequential_prior_covariance",
                "prequential_prior_branch_weights", "data_information",
                "data_information_nonzero_eigenvalues", "posterior_mean", "posterior_covariance",
                "measurement_statistical_covariance", "temporal_migration_covariance",
                "shared_systematic_covariance", "branch_weights",
                "statistical_accumulator_mean", "statistical_accumulator_covariance",
            ):
                arrays[f"{prefix}/{key}"] = np.asarray(getattr(snapshot, key))
            arrays[f"{prefix}/prequential_and_information_scalars"] = np.asarray([
                snapshot.prediction_nll_before_ingest,
                float(snapshot.prequential_observed_rank),
                snapshot.information_logdet,
                float(snapshot.data_information_rank),
                0.0 if snapshot.data_information_pseudologdet is None else snapshot.data_information_pseudologdet,
                float(snapshot.data_information_pseudologdet is not None),
                snapshot.uncertainty_trace,
                snapshot.branch_concentration,
                snapshot.physical_validity,
            ])
        return arrays

    def _scientific_output_arrays(self) -> dict[str, np.ndarray]:
        if self._progressive_owner is None:
            raise RuntimeError("scientific output extraction requires the progressive owner")
        arrays: dict[str, np.ndarray] = {}
        registered_checkpoint_actions = set(
            str(value)
            for value in self.settings["scientific_renderer"]["checkpoint_actions"]
        )
        for oriented in self._oriented_actions:
            arrays.update(_owner_authenticated_orientation_replay_arrays(oriented))
        arrays.update(
            _owner_authenticated_aligned_pair_replay_arrays(
                self._aligned_pair_history
            )
        )
        for index, checkpoint in sorted(self._geometry_checkpoint_history.items()):
            if str(checkpoint["action"]) not in registered_checkpoint_actions:
                continue
            for edge, estimate in sorted(checkpoint["centers"].items()):
                prefix = f"geometry_checkpoint/{index:02d}/center/{edge}"
                arrays[f"{prefix}/mean"] = np.r_[
                    estimate.joint_to_parent_sensor_m,
                    estimate.joint_to_child_sensor_m,
                ]
                arrays[f"{prefix}/covariance"] = np.asarray(
                    estimate.covariance_m2, dtype=float,
                )
                latest = estimate.report.get("latest_local_estimate_report")
                if isinstance(latest, Mapping):
                    candidates = list(latest.get("online_candidate_branches", ()))
                    if candidates:
                        arrays[f"{prefix}/candidate_means"] = np.asarray([
                            [
                                *row["joint_to_parent_sensor_m"],
                                *row["joint_to_child_sensor_m"],
                            ]
                            for row in candidates
                        ], dtype=float)
                        arrays[f"{prefix}/candidate_weights"] = np.asarray([
                            row["weight"] for row in candidates
                        ], dtype=float)
            for edge, estimate in sorted(checkpoint["axes"].items()):
                prefix = f"geometry_checkpoint/{index:02d}/axis/{edge}"
                arrays[f"{prefix}/parent"] = np.asarray(
                    estimate.parent_axis_sensor, dtype=float,
                )
                arrays[f"{prefix}/child"] = np.asarray(
                    estimate.child_axis_sensor, dtype=float,
                )
                arrays[f"{prefix}/covariance"] = np.asarray(
                    estimate.tangent_covariance_rad2, dtype=float,
                )
        for edge, estimate in sorted(self._geometry_owner.posterior_centers().items()):
            prefix = f"geometry/center/{edge}"
            arrays[f"{prefix}/mean"] = np.r_[
                estimate.joint_to_parent_sensor_m, estimate.joint_to_child_sensor_m,
            ]
            arrays[f"{prefix}/total_covariance"] = np.asarray(estimate.covariance_m2)
            for key in (
                "measurement_statistical_covariance_m2",
                "temporal_migration_covariance_m2",
                "systematic_shared_model_covariance_m2",
            ):
                arrays[f"{prefix}/{key}"] = np.asarray(estimate.report[key])
            for component_name, component_covariance in sorted(
                estimate.report[
                    "systematic_shared_component_covariances_m2"
                ].items()
            ):
                arrays[
                    f"{prefix}/systematic_shared_component/{component_name}"
                ] = np.asarray(component_covariance)
        for edge, estimate in sorted(self._geometry_owner.posterior_axes().items()):
            prefix = f"geometry/axis/{edge}"
            arrays[f"{prefix}/parent"] = np.asarray(estimate.parent_axis_sensor)
            arrays[f"{prefix}/child"] = np.asarray(estimate.child_axis_sensor)
            arrays[f"{prefix}/total_covariance"] = np.asarray(estimate.tangent_covariance_rad2)
            for key in (
                "measurement_statistical_tangent_covariance_rad2",
                "temporal_migration_tangent_covariance_rad2",
                "total_systematic_tangent_covariance_rad2",
                "systematic_human_worn_tangent_covariance_rad2",
                "parent_tangent_basis_sensor",
                "child_tangent_basis_sensor",
            ):
                arrays[f"{prefix}/{key}"] = np.asarray(estimate.report[key])
            for component_name, component_covariance in sorted(
                estimate.report[
                    "systematic_component_tangent_covariances_rad2"
                ].items()
            ):
                arrays[
                    f"{prefix}/systematic_shared_component/{component_name}"
                ] = np.asarray(component_covariance)
        for branch in self._frame_owner.branches:
            prefix = f"frames/{branch.branch_id}"
            arrays[f"{prefix}/joint_frame_covariance"] = np.asarray(
                branch.joint_frame_tangent_covariance_rad2
            )
            for segment, rotation in sorted(branch.segment_from_sensor.items()):
                arrays[f"{prefix}/segment_from_sensor/{segment}"] = np.asarray(rotation)
                arrays[f"{prefix}/frame_covariance/{segment}"] = np.asarray(
                    branch.frame_tangent_covariance_rad2[segment]
                )
            for edge, connection in sorted(branch.connection_vectors_by_edge.items()):
                arrays[f"{prefix}/connection/{edge}/parent"] = np.asarray(
                    connection.parent_sensor_to_joint_m
                )
                arrays[f"{prefix}/connection/{edge}/child"] = np.asarray(
                    connection.child_sensor_to_joint_m
                )
                arrays[f"{prefix}/connection/{edge}/covariance"] = np.asarray(connection.covariance_m2)
            for edge, covariance in sorted(
                branch.paired_hinge_frame_tangent_covariance_rad2.items()
            ):
                arrays[f"{prefix}/paired_hinge_frame_covariance/{edge}"] = np.asarray(
                    covariance
                )
        arrays["frozen/branch_hard_support"] = np.asarray(self._branch_support, dtype=bool)
        arrays["frozen/branch_weights"] = np.asarray(
            self._progress_snapshots[-1].branch_weights, dtype=float,
        )
        if self._heading_owner is not None:
            heading_state = self._heading_owner.frozen_evaluation_state()
            for row in heading_state["edge_state"]:
                key = f"{row['branch_id']}:{row['edge']}"
                arrays[f"frozen/heading_edge_state/{key}"] = np.asarray([
                    float(row["delta_rad"]),
                    float(row["variance_rad2"]),
                    float(row["span_count"]),
                    float(row["observation_count"]),
                ])
        arrays.update(self._causal_prefix_arrays())
        progressive_checkpoint = self._progressive_owner.checkpoint()
        arrays["progressive/data_information"] = np.asarray(progressive_checkpoint["data_information"])
        return arrays

    def _scientific_output_structure(self) -> dict[str, Any]:
        return {
            "center_edges_and_endpoints": [
                [edge, estimate.parent, estimate.child]
                for edge, estimate in sorted(self._geometry_owner.posterior_centers().items())
            ],
            "axis_edges": sorted(self._geometry_owner.posterior_axes()),
            "owner_authenticated_orientation_checkpoint_support": {
                str(oriented.chronological_index): {
                    "action": oriented.action,
                    "hardware_nodes": sorted(
                        oriented.quat_world_sensor_wxyz_by_node
                    ),
                }
                for oriented in self._oriented_actions
                if oriented.action in set(
                    self.settings["scientific_renderer"]["checkpoint_actions"]
                )
            },
            "owner_authenticated_orientation_replay_support": {
                str(oriented.chronological_index): {
                    "action": oriented.action,
                    "hardware_nodes": sorted(
                        oriented.quat_world_sensor_wxyz_by_node
                    ),
                    "persisted_arrays_per_node": [
                        "time_us", "derived_boot_epoch", "contiguous_span_id",
                        "acc_mps2", "gyro_rads", "quat_world_sensor_wxyz",
                        "gap_only_covariance_rad2",
                    ],
                    "retrospective_heading_replay_only": True,
                    "causal_progressive_metrics_modified": False,
                }
                for oriented in self._oriented_actions
            },
            "owner_authenticated_aligned_pair_replay_support": {
                str(index): {
                    edge: {
                        "action": pair.action,
                        "parent_node": str(pair.provenance["parent_node"]),
                        "child_node": str(pair.provenance["child_node"]),
                        "row_count": int(len(pair.alignment.parent_indices)),
                        "contiguous_span_half_open": [
                            [span.start, span.stop]
                            for span in pair.contiguous_spans
                        ],
                        "owner": "functional_geometry.aligned_pair",
                        "runtime_capability_token_exported": False,
                        "realignment_during_export": False,
                    }
                    for edge, pair in sorted(by_edge.items())
                }
                for index, by_edge in sorted(self._aligned_pair_history.items())
            },
            "owner_authenticated_geometry_checkpoint_support": {
                str(index): {
                    "action": str(row["action"]),
                    "center_edges": sorted(row["centers"]),
                    "axis_edges": sorted(row["axes"]),
                    "owner_authenticated": bool(row["owner_authenticated"]),
                }
                for index, row in sorted(self._geometry_checkpoint_history.items())
                if str(row["action"]) in set(
                    self.settings["scientific_renderer"]["checkpoint_actions"]
                )
            },
            "frame_branches": [
                {
                    "branch_id": branch.branch_id,
                    "axis_sign_by_edge": dict(branch.axis_sign_by_edge),
                    "segments": sorted(branch.segment_from_sensor),
                    "edges": sorted(branch.connection_vectors_by_edge),
                    "prior_weight": float(branch.prior_weight),
                    "wear_log_likelihood": float(branch.wear_log_likelihood),
                    "wear_profile_log_likelihood": dict(
                        branch.wear_profile_log_likelihood
                    ),
                    "wear_gross_wrong_hemisphere": bool(
                        branch.wear_gross_wrong_hemisphere
                    ),
                    "retained": bool(branch.retained),
                }
                for branch in self._frame_owner.branches
            ],
            "frozen_evaluation_authority": {
                "schema": "biospur-c2-frozen-heldout-owner-authority-v1",
                "branch_ids": list(self._branch_ids),
                "hard_support_mask": self._branch_support.tolist(),
                "pair_clock_checkpoint": self._clock_owner.checkpoint(),
                "heading_prior": self._heading_owner.frozen_evaluation_state()
                if self._heading_owner is not None else None,
                "calibration_owner_mutable_reference_exported": False,
                "heldout_threshold_override_allowed": False,
            },
            **self._causal_prefix_structure(),
        }

    def _causal_prefix_structure(self) -> dict[str, Any]:
        return {
            "progressive_prefixes": [
                {
                    "chronological_index": snapshot.chronological_index,
                    "action": snapshot.action,
                    "prequential_status": snapshot.prequential_status,
                    "prequential_observed_rank": snapshot.prequential_observed_rank,
                    "data_information_rank": snapshot.data_information_rank,
                    "branch_ids": list(snapshot.branch_ids),
                }
                for snapshot in self._progress_snapshots
            ],
            "heading_trajectory_support": {
                str(index): {
                    branch_id: {
                        "action": trajectory.action,
                        "chronological_index": trajectory.chronological_index,
                        "edge_ids": sorted(trajectory.edge_delta_filt_rad),
                        "segment_ids": sorted(trajectory.segment_global_delta_rad),
                    }
                    for branch_id, trajectory in sorted(by_branch.items())
                }
                for index, by_branch in sorted(self._heading_trajectory_history.items())
            },
            "physical_trajectory_support": {
                str(index): {
                    branch_id: {
                        "action": str(row["owner_binding"]["action"]),
                        "chronological_index": int(
                            row["owner_binding"]["chronological_index"]
                        ),
                        "segment_ids": sorted(row["world_from_segment"]),
                        "physically_legal": bool(row["assessment"].physically_legal),
                        "soft_total_log_likelihood": float(
                            row["assessment"].soft_total_log_likelihood
                        ),
                        "source": row["owner_binding"]["source"],
                        "runtime_owner_token_or_identity_entered_fresh_comparison": False,
                    }
                    for branch_id, row in sorted(by_branch.items())
                }
                for index, by_branch in sorted(self._physical_trajectory_history.items())
            },
        }

    @staticmethod
    def _compare_named_arrays(
        primary: Mapping[str, np.ndarray],
        fresh: Mapping[str, np.ndarray],
        *,
        atol: float,
        rtol: float,
    ) -> tuple[bool, dict[str, bool], bool]:
        keys_equal = set(primary) == set(fresh)
        comparisons: dict[str, bool] = {}
        for name in sorted(set(primary) | set(fresh)):
            if name not in primary or name not in fresh:
                comparisons[name] = False
                continue
            left = np.asarray(primary[name])
            right = np.asarray(fresh[name])
            if left.dtype == bool or right.dtype == bool:
                comparisons[name] = left.shape == right.shape and np.array_equal(left, right)
            else:
                comparisons[name] = left.shape == right.shape and np.allclose(
                    left, right, atol=atol, rtol=rtol, equal_nan=False,
                )
        complete_agreement = bool(
            keys_equal and comparisons and all(comparisons.values())
        )
        return keys_equal, comparisons, complete_agreement

    def _validate_causal_prefix_fresh_runtime(
        self,
        fresh_runtime: "C2PipelineRuntime",
    ) -> Mapping[str, Any]:
        atol = float(self.settings["progressive"]["fresh_absolute_tolerance"])
        rtol = float(self.settings["progressive"]["fresh_relative_tolerance"])
        primary_arrays = self._causal_prefix_arrays()
        fresh_arrays = fresh_runtime._causal_prefix_arrays()
        keys_equal, array_comparisons, complete_array_agreement = self._compare_named_arrays(
            primary_arrays, fresh_arrays, atol=atol, rtol=rtol,
        )
        primary_structure = self._causal_prefix_structure()
        fresh_structure = fresh_runtime._causal_prefix_structure()
        structure_equal = self._canonical_state(primary_structure) == self._canonical_state(fresh_structure)
        passed = bool(complete_array_agreement and structure_equal)
        self.guard.validate_causal_prefix_fresh_agreement(
            all_prefixes_and_trajectories_match=passed,
        )
        return {
            "array_keys_equal": keys_equal,
            "structure_equal": structure_equal,
            "array_comparisons": array_comparisons,
            "pass": passed,
        }

    @staticmethod
    def _comparison_category(name: str) -> str:
        if name.startswith("geometry/"):
            return "geometry"
        if name.startswith("frames/"):
            return "segment_frames"
        if name.startswith("trajectories/"):
            return "heading_trajectories"
        if name.startswith("progressive_prefix/"):
            if name.endswith("/posterior_mean"):
                return "all_prefix_posterior_means"
            if name.endswith("/data_information") or name.endswith("/data_information_nonzero_eigenvalues"):
                return "all_prefix_information_and_rank"
            if name.endswith("/branch_weights") or name.endswith("/prequential_prior_branch_weights"):
                return "all_prefix_branch_posteriors"
            if "/prequential_" in name or name.endswith("/prequential_and_information_scalars"):
                return "all_prefix_prequential_predictions_and_scores"
            return "all_prefix_uncertainty_and_statistical_state"
        if name == "progressive/data_information":
            return "final_data_information"
        return "other_scientific_state"

    def verify_final_raw_fresh_runtime(self, fresh_runtime: "C2PipelineRuntime") -> Mapping[str, Any]:
        """Compare two actual frozen runtimes; no caller-attested booleans can unlock hold-out."""

        self._require("verify_final_raw_fresh_runtime", PipelineStage.FIT_FROZEN)
        if self.execution_role != "PRIMARY_CAUSAL":
            raise RuntimeError("only the primary causal runtime can own the final fresh gate")
        if not isinstance(fresh_runtime, C2PipelineRuntime) or fresh_runtime is self:
            raise ValueError("final raw fresh verification requires a distinct C2PipelineRuntime instance")
        if fresh_runtime.execution_role != "FRESH_RAW_RECOMPUTATION" or fresh_runtime._stage is not PipelineStage.FIT_FROZEN:
            raise ValueError("fresh runtime must independently reach FIT_FROZEN in FRESH_RAW_RECOMPUTATION role")
        settings_hash = sha256(json.dumps(
            self._canonical_state(self.settings), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        fresh_settings_hash = sha256(json.dumps(
            self._canonical_state(fresh_runtime.settings), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        primary_access, fresh_access = self._validate_distinct_fresh_access_bindings(fresh_runtime)
        causal_prefix_comparison = self._validate_causal_prefix_fresh_runtime(fresh_runtime)
        primary_access_hash = sha256(json.dumps(
            self._canonical_state(primary_access["stable_action_access"]), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        fresh_access_hash = sha256(json.dumps(
            self._canonical_state(fresh_access["stable_action_access"]), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        primary_arrays = self._scientific_output_arrays()
        fresh_arrays = fresh_runtime._scientific_output_arrays()
        primary_structure = self._scientific_output_structure()
        fresh_structure = fresh_runtime._scientific_output_structure()
        primary_structure_hash = sha256(json.dumps(
            self._canonical_state(primary_structure), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        fresh_structure_hash = sha256(json.dumps(
            self._canonical_state(fresh_structure), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        atol = float(self.settings["progressive"]["fresh_absolute_tolerance"])
        rtol = float(self.settings["progressive"]["fresh_relative_tolerance"])
        keys_equal, array_comparisons, complete_array_agreement = self._compare_named_arrays(
            primary_arrays, fresh_arrays, atol=atol, rtol=rtol,
        )
        categories = {
            category: bool(all(
                passed for name, passed in array_comparisons.items()
                if self._comparison_category(name) == category
            ))
            for category in {
                "geometry", "segment_frames", "heading_trajectories",
                "all_prefix_posterior_means", "all_prefix_information_and_rank",
                "all_prefix_branch_posteriors", "all_prefix_prequential_predictions_and_scores",
                "all_prefix_uncertainty_and_statistical_state", "final_data_information",
                "other_scientific_state",
            }
        }
        category_has_evidence = {
            category: any(self._comparison_category(name) == category for name in array_comparisons)
            for category in categories
        }
        categories = {
            category: bool(value and category_has_evidence[category])
            for category, value in categories.items()
        }
        comparisons = {
            "distinct_runtime_instances": True,
            "distinct_bounded_reader_sessions": (
                primary_access["reader_session_id"] != fresh_access["reader_session_id"]
            ),
            "exact_registered_settings_hash": settings_hash == fresh_settings_hash,
            "exact_actual_raw_range_access_binding": primary_access_hash == fresh_access_hash,
            "exact_scientific_array_key_closure": keys_equal,
            "all_scientific_named_arrays": complete_array_agreement,
            "all_causal_prefixes_and_supported_heading_trajectories": bool(
                causal_prefix_comparison["pass"]
            ),
            "exact_causal_prefix_and_trajectory_support_structure": (
                primary_structure_hash == fresh_structure_hash
            ),
            **categories,
        }
        self._final_raw_fresh_verification = {
            "schema": "biospur-c2-final-raw-independent-frozen-owner-comparison-v1",
            "scope": "FINAL_RAW_RANGE_FULL_FROZEN_PIPELINE",
            "primary_execution_role": self.execution_role,
            "fresh_execution_role": fresh_runtime.execution_role,
            "primary_runtime_identity": id(self),
            "fresh_runtime_identity": id(fresh_runtime),
            "registered_settings_sha256": settings_hash,
            "fresh_registered_settings_sha256": fresh_settings_hash,
            "primary_actual_access_binding_sha256": primary_access_hash,
            "fresh_actual_access_binding_sha256": fresh_access_hash,
            "primary_reader_session_id": primary_access["reader_session_id"],
            "fresh_reader_session_id": fresh_access["reader_session_id"],
            "primary_array_sha256": {
                name: self._array_digest(value) for name, value in sorted(primary_arrays.items())
            },
            "fresh_array_sha256": {
                name: self._array_digest(value) for name, value in sorted(fresh_arrays.items())
            },
            "primary_scientific_structure_sha256": primary_structure_hash,
            "fresh_scientific_structure_sha256": fresh_structure_hash,
            "array_allclose": array_comparisons,
            "causal_prefix_comparison": causal_prefix_comparison,
            "comparisons": comparisons,
            "caller_attested_scientific_booleans_consumed": False,
            "pass": bool(all(comparisons.values())),
        }
        if not self._final_raw_fresh_verification["pass"]:
            raise ValueError("independent final raw frozen-owner recomputation disagrees with primary fit")
        self._transition(PipelineStage.FINAL_RAW_FRESH_VERIFIED, cause="INDEPENDENT_RAW_RANGE_FULL_PIPELINE_AGREEMENT")
        return deepcopy(self._final_raw_fresh_verification)

    def export_frozen_scientific_state(self) -> Mapping[str, Any]:
        """Return copy-only frozen arrays for a qualified renderer/evaluator."""

        self._require(
            "export_frozen_scientific_state",
            PipelineStage.FIT_FROZEN,
            PipelineStage.FINAL_RAW_FRESH_VERIFIED,
            PipelineStage.HOLDOUT_OPEN,
        )
        return {
            "schema": "biospur-c2-frozen-scientific-state-export-v1",
            "execution_role": self.execution_role,
            "stage": self._stage.value,
            "settings_semantic_sha256": self._settings_semantic_sha256,
            "arrays": {
                name: np.asarray(value).copy()
                for name, value in self._scientific_output_arrays().items()
            },
            "structure": deepcopy(self._scientific_output_structure()),
            "mutable_owner_reference_exported": False,
            "anthropometric_fit_or_viewer_rebase_present": False,
        }

    def record_final_raw_fresh_verification(self, *_: Any, **__: Any) -> None:
        self.guard.reject_caller_attested_fresh_unlock()

    def open_holdout(self) -> None:
        self._require("open_holdout", PipelineStage.FINAL_RAW_FRESH_VERIFIED)
        assert self._progressive_owner is not None
        self._progressive_owner.open_holdout()
        self._transition(PipelineStage.HOLDOUT_OPEN, cause="FIT_FROZEN_AND_FINAL_RAW_FRESH_BATCH_VERIFIED")

    def request_refit(self) -> None:
        self._require(
            "request_refit", PipelineStage.CALIBRATION_EPISODE_READY,
            PipelineStage.FIT_FROZEN, PipelineStage.FINAL_RAW_FRESH_VERIFIED,
            PipelineStage.HOLDOUT_OPEN,
        )
        if self._progressive_owner is None:
            raise RuntimeError("progressive owner not initialized")
        self._progressive_owner.request_refit()

    def audit(self) -> dict[str, Any]:
        owner_guards = [
            id(self.guard), id(self._orientation_owner.execution_guard),
            id(self._frame_owner.execution_guard), id(self._fk_owner.execution_guard),
        ]
        if self._heading_owner is not None:
            owner_guards.append(id(self._heading_owner.execution_guard))
        if self._progressive_owner is not None:
            owner_guards.append(id(self._progressive_owner.execution_guard))
        return {
            "schema": "biospur-c2-causal-episode-pipeline-runtime-v3",
            "execution_role": self.execution_role,
            "settings_semantic_sha256": self._settings_semantic_sha256,
            "initial_stochastic_state_semantic_sha256": self._initial_stochastic_state_semantic_sha256,
            "prefit_registry_seal_authority": dict(self._prefit_registry_seal_authority),
            "real_fit_activation_authority": (
                None if self._real_fit_activation_authority is None
                else dict(self._real_fit_activation_authority)
            ),
            "stage": self._stage.value,
            "stage_events": list(self._stage_events),
            "mandatory_intra_episode_order": [
                "PREFIX_I_MINUS_1_PREQUENTIAL_SCORE",
                "CURRENT_EPISODE_AXES_AND_CAUSAL_SAME_EDGE_CENTER_PREFIX",
                "PERSISTENT_GEOMETRY", "SO3_BRANCH_POSTERIOR", "OFFICIAL_QMT_AND_ROOTED_TREE",
                "QMT_CORRECTED_PHYSICAL_CANDIDATE_GATE_AND_SOFT_ROM",
                "PROGRESSIVE_COMMIT_AND_READ_ONLY_SNAPSHOT",
            ],
            "orientation_episode_count": len(self._oriented_actions),
            "bounded_input_access_audit_count": len(self._input_access_audits),
            "causal_progressive_snapshot_count": len(self._progress_snapshots),
            "heading_trajectory_history_episode_indices": sorted(self._heading_trajectory_history),
            "aligned_pair_history_episode_indices": sorted(
                self._aligned_pair_history
            ),
            "physical_trajectory_history_episode_indices": sorted(
                self._physical_trajectory_history
            ),
            "owner_derived_geometry_reference_time_audits": deepcopy(
                self._episode_reference_time_audits
            ),
            "progressive_state_dimension": self._progressive_dimension,
            "progressive_state_layout": [dict(row) for row in self._progressive_layout],
            "owner_bound_progressive_assemblies": list(self._progressive_assemblies),
            "transaction_events": deepcopy(self._transaction_events),
            "calibration_episode_transaction_required": True,
            "partial_owner_state_survives_ordinary_failure": False,
            "authoritative_branch_ids": list(self._branch_ids),
            "authoritative_branch_weights": (
                [] if not self._progress_snapshots else self._progress_snapshots[-1].branch_weights.tolist()
            ),
            "frame_owner_branch_weights_match_latest_progressive_snapshot": bool(
                not self._progress_snapshots or not self._frame_owner.branches
                or np.array_equal(
                    np.asarray(self._frame_owner.audit()["posterior_weights"]),
                    self._progress_snapshots[-1].branch_weights,
                )
            ),
            "final_raw_fresh_verification": deepcopy(self._final_raw_fresh_verification),
            "caller_attested_fresh_unlock_available": False,
            "caller_supplied_scientific_progress_fields": False,
            "future_episode_factor_reuse": False,
            "prefix_backfill_or_refit": False,
            "geometry_audit": self._geometry_owner.audit(),
            "causal_center_prefix_audit": self._center_prefix_owner.audit(),
            "frame_audit": self._frame_owner.audit(),
            "scientific_fk_audit": self._fk_owner.audit(),
            "heading_audit": None if self._heading_owner is None else self._heading_owner.audit(),
            "one_execution_guard_instance": True,
            "owner_guard_identities": owner_guards,
            "all_owner_guard_identities_equal": len(set(owner_guards)) == 1,
            "guard_audit": self.guard.audit(),
        }
