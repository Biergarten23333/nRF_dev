#!/usr/bin/env python3
"""Pre-register and execute the leakage-proof O2 full body-shadow study.

``preflight`` is metadata/synthetic only.  ``full`` is intentionally guarded
by a complete preflight seal and is not authorized merely because this tool
exists.  The full path reuses the exact O2-PERF2 causal feature and held-link
owners; this file adds columnar evidence and the frozen statistical owner.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
import multiprocessing
import os

for _thread_variable in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
):
    os.environ[_thread_variable] = "1"

from pathlib import Path
import resource
import time
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_coupled_progressive.contracts import (
    EPISODES, NODE_TO_SEGMENT, ROOT,
)
from biospur_fusion.c2_uwb_calibration.body_shadow_study import (
    BOOTSTRAP_BLOCK_EPOCHS,
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
    DESIGN_CONDITION_MAXIMUM,
    EVALUATION_CONTRACT,
    FULL_ROW_DTYPE,
    MODEL_CONTRACT,
    PAIR_MINIMUM_TRAIN_ACTIONS,
    PAIR_MINIMUM_TRAIN_BLOCKS,
    PAIR_MINIMUM_TRAIN_ROWS,
    PAIR_ORDER,
    eligibility_gate,
    evaluate_full_study,
    exposure_cluster_gate,
    fit_nested_models,
    freeze_common_design,
    pair_coverage_gate,
    residualized_shadow_audit,
)
from biospur_fusion.c2_uwb_calibration.causal_body_shadow_validation import (
    NESTED_MODEL_CONTRACT,
    NodeLinkClock,
    TRAIN_ACTIONS,
    VALIDATION_ACTIONS,
    causal_shadow_features,
    common_nuisance_values,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import frozen_world_alignment
from biospur_fusion.c2_uwb_calibration.shared_root import SharedRangeLink, solve_shared_root
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    DATASET as CALIBRATION_DATASET,
    PHYSICAL_DIRECTORY, _beacon_boundary_bridges, _clock_models,
)
from run_c2_causal_body_shadow_o2_pre import (
    ACCEPTED_RESULT,
    BASE_REPORT,
    CLOCK_TABLE,
    FRONTEND_ARCHIVE,
    FRONTEND_MANIFEST,
    PERF2_MAX_WORKERS,
    PELVIS_NODE,
    THREAD_LIMIT_ENVIRONMENT,
    _PoseProvider,
    _accelerated_transport_crc,
    _directory_bytes,
    _parallel_held_labels,
    _seal,
    _sha256,
    _verified_inputs,
    _verify_sealed_directory,
    _write_json,
)


FULL_HARD_S = 2_700.0
FULL_INTERNAL_STOP_S = 2_680.0
FULL_DISK_CAP_BYTES = 400_000_000
FULL_RSS_CAP_KB = 1_500_000
FULL_ROW_CAP = 500_000
FULL_EXTRACTION_PROJECTION_S = 2_215.077
PERF2_PILOT_ROWS_SHA256 = "cfcedb3653f12279b23571c8da87303f01a481779b6d32401da7231e5d1e8bb4"
QUALIFICATION_CEILING = "PREDICTIVE_UTILITY_QUALIFIED"
PERF2_CAUSAL_MODULE_SHA256 = "ab7ede59634a75cfcc3b9b65313b0f8029f2471f9ba1a6847a755c146dfe29df"
FULL_PROJECTION_MARGIN = 1.25
STAT_BENCHMARK_ROWS = 30_400
STAT_BENCHMARK_REPLICATES = BOOTSTRAP_REPLICATES
ACTION_ORDER = tuple(EPISODES)
NODE_ORDER = tuple(sorted(NODE_TO_SEGMENT))
NODE_INDEX = MappingProxyType({name: index for index, name in enumerate(NODE_ORDER)})
FORMAL_CAPTURE_MANIFEST = ROOT / (
    "logs/c2_imu_19plus2_formal_freeze_20260901_082039/"
    "FORMAL_FREEZE_MANIFEST.json"
)
POST_EXTRACTION_RESERVE_S = 300.0
EXTRACTION_STOP_S = FULL_HARD_S - POST_EXTRACTION_RESERVE_S
FINALIZATION_DISK_RESERVE_BYTES = 20_000_000
if ACTION_ORDER != tuple(TRAIN_ACTIONS) + tuple(VALIDATION_ACTIONS):
    raise RuntimeError("O2 full action split differs from the canonical C2 order")
if len(ACTION_ORDER) != 19 or len(TRAIN_ACTIONS) != 11 or len(VALIDATION_ACTIONS) != 8:
    raise RuntimeError("O2 full split cardinality changed")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _metadata_input_hashes() -> Mapping[str, str]:
    """Hash only the already accepted 00--19 owners; never H01/H02."""

    accepted_result = json.loads(ACCEPTED_RESULT.read_text(encoding="utf-8"))
    base_report = json.loads(BASE_REPORT.read_text(encoding="utf-8"))
    paths = (
        ACCEPTED_RESULT,
        ROOT / accepted_result["calibration_trajectory"]["path"],
        BASE_REPORT,
        ROOT / base_report["trajectory"]["path"],
        CLOCK_TABLE,
        FRONTEND_ARCHIVE,
        FRONTEND_MANIFEST,
        FORMAL_CAPTURE_MANIFEST,
    )
    forbidden = ("H01", "H02", "h01", "h02")
    if any(any(token in str(path) for token in forbidden) for path in paths):
        raise RuntimeError("O2 full metadata manifest attempted to include H01/H02")
    return MappingProxyType({str(path.relative_to(ROOT)): _sha256(path) for path in paths})


def _frozen_calibration_capture_bindings() -> Mapping[str, Any]:
    """Select only canonical 00--19 members from the sealed 19+2 manifest."""

    document = json.loads(FORMAL_CAPTURE_MANIFEST.read_text(encoding="utf-8"))
    payloads = [
        row for row in document.get("canonical_payload", [])
        if str(row.get("path", "")).endswith("fusion_host_raw.cobs.bin")
    ]
    if len(payloads) != 1:
        raise RuntimeError("formal freeze does not own exactly one canonical raw payload")
    payload = payloads[0]
    action_rows: dict[str, Any] = {}
    expected_paths: dict[str, str] = {
        str(payload["path"]): str(payload["sha256"]),
    }
    metadata = document.get("capture_metadata", [])
    for action in ACTION_ORDER:
        physical_action = PHYSICAL_DIRECTORY[action]
        token = f"/actions/{physical_action}/"
        selected = [row for row in metadata if token in f"/{row.get('path', '')}"]
        if len(selected) != 3:
            raise RuntimeError(f"formal freeze action metadata is incomplete: {action}")
        range_manifests = [
            row for row in selected
            if str(row["path"]).endswith("/manifest/CONTINUOUS_RANGE.json")
        ]
        if len(range_manifests) != 1:
            raise RuntimeError(f"formal freeze has no unique range slice owner: {action}")
        range_manifest_row = range_manifests[0]
        range_manifest_path = ROOT / str(range_manifest_row["path"])
        range_manifest = json.loads(range_manifest_path.read_text(encoding="utf-8"))
        if range_manifest.get("schema") != "biospur-continuous-range-v1":
            raise RuntimeError(f"range slice owner schema changed: {action}")
        decoded_slice_path = (
            CALIBRATION_DATASET / "actions" / physical_action
            / "rep_01/raw/fusion_host_raw.cobs.bin"
        )
        expected_slice_path = range_manifest_path.parent.parent / (
            "raw/fusion_host_raw.cobs.bin"
        )
        if decoded_slice_path.resolve() != expected_slice_path.resolve():
            raise RuntimeError(f"range slice path differs from decoder owner: {action}")
        decoded_slice_relative = str(decoded_slice_path.relative_to(ROOT))
        slice_sha256 = str(range_manifest["slice_sha256"])
        if len(slice_sha256) != 64 or int(range_manifest["slice_bytes"]) <= 0:
            raise RuntimeError(f"range slice binding is malformed: {action}")
        action_rows[action] = {
            "physical_action_directory": physical_action,
            "canonical_raw_payload_path": str(payload["path"]),
            "canonical_raw_payload_sha256": str(payload["sha256"]),
            "decoded_slice_path": decoded_slice_relative,
            "decoded_slice_sha256": slice_sha256,
            "decoded_slice_bytes": int(range_manifest["slice_bytes"]),
            "decoder_path_owner": (
                "evaluate_c2_pair_bias_gate._load_episode via "
                "c2_uwb_root_world.run_calibration.DATASET/PHYSICAL_DIRECTORY"
            ),
            "metadata": [
                {"path": str(row["path"]), "sha256": str(row["sha256"])}
                for row in selected
            ],
        }
        expected_paths[decoded_slice_relative] = slice_sha256
        for row in selected:
            expected_paths[str(row["path"])] = str(row["sha256"])
    if set(action_rows) != set(ACTION_ORDER):
        raise RuntimeError("formal freeze action set differs from O2 full")
    return MappingProxyType({
        "formal_manifest_path": str(FORMAL_CAPTURE_MANIFEST.relative_to(ROOT)),
        "formal_manifest_sha256": _sha256(FORMAL_CAPTURE_MANIFEST),
        "actions": MappingProxyType(action_rows),
        "full_predecode_expected_hashes": MappingProxyType(expected_paths),
        "H01_H02_members_selected": False,
    })


def _verify_full_allowlist(preflight: Path) -> Mapping[str, Any]:
    """Verify every sealed source/input owner before the first raw decode."""

    allowlist = json.loads((preflight / "ALLOWLIST.json").read_text(encoding="utf-8"))
    checked = 0
    for section in (
        "source_hashes", "input_hashes", "full_predecode_expected_hashes"
    ):
        for relative, expected in allowlist[section].items():
            if any(token in relative for token in ("H01", "H02", "h01", "h02")):
                raise RuntimeError("full allowlist selected a forbidden H01/H02 path")
            path = ROOT / relative
            if _sha256(path) != expected:
                raise RuntimeError(f"O2 full allowlist hash mismatch: {relative}")
            checked += 1
    return MappingProxyType({
        "verified_entries": checked,
        "verified_before_raw_decode": True,
        "H01_H02_opened_or_hashed": False,
    })


def _synthetic_rows() -> np.ndarray:
    """Fixed outcome-independent resource fixture; never a scientific fit."""

    repetitions = STAT_BENCHMARK_ROWS // (19 * len(PAIR_ORDER))
    count = 19 * len(PAIR_ORDER) * repetitions
    rows = np.zeros(count, dtype=FULL_ROW_DTYPE)
    rng = np.random.default_rng(701)
    cursor = 0
    for action in range(19):
        for repetition in range(repetitions):
            for pair in range(len(PAIR_ORDER)):
                node, anchor = divmod(pair, 8)
                phase = 0.13 * action + 0.17 * repetition + 0.031 * pair
                own = 0.5 + 0.45 * math.sin(phase)
                torso = 0.5 + 0.45 * math.cos(1.7 * phase + 0.2)
                limb = 0.5 + 0.45 * math.sin(2.3 * phase - 0.4)
                predicted = 2.5 + 0.015 * pair + 0.08 * rng.normal()
                xyz = rng.normal(size=3)
                response = (
                    0.002 * ((pair % 9) - 4) + 0.03 * own
                    + 0.04 * torso + 0.05 * limb + 0.01 * rng.normal()
                )
                rows[cursor] = (
                    action, action * 1000 + repetition, node, anchor,
                    action * 1000 + repetition, 2.5, 1e12 + cursor,
                    predicted - response, response, 3, 10.0, 3, 11.0, 1,
                    own, torso, limb, predicted,
                    xyz[0], xyz[1], xyz[2], 0.1 + 0.01 * abs(rng.normal()),
                )
                cursor += 1
    return rows


def _resource_projection() -> Mapping[str, Any]:
    rows = _synthetic_rows()
    train = rows["action_index"] < len(TRAIN_ACTIONS)
    validation = ~train
    started = time.perf_counter()
    design = freeze_common_design(rows, train)
    residualized_shadow_audit(rows, train, design)
    fit = fit_nested_models(rows, train, design)
    fit_wall = time.perf_counter() - started
    evaluation_started = time.perf_counter()
    evaluate_full_study(
        rows, train, validation, design, fit,
        replicates=STAT_BENCHMARK_REPLICATES,
    )
    evaluation_wall = time.perf_counter() - evaluation_started
    row_scale = FULL_ROW_CAP / len(rows)
    projected_statistics_s = FULL_PROJECTION_MARGIN * (
        fit_wall * row_scale + evaluation_wall * row_scale
    )
    projected_total_s = FULL_EXTRACTION_PROJECTION_S + projected_statistics_s
    projected_rows_bytes = FULL_ROW_CAP * FULL_ROW_DTYPE.itemsize
    projected_evidence_bytes = 2 * projected_rows_bytes + 20_000_000
    return MappingProxyType({
        "fixture_rows": len(rows),
        "fixture_replicates": STAT_BENCHMARK_REPLICATES,
        "fit_and_design_wall_s": fit_wall,
        "evaluation_wall_s": evaluation_wall,
        "row_scale_to_cap": row_scale,
        "bootstrap_scale_to_contract": 1.0,
        "projection_margin": FULL_PROJECTION_MARGIN,
        "sealed_perf2_extraction_projection_s": FULL_EXTRACTION_PROJECTION_S,
        "projected_statistics_s": projected_statistics_s,
        "projected_total_s": projected_total_s,
        "runtime_gate_s": FULL_HARD_S,
        "projected_rows_bytes": projected_rows_bytes,
        "projected_evidence_bytes": projected_evidence_bytes,
        "disk_gate_bytes": FULL_DISK_CAP_BYTES,
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "pass": bool(
            projected_total_s <= FULL_HARD_S
            and projected_evidence_bytes <= FULL_DISK_CAP_BYTES
            and resource.getrusage(resource.RUSAGE_SELF).ru_maxrss <= FULL_RSS_CAP_KB
        ),
    })


def _source_hashes() -> Mapping[str, str]:
    paths = (
        ROOT / "src/biospur_fusion/c2_3a_kinematics/__init__.py",
        ROOT / "src/biospur_fusion/c2_3a_kinematics/interface.py",
        ROOT / "src/biospur_fusion/c2_3a_kinematics/provenance.py",
        ROOT / "src/biospur_fusion/c2_coupled_progressive/__init__.py",
        ROOT / "src/biospur_fusion/c2_coupled_progressive/contracts.py",
        ROOT / "src/biospur_fusion/c2_coupled_progressive/estimator.py",
        ROOT / "src/biospur_fusion/c2_coupled_progressive/frontend.py",
        ROOT / "src/biospur_fusion/c2_coupled_progressive/math_utils.py",
        ROOT / "src/biospur_fusion/c2_coupled_progressive/output_coordinates.py",
        ROOT / "src/biospur_fusion/c2_coupled_progressive/renderer.py",
        ROOT / "src/biospur_fusion/c2_uwb_root_world/__init__.py",
        ROOT / "src/biospur_fusion/c2_uwb_root_world/beacon_clock.py",
        ROOT / "src/biospur_fusion/c2_uwb_root_world/calibration.py",
        ROOT / "src/biospur_fusion/c2_uwb_root_world/run_calibration.py",
        ROOT / "src/biospur_fusion/c2_uwb_root_world/u0.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/causal_body_shadow_validation.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/body_shadow_study.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/shared_root.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/antenna_los.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/body_occlusion.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/frozen_body_proxy.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/pair_bias.py",
        ROOT / "src/biospur_fusion/ingest/events.py",
        ROOT / "src/biospur_fusion/ingest/v47.py",
        ROOT / "src/biospur_fusion/time/common_clock.py",
        ROOT / "src/biospur_fusion/uwb/__init__.py",
        ROOT / "src/biospur_fusion/uwb/canonical_t4.py",
        ROOT / "src/biospur_fusion/uwb/frontend.py",
        ROOT.parent / "B306_Part/tools/fusion_host_binary.py",
        ROOT / "tools/run_c2_causal_body_shadow_o2_pre.py",
        ROOT / "tools/evaluate_c2_pair_bias_gate.py",
        Path(__file__).resolve(),
        ROOT / "tests/test_c2_causal_body_shadow_validation.py",
        ROOT / "tests/test_c2_body_shadow_study.py",
        ROOT / "tests/test_c2_body_shadow_full_runner.py",
    )
    frozen_t4 = ROOT.parent / (
        "UWB_Part/2026-07-15-FREEZE/scripts/solvers/erlangen_deployment_v4io_t4/"
        "stage2_position_T4_pristine/biospur_tag_positioning_offline_solver"
    )
    paths += tuple(sorted(frozen_t4.glob("*.py")))
    if any("rf_shadow_field" in str(path) for path in paths):
        raise RuntimeError("rejected rf_shadow_field entered the O2 allowlist")
    if len(paths) != len({path.resolve() for path in paths}):
        raise RuntimeError("duplicate path in O2 source dependency allowlist")
    return MappingProxyType({os.path.relpath(path, ROOT): _sha256(path) for path in paths})


def _perf2_feature_owner_delta_audit() -> Mapping[str, Any]:
    """Prove PERF2 features survived the registered contract/cap-owner edits."""

    path = ROOT / "src/biospur_fusion/c2_uwb_calibration/causal_body_shadow_validation.py"
    current = path.read_bytes()
    current_text = (
        b'        "continuous columns centred and RMS-scaled using train rows only; "\n'
        b'        "the complete reference-coded design is then train-RMS-scaled before SVD/fit"\n'
    )
    perf2_text = (
        b'        "continuous columns centred and RMS-scaled using train rows only; "\n'
        b'        "reference-coded pair columns are unscaled"\n'
    )
    current_labeler_owner = b'''@dataclass(frozen=True)
class HeldRangeLabeler:
    """Same-node leave-one-anchor-out label owner for one range epoch."""

    anchors_m: Mapping[int, np.ndarray] | np.ndarray
    maximum_condition: float = MAXIMUM_LOO_CONDITION
    maximum_nfev: int = 75

    def __post_init__(self) -> None:
        anchors = np.asarray(self.anchors_m, dtype=float)
        if anchors.shape != (8, 3) or not np.all(np.isfinite(anchors)):
            raise ValueError("the exact eight-anchor C2 layout is required")
        condition = float(self.maximum_condition)
        if not math.isfinite(condition) or condition <= 0.0:
            raise ValueError("maximum condition must be finite and positive")
        maximum_nfev = self.maximum_nfev
        if (
            isinstance(maximum_nfev, (bool, np.bool_))
            or not isinstance(maximum_nfev, (int, np.integer))
            or int(maximum_nfev) <= 0
        ):
            raise ValueError("maximum_nfev must be a positive integer")
        frozen_anchors = anchors.copy()
        frozen_anchors.setflags(write=False)
        object.__setattr__(self, "anchors_m", frozen_anchors)
        object.__setattr__(self, "maximum_condition", condition)
        object.__setattr__(self, "maximum_nfev", int(maximum_nfev))
'''
    perf2_labeler_owner = b'''class HeldRangeLabeler:
    """Same-node leave-one-anchor-out label owner for one range epoch."""

    def __init__(
        self,
        *,
        anchors_m: Mapping[int, np.ndarray] | np.ndarray,
        maximum_condition: float = MAXIMUM_LOO_CONDITION,
    ) -> None:
        anchors = np.asarray(anchors_m, dtype=float)
        if anchors.shape != (8, 3) or not np.all(np.isfinite(anchors)):
            raise ValueError("the exact eight-anchor C2 layout is required")
        self.anchors_m = anchors.copy()
        self.anchors_m.setflags(write=False)
        self.maximum_condition = float(maximum_condition)
'''
    current_solve_owner = (
        b'            maximum_condition=self.maximum_condition,\n'
        b'            maximum_nfev=self.maximum_nfev,\n'
    )
    perf2_solve_owner = b'            maximum_condition=self.maximum_condition,\n'
    replacements = (
        (current_text, perf2_text),
        (current_labeler_owner, perf2_labeler_owner),
        (current_solve_owner, perf2_solve_owner),
    )
    reconstructed = current
    for current_span, perf2_span in replacements:
        if reconstructed.count(current_span) != 1:
            raise RuntimeError("causal owner delta differs from registered edits")
        reconstructed = reconstructed.replace(current_span, perf2_span)
    reconstructed_digest = hashlib.sha256(reconstructed).hexdigest()
    if reconstructed_digest != PERF2_CAUSAL_MODULE_SHA256:
        raise RuntimeError("contract-only reverse patch does not reconstruct PERF2 source")
    return MappingProxyType({
        "current_module_sha256": hashlib.sha256(current).hexdigest(),
        "sealed_PERF2_module_sha256": PERF2_CAUSAL_MODULE_SHA256,
        "reverse_patch_reconstructed_sha256": reconstructed_digest,
        "changed_byte_span_count": len(replacements),
        "changed_owners": (
            "NESTED_MODEL_CONTRACT.nuisance_scaling text",
            "HeldRangeLabeler immutable maximum_nfev=75 owner",
            "HeldRangeLabeler-only solve plumbing",
        ),
        "causal_shadow_features_changed": False,
        "compact_feature_bytes_changed": False,
        "clock_geometry_code_changed": False,
        "held_label_solver_cap_changed": True,
        "global_shared_root_default_changed": False,
        "proof": (
            "reversing the three registered metadata/held-cap spans "
            "reconstructs the complete sealed PERF2 module byte-for-byte"
        ),
    })


def _preflight(args: argparse.Namespace) -> Mapping[str, Any]:
    if args.output.exists():
        raise FileExistsError(args.output)
    started = time.perf_counter()
    input_hashes = _metadata_input_hashes()
    source_hashes = _source_hashes()
    capture_bindings = _frozen_calibration_capture_bindings()
    feature_owner_delta = _perf2_feature_owner_delta_audit()
    resource_projection = _resource_projection()
    if not resource_projection["pass"]:
        status = "BLOCKED_PRE_RUN_RESOURCE_PROJECTION"
    else:
        status = "READY_FOR_INDEPENDENT_FULL_REVIEW"
    contracts = {
        "MODEL_CONTRACT.json": _plain({
            **dict(NESTED_MODEL_CONTRACT), **dict(MODEL_CONTRACT),
            "full_study_requirement": (
                "nominal-only exact PERF2 feature bytes; no envelope repetition"
            ),
            "morphology_boundary": (
                "fixed display/skeleton-scale proxy, not anatomy; envelope robustness "
                "is DEFERRED and cannot be inferred from this study"
            ),
            "qualification_ceiling": (
                "PREDICTIVE_UTILITY_QUALIFIED only; never scientific/anatomical "
                "or production-solver qualification"
            ),
            "feature_byte_contract": (
                "exact nominal causal_shadow_features owner and source hash used by "
                "sealed O2-PERF2; full rows copy own_inward_probability, "
                "torso_exposure, and other_limb_exposure without transformation"
            ),
            "sealed_PERF2_PILOT_ROWS_sha256": PERF2_PILOT_ROWS_SHA256,
            "fit_scope": "O2-FULL train actions only; validation is never refit",
        }),
        "EVALUATION_CONTRACT.json": _plain({
            **dict(EVALUATION_CONTRACT),
            "all_gates_independent": True,
            "ties": "fail every strict improvement gate",
            "multiple_gate_policy": "every named gate must pass; no composite rescue",
            "link_denominator": (
                "only estimable held-link labels enter paired B0/B1/B2 evaluation; "
                "attempted, eligible, and ineligible counts/reasons remain explicit"
            ),
            "no_occlusion_based_link_deletion": True,
        }),
        "SPLIT.json": {
            "actions": list(ACTION_ORDER),
            "train_actions": list(TRAIN_ACTIONS),
            "validation_actions": list(VALIDATION_ACTIONS),
            "validation_untouched_until_final_evaluation": True,
            "H01_H02": "UNOPENED_UNHASHED_FORBIDDEN",
        },
        "PREFIT_GATES.json": {
            "eligibility_fraction_per_action_node_minimum": 0.95,
            "eligible_optimizer_failures": 0,
            "held_identity_omitted_count": 1,
            "eligibility_and_solver_rank": 3,
            "condition_maximum": 1e8,
            "direct_train_scaled_design_condition_maximum": DESIGN_CONDITION_MAXIMUM,
            "direct_rank_sequence": "B0 full; B1=B0+1; B2=B1+1",
            "pair_minimum_train_actions": PAIR_MINIMUM_TRAIN_ACTIONS,
            "pair_minimum_train_blocks": PAIR_MINIMUM_TRAIN_BLOCKS,
            "pair_minimum_train_rows": PAIR_MINIMUM_TRAIN_ROWS,
            "pair_validation_rows_minimum": 1,
            "exposure_low_high_distinct_epoch_clusters_minimum": 30,
            "exposure_validation_actions_minimum": 6,
            "evaluable_fixed_strata_minimum_per_increment": 30,
            "failure_policy": "persist denominators/reasons then stop before fit",
        },
        "BOOTSTRAP_CONTRACT.json": {
            "seed": BOOTSTRAP_SEED,
            "replicates": BOOTSTRAP_REPLICATES,
            "block": (
                f"within each action, floor(source_epoch_index/{BOOTSTRAP_BLOCK_EPOCHS}); "
                "the original number of distinct blocks in that action is redrawn"
            ),
            "action_sampling": (
                "draw exactly 8 validation action occurrences with replacement; "
                "for each occurrence independently redraw that action's original "
                "number of blocks with replacement"
            ),
            "pairing": "one response-free weight plan shared by every model and metric",
            "multiplicity": "repeated actions and blocks retain multiplicity",
            "percentile": "NumPy linear q=.05/.95",
        },
        "ROW_STORAGE.json": {
            "format": "NumPy structured .npy, fixed little-endian columns",
            "dtype": FULL_ROW_DTYPE.descr,
            "python_dict_materialization": False,
            "per_action_chunks_then_memmapped_combined_copy": True,
            "row_cap": FULL_ROW_CAP,
            "disk_cap_bytes": FULL_DISK_CAP_BYTES,
            "finalization_disk_reserve_bytes": FINALIZATION_DISK_RESERVE_BYTES,
        },
        "PERF2_FEATURE_OWNER_DELTA.json": _plain(feature_owner_delta),
        "RESOURCE_PROJECTION.json": _plain(resource_projection),
        "COMMAND.json": {
            "external": (
                "timeout --signal=TERM --kill-after=10s 2700s env "
                "PYTHONPATH=src:tools:. .venv-v0/bin/python "
                "tools/run_c2_causal_body_shadow_o2_full.py full "
                "--preflight <sealed-preflight> "
                "--expected-preflight-sha256 <SHA256SUMS-digest> "
                "--output <fresh-nonexistent-output>"
            ),
            "max_workers": PERF2_MAX_WORKERS,
            "BLAS_threads": 1,
            "internal_stop_s": FULL_INTERNAL_STOP_S,
            "extraction_stop_s": EXTRACTION_STOP_S,
            "reserved_fit_evaluation_seal_s": POST_EXTRACTION_RESERVE_S,
            "no_retry": True,
        },
        "ALLOWLIST.json": {
            "source_hashes": dict(source_hashes),
            "input_hashes": dict(input_hashes),
            "full_predecode_expected_hashes": dict(
                capture_bindings["full_predecode_expected_hashes"]
            ),
            "per_action_capture_bindings": _plain(capture_bindings["actions"]),
            "formal_19_plus_2_manifest": {
                "path": capture_bindings["formal_manifest_path"],
                "sha256": capture_bindings["formal_manifest_sha256"],
                "selected_scope": "canonical 00-19 only",
                "H01_H02_members_selected": False,
            },
            "forbidden": ["H01", "H02", "rf_shadow_field", "production solver integration"],
            "pilot_artifact_policy": (
                "sealed pilot rows/results are not opened or parsed by this tool; "
                "2215.077s is the frozen independently audited PERF2 projection only"
            ),
        },
    }
    args.output.mkdir(parents=True)
    for name, value in contracts.items():
        _write_json(args.output / name, value)
    result = {
        "schema": "biospur.c2.o2_full.preflight.v1",
        "status": status,
        "scientific_pass": False,
        "qualification_ceiling": QUALIFICATION_CEILING,
        "full_data_opened": False,
        "H01_H02_opened_or_hashed": False,
        "pilot_rows_opened_or_parsed": False,
        "focused_tests": args.focused_tests,
        "focused_tests_output": args.focused_tests_output,
        "focused_tests_wall_s": args.focused_tests_wall_s,
        "wall_s": time.perf_counter() - started,
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    _write_json(args.output / "TESTS.json", {
        "command": args.focused_tests,
        "output": args.focused_tests_output,
        "wall_s": args.focused_tests_wall_s,
        "passed": args.focused_tests_passed,
        "failed": 0,
        "exit_code": 0,
    })
    _write_json(args.output / "RESULT.json", result)
    (args.output / "REPORT.md").write_text(
        "# O2-FULL pre-run contract\n\n"
        f"Status: `{status}`. No range capture, H01/H02, pilot outcome, or full "
        "study row was opened. Passing this preflight is not execution authority.\n",
        encoding="utf-8",
    )
    if _directory_bytes(args.output) > 100_000_000:
        raise RuntimeError("O2 full preflight evidence exceeded 100 MB")
    result["seal_member_count"] = len([
        path for path in args.output.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    ])
    _write_json(args.output / "RESULT.json", result)
    _seal(args.output)
    return MappingProxyType(result)


def _run_full(args: argparse.Namespace) -> Mapping[str, Any]:
    """Run only after an external monitor authorizes the exact sealed command."""

    from evaluate_c2_pair_bias_gate import (
        _base_sigma, _load_episode, _load_layout, _prediction, _reference_time,
        _tracker, _update_tracker, _valid_slots,
    )

    if args.output.exists():
        raise FileExistsError(args.output)
    _verify_sealed_directory(args.preflight, args.expected_preflight_sha256)
    preflight = json.loads((args.preflight / "RESULT.json").read_text(encoding="utf-8"))
    if preflight.get("status") != "READY_FOR_INDEPENDENT_FULL_REVIEW":
        raise RuntimeError("O2 full preflight is not review-ready")
    allowlist_audit = _verify_full_allowlist(args.preflight)
    started = time.perf_counter()
    trajectory, pose_clocks, input_audit = _verified_inputs()
    clocks = _clock_models(CLOCK_TABLE)
    clock_document = json.loads(CLOCK_TABLE.read_text(encoding="utf-8"))
    bridges = _beacon_boundary_bridges(CLOCK_TABLE)
    anchors, delays, tag_delay, layout_sigma = _load_layout()
    calibration = load_frozen_c2_3a()
    alignment, _forward = frozen_world_alignment(calibration)
    provider = _PoseProvider(
        trajectory=trajectory, clocks=pose_clocks,
        alignment=alignment, geometry=calibration.geometry,
    )
    node_clocks = {
        node: NodeLinkClock(
            node, clock.a_ns_per_us, clock.b_ns, clock.boot_epoch,
            int(clock_document["models"][node]["first_timer_us"]),
            int(clock_document["models"][node]["last_timer_us"]),
        ) for node, clock in clocks.items()
    }
    room_initial = np.array([
        float(np.mean(anchors[:, 0])), float(np.mean(anchors[:, 1])), 0.95,
    ])
    args.output.mkdir(parents=True)
    chunk_dir = args.output / "ROW_CHUNKS"
    chunk_dir.mkdir()
    attempts: Counter[tuple[int, int]] = Counter()
    eligible: Counter[tuple[int, int]] = Counter()
    ineligible_reasons: Counter[str] = Counter()
    action_audit: dict[str, Any] = {}
    total_rows = 0
    pose_ages: list[float] = []
    decode_wall = held_wall = tracker_wall = 0.0
    spawn_context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=PERF2_MAX_WORKERS, mp_context=spawn_context,
    ) as executor:
        for action_index, action in enumerate(ACTION_ORDER):
            if time.perf_counter() - started > EXTRACTION_STOP_S:
                raise TimeoutError("O2 full exhausted its post-extraction reserve")
            decode_started = time.perf_counter()
            with _accelerated_transport_crc():
                episode = _load_episode(action, clocks, bridges)
            decode_wall += time.perf_counter() - decode_started
            tracker = _tracker(room_initial)
            action_rows: list[tuple[Any, ...]] = []
            action_attempts = action_ineligible = 0
            for source_epoch_index, group in enumerate(episode["groups"]):
                if time.perf_counter() - started > EXTRACTION_STOP_S:
                    raise TimeoutError("O2 full exhausted its post-extraction reserve")
                reference_s = _reference_time(group, clocks)
                predicted_root, dt = _prediction(tracker, reference_s)
                velocity = np.asarray(tracker["velocity"], dtype=float)
                exact: list[tuple[SharedRangeLink, Any, Any, Any]] = []
                identities: list[tuple[str, int]] = []
                for raw in group:
                    node_index = NODE_INDEX[raw.node]
                    node_clock = node_clocks[raw.node]
                    for anchor in _valid_slots(raw):
                        attempts[(action_index, node_index)] += 1
                        action_attempts += 1
                        link_ns = node_clock.link_time_ns(
                            event_boot_epoch=int(raw.boot),
                            strobe_us=int(raw.strobe_us),
                            t_round_us=float(raw.t_round_us[anchor]),
                        )
                        link_s = link_ns * 1e-9
                        link_dt = link_s - reference_s
                        root_at_link = predicted_root + link_dt * velocity
                        try:
                            snapshot = provider.snapshot(
                                action=action, link_time_ns=link_ns,
                                root_world_m=root_at_link,
                            )
                        except ValueError as exc:
                            ineligible_reasons[f"POSE:{exc}"] += 1
                            action_ineligible += 1
                            continue
                        corrected = (
                            float(raw.ranges_mm[anchor]) / 1000.0
                            - float(delays[anchor]) - float(tag_delay)
                        )
                        sigma = _base_sigma(layout_sigma, int(raw.quality[anchor]))
                        link = SharedRangeLink(
                            node=raw.node, anchor=int(anchor), range_m=corrected,
                            tag_offset_world_m=snapshot.offsets_world_m[raw.node],
                            link_dt_s=link_dt, sigma_m=sigma,
                        )
                        feature = causal_shadow_features(
                            node=raw.node,
                            anchor_position_world_m=anchors[anchor],
                            snapshot=snapshot,
                            geometry=calibration.geometry,
                        )
                        origin = root_at_link + snapshot.offsets_world_m[raw.node]
                        nuisance = common_nuisance_values(
                            node=raw.node, anchor=anchor,
                            causal_tag_origin_m=origin,
                            anchor_position_m=anchors[anchor],
                            base_sigma_m=sigma,
                        )
                        exact.append((link, snapshot, feature, nuisance))
                        identities.append((raw.node, int(anchor)))
                if len(identities) != len(set(identities)):
                    raise RuntimeError("duplicate node-anchor identity in full-study epoch")
                links = tuple(row[0] for row in exact)
                label_started = time.perf_counter()
                try:
                    outcomes = _parallel_held_labels(
                        executor, links, anchors=anchors,
                        initial_root=predicted_root, velocity=velocity,
                    )
                except BaseException as exc:
                    _write_json(args.output / "ELIGIBLE_WORKER_FAILURE.json", {
                        "status": "BLOCKED_ELIGIBLE_WORKER_FAILURE",
                        "action": action,
                        "source_epoch_index": source_epoch_index,
                        "exception_type": type(exc).__name__,
                        "exception": str(exc),
                        "attempts_by_action_node": {
                            f"{key[0]}/{NODE_ORDER[key[1]]}": value
                            for key, value in sorted(attempts.items())
                        },
                        "eligible_by_action_node": {
                            f"{key[0]}/{NODE_ORDER[key[1]]}": value
                            for key, value in sorted(eligible.items())
                        },
                        "ineligible_reasons": dict(ineligible_reasons),
                        "persisted_before_outer_raise": True,
                    })
                    raise
                held_wall += time.perf_counter() - label_started
                for (link, snapshot, feature, nuisance), (label, error) in zip(
                    exact, outcomes, strict=True
                ):
                    if error is not None:
                        ineligible_reasons[f"HELD:{error}"] += 1
                        action_ineligible += 1
                        continue
                    if label is None:
                        raise RuntimeError("full-study held-label outcome is empty")
                    node_index = NODE_INDEX[link.node]
                    eligible[(action_index, node_index)] += 1
                    pose_ages.append(snapshot.pose_age_ns * 1e-6)
                    action_rows.append((
                        action_index, source_epoch_index, node_index, int(link.anchor),
                        snapshot.frame, snapshot.pose_age_ns * 1e-6,
                        snapshot.query_global_ns, label.predicted_range_m,
                        label.signed_innovation_m, label.eligibility_rank,
                        label.eligibility_condition, label.rank, label.condition,
                        label.omitted_identity_count, feature.own_inward_probability,
                        feature.torso_exposure, feature.other_limb_exposure,
                        nuisance["causal_predicted_range_m"],
                        nuisance["causal_tag_origin_x_m"],
                        nuisance["causal_tag_origin_y_m"],
                        nuisance["causal_tag_origin_z_m"], nuisance["base_sigma_m"],
                    ))
                tracker_started = time.perf_counter()
                all_result = solve_shared_root(
                    links, anchors_m=anchors, initial_root_m=predicted_root,
                    root_velocity_mps=velocity,
                )
                tracker_wall += time.perf_counter() - tracker_started
                if all_result.success:
                    _update_tracker(tracker, all_result.root_position_m, reference_s, dt)
            array = np.asarray(action_rows, dtype=FULL_ROW_DTYPE)
            total_rows += len(array)
            if total_rows > FULL_ROW_CAP:
                raise RuntimeError("O2 full structured row cap exceeded")
            chunk_path = chunk_dir / f"{action_index:02d}.npy"
            np.save(chunk_path, array, allow_pickle=False)
            if _directory_bytes(args.output) > FULL_DISK_CAP_BYTES:
                raise RuntimeError("O2 full incremental disk cap exceeded")
            if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss > FULL_RSS_CAP_KB:
                raise MemoryError("O2 full parent RSS cap exceeded")
            action_audit[action] = {
                "attempted_links": action_attempts,
                "eligible_rows": len(array),
                "ineligible_links": action_ineligible,
            }
    residual_workers = [
        process.pid for process in multiprocessing.active_children()
        if process.is_alive()
    ]
    if residual_workers:
        raise RuntimeError(f"O2 full worker teardown failed: {residual_workers}")
    child_peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if child_peak > FULL_RSS_CAP_KB:
        raise MemoryError("O2 full child RSS cap exceeded")
    rows_path = args.output / "FULL_ROWS.npy"
    rows = np.lib.format.open_memmap(
        rows_path, mode="w+", dtype=FULL_ROW_DTYPE, shape=(total_rows,)
    )
    cursor = 0
    for action_index in range(len(ACTION_ORDER)):
        chunk = np.load(chunk_dir / f"{action_index:02d}.npy", mmap_mode="r")
        rows[cursor:cursor + len(chunk)] = chunk
        cursor += len(chunk)
    rows.flush()
    del rows
    rows = np.load(rows_path, mmap_mode="r")
    train = rows["action_index"] < len(TRAIN_ACTIONS)
    validation = ~train
    eligibility = eligibility_gate(attempts, eligible, optimizer_failures=0)
    identity_gate = bool(
        np.all(rows["omitted_identity_count"] == 1)
        and np.all(rows["eligibility_rank"] == 3)
        and np.all(rows["solver_rank"] == 3)
        and np.all(rows["eligibility_condition"] <= 1e8)
        and np.all(rows["solver_condition"] <= 1e8)
    )
    pose_gate = bool(
        pose_ages and min(pose_ages) > 0.0 and max(pose_ages) <= 5.005
    )
    design = freeze_common_design(rows, train)
    design_audit = residualized_shadow_audit(rows, train, design)
    pair_audit = pair_coverage_gate(rows, train, validation)
    exposure_audit = exposure_cluster_gate(rows, train, validation)
    prefit_gates = {
        "eligibility": bool(eligibility["pass"]),
        "held_identity_rank_condition": identity_gate,
        "pose_strict_floor_age": pose_gate,
        "pair_coverage": bool(pair_audit["pass"]),
        "direct_scaled_design_and_increment_rank": bool(design_audit["pass"]),
        "validation_exposure_cluster_coverage": bool(exposure_audit["pass"]),
        "same_rows_for_B0_B1_B2": True,
        "eligible_optimizer_failures_zero": True,
    }
    extraction_audit = {
        "action_audit": action_audit,
        "attempts_by_action_node": {
            f"{action}/{NODE_ORDER[node]}": count
            for (action, node), count in sorted(attempts.items())
        },
        "eligible_by_action_node": {
            f"{action}/{NODE_ORDER[node]}": count
            for (action, node), count in sorted(eligible.items())
        },
        "ineligible_reasons": dict(ineligible_reasons),
        "eligibility_gate": _plain(eligibility),
        "pose_age_ms": {
            "minimum": min(pose_ages) if pose_ages else None,
            "median": float(np.median(pose_ages)) if pose_ages else None,
            "maximum": max(pose_ages) if pose_ages else None,
        },
        "prefit_gates": prefit_gates,
        "pair_coverage": _plain(pair_audit),
        "design": {
            "names": design.names,
            "rank": design.rank,
            "condition": design.condition,
            "singular_values": design.singular_values.tolist(),
            "column_rms": design.design_column_rms.tolist(),
            "increment_audit": _plain(design_audit),
        },
        "exposure_coverage": _plain(exposure_audit),
    }
    _write_json(args.output / "EXTRACTION_AUDIT.json", extraction_audit)
    extraction_elapsed = time.perf_counter() - started
    if extraction_elapsed > EXTRACTION_STOP_S:
        raise TimeoutError("O2 full has insufficient frozen fit/evaluation/seal time")
    if _directory_bytes(args.output) > (
        FULL_DISK_CAP_BYTES - FINALIZATION_DISK_RESERVE_BYTES
    ):
        raise RuntimeError("O2 full has insufficient finalization disk reserve")
    if not all(prefit_gates.values()):
        status = "BLOCKED_PRE_FIT_GATE"
        evaluation: Mapping[str, Any] | None = None
    else:
        fit = fit_nested_models(rows, train, design)
        evaluation = evaluate_full_study(rows, train, validation, design, fit)
        status = (
            QUALIFICATION_CEILING
            if evaluation["pass"] else "BLOCKED_VALIDATION_GATE"
        )
        _write_json(args.output / "EVALUATION.json", _plain(evaluation))
        if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss > FULL_RSS_CAP_KB:
            raise MemoryError("O2 full parent RSS cap exceeded after statistics")
        if time.perf_counter() - started > FULL_INTERNAL_STOP_S:
            raise TimeoutError("O2 full exhausted its final seal time reserve")
    wall = time.perf_counter() - started
    result = {
        "schema": "biospur.c2.o2_full.result.v1",
        "status": status,
        "scientific_pass": False,
        "predictive_utility_qualified": status == QUALIFICATION_CEILING,
        "qualification_ceiling": QUALIFICATION_CEILING,
        "actions": list(ACTION_ORDER),
        "train_actions": list(TRAIN_ACTIONS),
        "validation_actions": list(VALIDATION_ACTIONS),
        "rows": total_rows,
        "row_dtype": FULL_ROW_DTYPE.descr,
        "H01_H02_opened_or_hashed": False,
        "pilot_rows_opened_or_parsed": False,
        "current_epoch_ranges_used_for_features": False,
        "labels_frozen_before_tracker_update": True,
        "no_occlusion_based_link_deletion": True,
        "link_estimability": {
            "attempted": int(sum(attempts.values())),
            "eligible": int(total_rows),
            "ineligible": int(sum(attempts.values()) - total_rows),
            "ineligible_reasons": dict(ineligible_reasons),
            "evaluation_denominator": "eligible held-link labels only",
        },
        "decode_wall_s": decode_wall,
        "held_label_wall_s": held_wall,
        "tracker_wall_s": tracker_wall,
        "wall_s": wall,
        "parent_peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "child_peak_rss_kb": child_peak,
        "workers": PERF2_MAX_WORKERS,
        "executor_shutdown_waited": True,
        "residual_worker_pids": residual_workers,
        "thread_limits": {name: os.environ[name] for name in THREAD_LIMIT_ENVIRONMENT},
        "prefit_gates": prefit_gates,
        "preflight_sha256sums_sha256": _sha256(args.preflight / "SHA256SUMS"),
        "predecode_allowlist_audit": _plain(allowlist_audit),
        "input_audit": input_audit,
    }
    _write_json(args.output / "RESULT.json", _plain(result))
    (args.output / "REPORT.md").write_text(
        "# O2-FULL causal held-link body-shadow study\n\n"
        f"Status: `{status}`. Scientific pass is always false: this study can "
        "qualify causal predictive utility only. Every validation gate is independent; no failed "
        "gate is rescued by a composite score. There is no occlusion-based link "
        "deletion; non-estimable LOO labels stay outside the explicit eligible "
        "evaluation denominator. This "
        "study does not integrate with the production solver.\n",
        encoding="utf-8",
    )
    output_bytes = _directory_bytes(args.output)
    if output_bytes > FULL_DISK_CAP_BYTES:
        raise RuntimeError("O2 full final evidence exceeds the disk cap")
    if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss > FULL_RSS_CAP_KB:
        raise MemoryError("O2 full parent RSS cap exceeded before seal")
    if wall > FULL_HARD_S:
        raise TimeoutError("O2 full completed outside its hard wall")
    result["output_bytes_before_seal"] = output_bytes
    _write_json(args.output / "RESULT.json", _plain(result))
    _seal(args.output)
    return MappingProxyType(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--output", type=Path, required=True)
    preflight.add_argument("--focused-tests", required=True)
    preflight.add_argument("--focused-tests-output", required=True)
    preflight.add_argument("--focused-tests-wall-s", type=float, required=True)
    preflight.add_argument("--focused-tests-passed", type=int, required=True)
    full = sub.add_parser("full")
    full.add_argument("--preflight", type=Path, required=True)
    full.add_argument("--expected-preflight-sha256", required=True)
    full.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for value in (args.output, getattr(args, "preflight", ROOT)):
        resolved = value.resolve()
        if resolved != ROOT and ROOT not in resolved.parents:
            raise SystemExit("all O2 full paths must remain under Fusion_Part")
    args.output = args.output.resolve()
    if hasattr(args, "preflight"):
        args.preflight = args.preflight.resolve()
    try:
        result = _preflight(args) if args.mode == "preflight" else _run_full(args)
    except BaseException as exc:
        if args.output.is_dir() and not (args.output / "SHA256SUMS").exists():
            _write_json(args.output / "FAILURE.json", {
                "status": "BLOCKED_EXCEPTION",
                "exception_type": type(exc).__name__,
                "exception": str(exc),
                "full_data_mode": args.mode == "full",
                "scientific_pass": False,
            })
            if _directory_bytes(args.output) <= FULL_DISK_CAP_BYTES:
                _seal(args.output)
        raise
    print(json.dumps(_plain(result), indent=2, sort_keys=True, allow_nan=False))
    return 0 if not str(result["status"]).startswith("BLOCKED") else 2


if __name__ == "__main__":
    raise SystemExit(main())
