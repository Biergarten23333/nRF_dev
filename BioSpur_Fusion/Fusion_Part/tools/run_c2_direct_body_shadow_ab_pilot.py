#!/usr/bin/env python3
"""Preflight and bounded direct A/B body-shadow pilot for C2 actions 04--07.

This is a paired diagnostic, not a held-link evaluator or production filter.
Each node sweep is solved exactly twice from the same causal prior: branch A
uses antenna/torso weights and branch B adds a lighter other-limb multiplier.
Only A updates the shared diagnostic tracker after both results are frozen.
"""
from __future__ import annotations

import argparse
import ast
import binascii
from collections import OrderedDict, defaultdict
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import sys
import time
from typing import Any, Mapping

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_coupled_progressive.contracts import (
    EPISODES,
    NODE_TO_SEGMENT,
    ROOT,
    load_effective_config,
)
from biospur_fusion.c2_coupled_progressive.renderer import (
    SEGMENTS,
    display_models,
    joints_for_frame,
)
from biospur_fusion.c2_uwb_calibration.antenna_los import outward_normal_world
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import (
    MATERIAL_WEIGHT_FLOOR,
    DirectNative200Clock,
    DirectNodeLinkClock,
    DirectPoseSnapshot,
    DirectShadowPolicy,
    PoseUnavailableError,
    commit_a_after_paired_results,
    direct_shadow_evidence,
    solve_direct_ab,
    summarize_pose_support,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    NODE_TO_PROXY_POINT,
    frozen_world_alignment,
)
from biospur_fusion.c2_uwb_calibration.shared_root import SharedRangeLink
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    _beacon_boundary_bridges,
    _clock_models,
)


PILOT_ACTIONS = (
    "04_shoulder_left",
    "05_shoulder_right",
    "06_elbow_left",
    "07_elbow_right",
)
PILOT_HARD_S = 300.0
PILOT_DISK_CAP_BYTES = 50_000_000
PILOT_RSS_CAP_KB = 1_500_000
PILOT_ROW_CAP = 20_000
PREFLIGHT_RSS_CAP_KB = 300_000
PREFLIGHT_DISK_CAP_BYTES = 50_000_000
FEATURE_SWEEP_P99_GATE_MS = 5.0
POSE_CACHE_MAXIMUM = 64
REFERENCE_FULL_PREFLIGHT = ROOT / (
    "logs/c2_causal_body_shadow_o2_full_preflight_v6_20260905T221310Z"
)
REFERENCE_FULL_PREFLIGHT_SHA256 = (
    "37938a223bcb14d798cbf36cb215c0a170dee33b63ac1a434ce21b44437d9c3f"
)
REFERENCE_DIRECT_PREFLIGHT_V5 = ROOT / (
    "logs/c2_direct_body_shadow_ab_preflight_v5_20260906T100611Z"
)
REFERENCE_DIRECT_PREFLIGHT_V5_SHA256 = (
    "b5954fcf5a21fb1e75d1134007c5a4f6ae70f1a9915390c3c484bb1045371a59"
)
CAP75_QUALIFICATION = ROOT / (
    "logs/c2_direct_body_shadow_ab_cap75_qualification_20260906T104600Z"
)
CAP75_QUALIFICATION_SHA256 = (
    "7731c3731eee571d5970249b53657c898c8d2ecd82785025efc28cf2a1238506"
)
CAP75_FEATURE_P99_MS = 16.094797315308814
CAP75_FEATURE_MAX_MS = 24.550542992074043
CAP75_ONLINE_B_P99_MS = 21.91983855213032
CAP75_ONLINE_B_MAX_MS = 38.720948970876634
CAP75_ONLINE_B_GATE_MS = 12.004801920768308
ACCEPTED_RESULT = ROOT / (
    "logs/c2_native200_orientation_constrained_biomechanics_v4_20260904/"
    "FINAL_RESULT.json"
)
BASE_REPORT = ROOT / (
    "logs/c2_native200_calibration_v3_20260904/POSE_RESET_QMT_DIAGNOSTIC.json"
)
CLOCK_TABLE = ROOT / (
    "logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json"
)
FRONTEND_ARCHIVE = ROOT / (
    "logs/c2_basis_progressive_20260829T102836Z/CONTINUATION_SPRINT/"
    "C2_NONHINGE_TRAINING_REPLAY_001/FRONTEND_RECONSTRUCTION_INPUTS.npz"
)
FRONTEND_MANIFEST = FRONTEND_ARCHIVE.with_suffix(".json")
PELVIS_NODE = "BSFC2CC"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(header + array.tobytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _directory_bytes(path: Path) -> int:
    return sum(row.stat().st_size for row in path.rglob("*") if row.is_file())


def _seal(path: Path) -> str:
    rows = []
    for item in sorted(row for row in path.rglob("*") if row.is_file()):
        if item.name == "SHA256SUMS":
            continue
        rows.append(f"{_sha256(item)}  {item.relative_to(path)}")
    seal = path / "SHA256SUMS"
    seal.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return _sha256(seal)


def _verify_seal(path: Path, expected_digest: str) -> None:
    seal = path / "SHA256SUMS"
    if not seal.is_file() or _sha256(seal) != str(expected_digest):
        raise RuntimeError("preflight seal digest mismatch")
    expected: dict[str, str] = {}
    for line in seal.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if separator != "  " or len(digest) != 64 or not relative:
            raise RuntimeError("malformed preflight seal")
        if relative in expected:
            raise RuntimeError("duplicate preflight seal member")
        expected[relative] = digest
    actual = {
        str(row.relative_to(path))
        for row in path.rglob("*") if row.is_file() and row.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise RuntimeError("preflight seal member set changed")
    for relative, digest in expected.items():
        if _sha256(path / relative) != digest:
            raise RuntimeError(f"preflight member changed: {relative}")


def _accelerated_crc16(data: bytes) -> int:
    return int(binascii.crc_hqx(data, 0xFFFF))


@contextmanager
def _accelerated_transport_crc():
    import fusion_host_binary as transport

    original = transport.crc16_ccitt_false
    if original(b"123456789") != 0x29B1 or _accelerated_crc16(b"123456789") != 0x29B1:
        raise RuntimeError("transport CRC equivalence failed")
    transport.crc16_ccitt_false = _accelerated_crc16
    try:
        yield
    finally:
        transport.crc16_ccitt_false = original


def _load_trajectory(path: Path) -> dict[str, Any]:
    output: dict[str, Any] = {"trajectory": {}}
    with np.load(path, allow_pickle=False) as archive:
        for action in PILOT_ACTIONS:
            key = f"{EPISODES.index(action):02d}"
            output["trajectory"][key] = {}
            for segment in SEGMENTS:
                prefix = f"trajectory/{key}/{segment}"
                output["trajectory"][key][segment] = {
                    "time_root_s": np.array(archive[f"{prefix}/time_root_s"]),
                    "quat_world_segment_wxyz": np.array(
                        archive[f"{prefix}/quat_world_segment_wxyz"]
                    ),
                    "mask": np.array(archive[f"{prefix}/mask"], dtype=bool),
                }
    return output


def _verified_pose_inputs() -> tuple[
    dict[str, Any], dict[str, DirectNative200Clock], dict[str, Any]
]:
    accepted_result = json.loads(ACCEPTED_RESULT.read_text(encoding="utf-8"))
    base_report = json.loads(BASE_REPORT.read_text(encoding="utf-8"))
    clock_document = json.loads(CLOCK_TABLE.read_text(encoding="utf-8"))
    frontend_manifest = json.loads(FRONTEND_MANIFEST.read_text(encoding="utf-8"))
    accepted_path = ROOT / accepted_result["calibration_trajectory"]["path"]
    base_path = ROOT / base_report["trajectory"]["path"]
    expected = {
        accepted_path: accepted_result["calibration_trajectory"]["sha256"],
        base_path: base_report["trajectory"]["sha256"],
        FRONTEND_ARCHIVE: base_report["frontend"]["archive_sha256"],
        FRONTEND_MANIFEST: base_report["frontend"]["manifest_sha256"],
    }
    for path, digest in expected.items():
        if _sha256(path) != digest:
            raise RuntimeError(f"sealed pose input hash mismatch: {path}")
    if (
        accepted_result.get("sample_rate_hz") != 200.0
        or accepted_result.get("pose_interpolation") is not False
        or accepted_result.get("mechanism_pass") is not True
    ):
        raise RuntimeError("accepted native200 pose qualification changed")
    if (
        base_report.get("physical_time_windows_unchanged") is not True
        or base_report.get("grid_period_ns") != 5_000_000
    ):
        raise RuntimeError("native200 source clock contract changed")
    clock_source = ROOT / "src/biospur_fusion/c2_uwb_root_world/beacon_clock.py"
    if clock_document.get("source_sha256") != _sha256(clock_source):
        raise RuntimeError("sealed clock source binding changed")
    clock_models = _clock_models(CLOCK_TABLE)
    pelvis_clock = clock_models[PELVIS_NODE]
    trajectory = _load_trajectory(accepted_path)
    owners: dict[str, DirectNative200Clock] = {}
    audits: dict[str, Any] = {}
    with np.load(FRONTEND_ARCHIVE, allow_pickle=False) as frontend:
        for action in PILOT_ACTIONS:
            key = f"{EPISODES.index(action):02d}"
            timer_key = f"orientation/{key}/{PELVIS_NODE}/time_us"
            boot_key = f"orientation/{key}/{PELVIS_NODE}/derived_boot_epoch"
            span_key = f"orientation/{key}/{PELVIS_NODE}/contiguous_span_id"
            timer_raw = np.array(frontend[timer_key], copy=True)
            boot_raw = np.array(frontend[boot_key], copy=True)
            span_raw = np.array(frontend[span_key], copy=True)
            for member, value in (
                (timer_key, timer_raw), (boot_key, boot_raw), (span_key, span_raw)
            ):
                binding = frontend_manifest["array_bindings"][member]
                if (
                    list(value.shape) != binding["shape"]
                    or str(value.dtype) != binding["dtype"]
                    or _array_sha256(value) != binding["sha256"]
                ):
                    raise RuntimeError(f"frontend pose binding failed: {member}")
            if not np.all(boot_raw.astype(np.int64) == int(pelvis_clock.boot_epoch)):
                raise RuntimeError(f"{action}: pelvis boot differs from clock owner")
            times = np.asarray(
                trajectory["trajectory"][key]["pelvis"]["time_root_s"], dtype=float
            )
            valid = np.logical_and.reduce([
                np.asarray(trajectory["trajectory"][key][segment]["mask"], dtype=bool)
                for segment in SEGMENTS
            ])
            owner = DirectNative200Clock(
                action=action,
                time_root_s=times,
                source_pelvis_timer_us=timer_raw.astype(np.int64),
                source_contiguous_span_id=span_raw.astype(np.int64),
                common_clock_a_ns_per_us=pelvis_clock.a_ns_per_us,
                common_clock_b_ns=pelvis_clock.b_ns,
                valid_mask=valid,
            )
            support = clock_document["models"][PELVIS_NODE]
            if (
                int(owner.timer_us[0]) < int(support["first_timer_us"])
                or int(owner.timer_us[-1]) > int(support["last_timer_us"])
            ):
                raise RuntimeError(f"{action}: pose lies outside clock support")
            owners[action] = owner
            audits[action] = {
                "samples": len(times),
                "first_timer_us": int(owner.timer_us[0]),
                "last_timer_us": int(owner.timer_us[-1]),
                "strict_floor": True,
            }
    return trajectory, owners, {
        "actions": audits,
        "accepted_path": str(accepted_path.relative_to(ROOT)),
        "accepted_sha256": _sha256(accepted_path),
        "frontend_sha256": _sha256(FRONTEND_ARCHIVE),
        "clock_table_sha256": _sha256(CLOCK_TABLE),
        "raw_uwb_opened": False,
        "H01_H02_opened_or_hashed": False,
    }


def _verified_pose_inputs_preflight() -> dict[str, Any]:
    """Verify pose owners without materializing compressed trajectory arrays.

    The complete decode/grid check remains owned by ``_verified_pose_inputs``
    and is rerun by the pilot before its first raw capture is decoded.  This
    metadata-only path binds that already-sealed validation while streaming
    hashes and checking its source manifests.
    """

    _verify_seal(
        REFERENCE_DIRECT_PREFLIGHT_V5,
        REFERENCE_DIRECT_PREFLIGHT_V5_SHA256,
    )
    prior_allowlist = json.loads(
        (REFERENCE_DIRECT_PREFLIGHT_V5 / "ALLOWLIST.json").read_text(
            encoding="utf-8"
        )
    )
    prior_audit = dict(prior_allowlist["pose_input_audit"])
    if (
        prior_audit.get("raw_uwb_opened") is not False
        or prior_audit.get("H01_H02_opened_or_hashed") is not False
        or set(prior_audit.get("actions", {})) != set(PILOT_ACTIONS)
    ):
        raise RuntimeError("sealed v5 pose audit is not admissible")

    accepted_result = json.loads(ACCEPTED_RESULT.read_text(encoding="utf-8"))
    base_report = json.loads(BASE_REPORT.read_text(encoding="utf-8"))
    clock_document = json.loads(CLOCK_TABLE.read_text(encoding="utf-8"))
    frontend_manifest = json.loads(FRONTEND_MANIFEST.read_text(encoding="utf-8"))
    accepted_path = ROOT / accepted_result["calibration_trajectory"]["path"]
    base_path = ROOT / base_report["trajectory"]["path"]
    expected = {
        accepted_path: accepted_result["calibration_trajectory"]["sha256"],
        base_path: base_report["trajectory"]["sha256"],
        FRONTEND_ARCHIVE: base_report["frontend"]["archive_sha256"],
        FRONTEND_MANIFEST: base_report["frontend"]["manifest_sha256"],
    }
    for path, digest in expected.items():
        if _sha256(path) != digest:
            raise RuntimeError(f"sealed pose input hash mismatch: {path}")
    if (
        accepted_result.get("sample_rate_hz") != 200.0
        or accepted_result.get("pose_interpolation") is not False
        or accepted_result.get("mechanism_pass") is not True
        or base_report.get("physical_time_windows_unchanged") is not True
        or base_report.get("grid_period_ns") != 5_000_000
    ):
        raise RuntimeError("native200 metadata contract changed")
    clock_source = ROOT / "src/biospur_fusion/c2_uwb_root_world/beacon_clock.py"
    if clock_document.get("source_sha256") != _sha256(clock_source):
        raise RuntimeError("sealed clock source binding changed")
    if not isinstance(frontend_manifest.get("array_bindings"), dict):
        raise RuntimeError("frontend manifest lacks array bindings")
    if (
        prior_audit.get("accepted_path") != str(accepted_path.relative_to(ROOT))
        or prior_audit.get("accepted_sha256") != _sha256(accepted_path)
        or prior_audit.get("frontend_sha256") != _sha256(FRONTEND_ARCHIVE)
        or prior_audit.get("clock_table_sha256") != _sha256(CLOCK_TABLE)
    ):
        raise RuntimeError("sealed v5 pose audit no longer matches its owners")
    return {
        **prior_audit,
        "validation_owner": str(REFERENCE_DIRECT_PREFLIGHT_V5.relative_to(ROOT)),
        "validation_owner_SHA256SUMS_sha256": REFERENCE_DIRECT_PREFLIGHT_V5_SHA256,
        "preflight_validation_mode": "STREAMING_HASH_AND_MANIFEST_ONLY",
        "trajectory_or_frontend_arrays_materialized": False,
        "pilot_revalidates_full_arrays_before_raw_decode": True,
    }


class _PoseProvider:
    def __init__(
        self,
        *,
        trajectory: dict[str, Any],
        clocks: Mapping[str, DirectNative200Clock],
        alignment: np.ndarray,
    ) -> None:
        self.trajectory = trajectory
        self.clocks = clocks
        self.alignment = np.asarray(alignment, dtype=float)
        self.config = load_effective_config()
        self.model = display_models(self.config)[1]
        self._cache: OrderedDict[
            tuple[str, int],
            tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray]],
        ] = OrderedDict()
        self.maximum_cache_entries = 0

    def snapshot(
        self, *, action: str, sweep_query_ns: float, root_world_m: np.ndarray
    ) -> DirectPoseSnapshot:
        index = self.clocks[action].strict_floor(sweep_query_ns)
        key = f"{EPISODES.index(action):02d}"
        cache_key = (action, index.frame)
        if cache_key not in self._cache:
            internal = joints_for_frame(
                self.trajectory, key, index.frame, self.model, self.config,
                apply_output_coordinates=False,
            )
            pelvis = np.asarray(internal["pelvis_center"], dtype=float)
            joints = {
                name: self.alignment @ (np.asarray(value, dtype=float) - pelvis)
                for name, value in internal.items()
            }
            offsets = {
                node: joints[point] for node, point in NODE_TO_PROXY_POINT.items()
            }
            normals = {
                node: outward_normal_world(
                    node,
                    self.trajectory["trajectory"][key][segment][
                        "quat_world_segment_wxyz"
                    ][index.frame],
                    self.alignment,
                )
                for node, segment in NODE_TO_SEGMENT.items()
            }
            self._cache[cache_key] = (offsets, normals, joints)
            if len(self._cache) > POSE_CACHE_MAXIMUM:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(cache_key)
        self.maximum_cache_entries = max(self.maximum_cache_entries, len(self._cache))
        offsets, normals, joints = self._cache[cache_key]
        return DirectPoseSnapshot(
            action=action,
            frame=index.frame,
            pose_global_ns=index.pose_global_ns,
            query_global_ns=index.query_global_ns,
            pose_age_ns=index.age_ns,
            root_world_m=root_world_m,
            offsets_world_m=offsets,
            normals_world=normals,
            joints_relative_world_m=joints,
        )


def _source_paths() -> tuple[Path, ...]:
    return (
        ROOT / "src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/shared_root.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/antenna_los.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/body_occlusion.py",
        ROOT / "src/biospur_fusion/c2_uwb_calibration/frozen_body_proxy.py",
        ROOT / "tests/test_c2_direct_body_shadow_ab.py",
        ROOT / "tests/test_c2_direct_body_shadow_ab_runner.py",
        ROOT / "tools/qualify_c2_direct_body_shadow_ab_cap75.py",
        Path(__file__).resolve(),
    )


def _raw_bindings() -> dict[str, str]:
    _verify_seal(REFERENCE_FULL_PREFLIGHT, REFERENCE_FULL_PREFLIGHT_SHA256)
    allowlist = json.loads(
        (REFERENCE_FULL_PREFLIGHT / "ALLOWLIST.json").read_text(encoding="utf-8")
    )
    rows = allowlist["per_action_capture_bindings"]
    selected = {
        action: (row["decoded_slice_path"], row["decoded_slice_sha256"])
        for action, row in rows.items() if action in PILOT_ACTIONS
    }
    if set(selected) != set(PILOT_ACTIONS):
        raise RuntimeError("reference seal lacks an exact pilot raw binding")
    return {path: digest for path, digest in selected.values()}


def _reference_v6_bindings() -> dict[str, dict[str, str]]:
    """Return the complete independently sealed v6 execution dependency map."""

    _verify_seal(REFERENCE_FULL_PREFLIGHT, REFERENCE_FULL_PREFLIGHT_SHA256)
    allowlist = json.loads(
        (REFERENCE_FULL_PREFLIGHT / "ALLOWLIST.json").read_text(encoding="utf-8")
    )
    output = {
        "source_hashes": dict(allowlist["source_hashes"]),
        "input_hashes": dict(allowlist["input_hashes"]),
        "full_predecode_expected_hashes": dict(
            allowlist["full_predecode_expected_hashes"]
        ),
    }
    expected_counts = {
        "source_hashes": 41,
        "input_hashes": 8,
        "full_predecode_expected_hashes": 77,
    }
    if {key: len(value) for key, value in output.items()} != expected_counts:
        raise RuntimeError("sealed v6 dependency counts changed")
    return output


def _verified_cap75_qualification() -> dict[str, Any]:
    _verify_seal(CAP75_QUALIFICATION, CAP75_QUALIFICATION_SHA256)
    result = json.loads((CAP75_QUALIFICATION / "RESULT.json").read_text())
    numeric = (
        "prefix_rows_exact",
        "cap50_cap75_prior_results_bitwise_equal",
        "all_prior_nfev_below_50",
        "blocker_cap75_equals_sealed_cap150",
        "rss_below_cap",
    )
    expected_timing = {
        "feature": {"p99": CAP75_FEATURE_P99_MS, "max": CAP75_FEATURE_MAX_MS},
        "single_branch_online_B_core": {
            "p99": CAP75_ONLINE_B_P99_MS,
            "max": CAP75_ONLINE_B_MAX_MS,
        },
    }
    if (
        result.get("status") != "BLOCKED_CAP75_QUALIFICATION_GATE"
        or result.get("prefix_rows_compared") != 5_352
        or not all(result.get("gates", {}).get(name) is True for name in numeric)
        or result.get("gates", {}).get("feature_p99_below_5ms") is not False
        or result.get("gates", {}).get(
            "online_B_core_p99_below_service_interval"
        ) is not False
        or result.get("gates", {}).get(
            "online_B_core_max_below_service_interval"
        ) is not False
        or {
            owner: {field: result["timing_ms"][owner][field] for field in values}
            for owner, values in expected_timing.items()
        } != expected_timing
    ):
        raise RuntimeError("sealed cap75 offline/online qualification boundary changed")
    return result


def _pilot_evidence_boundary() -> dict[str, Any]:
    """Return the irrevocable claim boundary shared by every pilot outcome."""

    return {
        "execution_class": "OFFLINE_ONLY",
        "numeric_status": "NUMERICALLY_QUALIFIED_OFFLINE_ONLY",
        "online_status": "ONLINE_BLOCKED",
        "qualification_ceiling": "OFFLINE_DIAGNOSTIC_REVIEW_ONLY",
        "qualification_claim_ready": False,
        "scientific_pass": False,
        "product_ready": False,
        "production_ready": False,
        "online_ready": False,
        "viewer_generated": False,
        "viewer_allowed": False,
        "cap75_qualification": {
            "path": str(CAP75_QUALIFICATION.relative_to(ROOT)),
            "SHA256SUMS_sha256": CAP75_QUALIFICATION_SHA256,
        },
        "frozen_online_timing_failure_ms": {
            "feature_p99": CAP75_FEATURE_P99_MS,
            "feature_max": CAP75_FEATURE_MAX_MS,
            "feature_gate": FEATURE_SWEEP_P99_GATE_MS,
            "online_B_core_p99": CAP75_ONLINE_B_P99_MS,
            "online_B_core_max": CAP75_ONLINE_B_MAX_MS,
            "online_B_core_gate": CAP75_ONLINE_B_GATE_MS,
        },
    }


def _pilot_completion_status(*, current_feature_timing_pass: bool) -> str:
    if not current_feature_timing_pass:
        return "BLOCKED_FEATURE_RUNTIME_P99"
    return "OFFLINE_DIRECT_AB_PILOT_COMPLETE_ONLINE_BLOCKED"


def _pilot_outcome(status: str, details: Mapping[str, Any]) -> dict[str, Any]:
    """Construct any emitted pilot result under one non-promotable boundary."""

    boundary = _pilot_evidence_boundary()
    reserved = set(boundary) | {"status"}
    conflicts = sorted(reserved.intersection(details))
    if conflicts:
        raise ValueError(
            "pilot outcome reserved evidence must have one owner: "
            + ",".join(conflicts)
        )
    return {**dict(details), **boundary, "status": str(status)}


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise FileExistsError(args.output)
    started = time.perf_counter()
    pose_audit = _verified_pose_inputs_preflight()
    source_hashes = {str(path.relative_to(ROOT)): _sha256(path) for path in _source_paths()}
    v6_bindings = _reference_v6_bindings()
    cap75_qualification = _verified_cap75_qualification()
    forbidden_tokens = ("HeldRangeLabeler", "body_shadow_study", "rf_shadow_field")
    for path in (Path(__file__).resolve(), _source_paths()[0]):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
                imported.extend(alias.name for alias in node.names)
        if any(token in value for token in forbidden_tokens for value in imported):
            raise RuntimeError(f"forbidden prior O2 owner imported by {path}")
    policy = DirectShadowPolicy()
    contracts = {
        "MODEL_CONTRACT.json": {
            "schema": "biospur.c2.direct_body_shadow_ab.model.v1",
            "offline_numeric_status": "NUMERICALLY_QUALIFIED_OFFLINE_ONLY",
            "online_status": "ONLINE_BLOCKED",
            "qualification_ceiling": "OFFLINE_DIAGNOSTIC_REVIEW_ONLY",
            "qualification_claim_ready": False,
            "scientific_pass_possible": False,
            "product_claim_possible": False,
            "production_integration_ready": False,
            "online_ready": False,
            "A": "same canonical valid ranges weighted by antenna-back and torso severity",
            "B": "A weight multiplied by lighter other-limb factor",
            "facing_score": "cos(anchor-tag, sensor -Z outward), domain [-1,1]",
            "per_segment": {
                "n": "minimum transverse centerline clearance / local display-proxy radius; dimensionless >=0",
                "s": "distance from tag to closest point along finite tag-anchor ray; metres >=0",
                "chord_depth": "sqrt(max(0,1-n^2)); proxy chord / local diameter; dimensionless [0,1]",
                "chord_length_m": "min(ray_length,2*radius*chord_depth); diagnostic only, not an independent weight input",
                "e": "(1-exp(-s/0.05m))*exp(-0.5*n^2)*(0.5+0.5*chord_depth)",
            },
            "family_union": "1-product(1-e_i), bounded/order-invariant/nonadditive",
            "A_weight": "(0.5+0.25*facing_score)*(1-0.80*torso_severity)",
            "A_bound": [0.05, 0.75],
            "B_limb_factor": "1-0.50*limb_severity",
            "B_bound": [0.025, 0.75],
            "constants": {
                "torso_strength": policy.torso_strength,
                "limb_strength": policy.limb_strength,
                "near_field_scale_m": policy.near_field_scale_m,
            },
            "proxy_owner": "existing skeleton-scale display proxy; not anatomy or fitted widths",
            "hard_occlusion_deletion": False,
            "all_positive_valid_links_retained": True,
            "forced_retain": "NOT_APPLICABLE_ALL_POSITIVE_LINKS_RETAINED",
            "material_support": {
                "weight_floor": MATERIAL_WEIGHT_FLOOR,
                "order": "(-final_information_weight, anchor)",
                "best_four": "highest-reliability material support; never weight-raised",
                "prefix_gate": "smallest prefix of >=4 links with weighted unit-LOS rank3 and condition<=1e8",
                "remainder": "strictly-positive lower-weight redundancy retained by solver",
            },
        },
        "CAUSAL_CONTRACT.json": {
            "unit": "one node UWB sweep, up to eight canonical anchors",
            "pose": "one strict-floor native200 snapshot at earliest strobe+t_round/2 link time",
            "pose_age_gate_ms": "(0,5.005]",
            "feature_inputs": "pre-sweep root/pose + anchors only; no current range",
            "tracker": (
                "common A-owned incremental tracker; both results/metrics freeze first; "
                "either solver failure blocks/no update; only A then updates future prior"
            ),
            "interpretation": "local incremental B diagnostic, not independently propagated A/B filters",
            "solver_calls_per_sweep": {"A": 1, "B": 1},
            "solver_settings": (
                "direct A/B explicitly passes immutable maximum_nfev=75 identically "
                "to A and B; shared_root global/default remains 50; all other settings identical"
            ),
            "physical_metrics": "common unweighted physical residuals from identical retained links",
            "solver_link_identity": "deep-identical A/B SharedRangeLink fields except information_weight",
            "pose_unavailable": (
                "no strictly preceding or stale pose is structured POSE_UNAVAILABLE; "
                "zero evidence/range/solve/tracker work; terminal suffix only"
            ),
            "future_online_complexity": "O(links*9 proxy segments), fixed one pose and <=64 offline cache entries",
            "online_status": "ONLINE_BLOCKED",
            "measured_online_failure_ms": {
                "feature_p99": CAP75_FEATURE_P99_MS,
                "feature_max": CAP75_FEATURE_MAX_MS,
                "feature_gate": 5.0,
                "online_B_core_p99": CAP75_ONLINE_B_P99_MS,
                "online_B_core_max": CAP75_ONLINE_B_MAX_MS,
                "online_B_core_gate": CAP75_ONLINE_B_GATE_MS,
            },
        },
        "PILOT_CONTRACT.json": {
            "actions": list(PILOT_ACTIONS),
            "epoch_stride": 1,
            "hard_wall_s": PILOT_HARD_S,
            "disk_cap_bytes": PILOT_DISK_CAP_BYTES,
            "rss_cap_kb": PILOT_RSS_CAP_KB,
            "row_cap": PILOT_ROW_CAP,
            "pose_support_gate": {
                "terminal_suffix_only": True,
                "minimum_fresh_fraction_per_action_node": 0.99,
                "minimum_fresh_sweeps_per_action_node": 100,
                "metrics_denominator": "fresh only",
                "fallback": False,
            },
            "feature_mask_construction_p99_gate_ms_per_node_sweep": FEATURE_SWEEP_P99_GATE_MS,
            "hard_timer_scope": (
                "starts before strict-floor provider.snapshot; includes cache lookup/miss, "
                "joints_for_frame FK, offsets/normals, and every anchor evidence; stops "
                "after the full evidence mapping is frozen"
            ),
            "maximum_processes": 1,
            "no_retry": True,
            "execution_class": "OFFLINE_ONLY",
            "online_status": "ONLINE_BLOCKED",
            "numeric_status": "NUMERICALLY_QUALIFIED_OFFLINE_ONLY",
            "cap75_failure_policy": "STOP; no higher cap",
            "selective_B_promotion": False,
            "reported_metrics": "paired physical residual and continuity only",
            "viewer": False,
            "viewer_policy": "FORBIDDEN",
            "command": None,
            "exact_command_binding": {
                "required": True,
                "owner": "fresh parent evidence wrapper literal COMMAND.txt",
                "placeholders_forbidden": True,
            },
        },
        "ALLOWLIST.json": {
            "source_hashes": source_hashes,
            "v6_reference_preflight": {
                "path": str(REFERENCE_FULL_PREFLIGHT.relative_to(ROOT)),
                "expected_SHA256SUMS_sha256": REFERENCE_FULL_PREFLIGHT_SHA256,
                "verified_member_count": 13,
            },
            "v6_complete_bindings": v6_bindings,
            "pose_input_audit": pose_audit,
            "pose_validation_reference": {
                "path": str(REFERENCE_DIRECT_PREFLIGHT_V5.relative_to(ROOT)),
                "expected_SHA256SUMS_sha256": REFERENCE_DIRECT_PREFLIGHT_V5_SHA256,
            },
            "raw_bindings_frozen_without_opening_raw": _raw_bindings(),
            "cap75_qualification": {
                "path": str(CAP75_QUALIFICATION.relative_to(ROOT)),
                "expected_SHA256SUMS_sha256": CAP75_QUALIFICATION_SHA256,
                "prefix_rows_compared": cap75_qualification["prefix_rows_compared"],
                "numeric_gates_pass": True,
                "online_timing_gates_pass": False,
            },
            "reference_binding_source": str(REFERENCE_FULL_PREFLIGHT.relative_to(ROOT)),
            "forbidden": ["H01", "H02", "held-link LOO", "rf_shadow_field", "body_shadow_study"],
        },
    }
    args.output.mkdir(parents=True)
    for name, value in contracts.items():
        _write_json(args.output / name, value)
    result = {
        **_pilot_evidence_boundary(),
        "status": "READY_FOR_MONITOR_OFFLINE_DIRECT_AB_PILOT_REVIEW",
        "raw_uwb_opened": False,
        "H01_H02_opened_or_hashed": False,
        "pilot_started": False,
        "focused_tests": args.focused_tests,
        "focused_tests_passed": int(args.focused_tests_passed),
        "focused_tests_wall_s": float(args.focused_tests_wall_s),
        "wall_s": time.perf_counter() - started,
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    _write_json(args.output / "RESULT.json", result)
    _write_json(args.output / "TESTS.json", {
        "command": args.focused_tests,
        "exit_code": 0,
        "passed": int(args.focused_tests_passed),
        "wall_s": float(args.focused_tests_wall_s),
    })
    _write_json(args.output / "COMMAND.json", {
        "focused_tests": args.focused_tests,
        "preflight": args.executable_command,
        "preflight_argv": sys.argv,
        "pilot_status": "HOLD_PENDING_MONITOR_GO",
    })
    _write_json(args.output / "PATCH_STATUS.json", {
        "allowed_files": [
            "tests/test_c2_direct_body_shadow_ab_runner.py",
            "tools/run_c2_direct_body_shadow_ab_pilot.py",
        ],
        "source_hashes": source_hashes,
        "protected_owners_modified": False,
        "production_integration": False,
    })
    (args.output / "REPORT.md").write_text(
        "# Direct body-shadow A/B preflight\n\n"
        "Continuous positive weights, strict pre-sweep pose ownership, explicit paired "
        "cap75 solver semantics, actions 04--07 and resource gates are frozen. Numeric "
        "qualification is offline-only; measured online timing remains BLOCKED. No raw "
        "UWB or HXX capture was opened. Pilot remains subject to monitor GO.\n",
        encoding="utf-8",
    )
    result["output_bytes"] = _directory_bytes(args.output)
    result["resource_gates"] = {
        "rss_cap_kb": PREFLIGHT_RSS_CAP_KB,
        "disk_cap_bytes": PREFLIGHT_DISK_CAP_BYTES,
        "rss_pass": result["peak_rss_kb"] < PREFLIGHT_RSS_CAP_KB,
        "disk_pass": result["output_bytes"] < PREFLIGHT_DISK_CAP_BYTES,
    }
    if not all((result["resource_gates"]["rss_pass"], result["resource_gates"]["disk_pass"])):
        result["status"] = "BLOCKED_PREFLIGHT_RESOURCE_GATE"
    _write_json(args.output / "RESULT.json", result)
    _seal(args.output)
    return result


def _quantile(values: list[float], q: float) -> float | None:
    return None if not values else float(np.quantile(np.asarray(values), q))


def _pilot(args: argparse.Namespace) -> dict[str, Any]:
    from evaluate_c2_pair_bias_gate import (
        _base_sigma,
        _load_episode,
        _load_layout,
        _prediction,
        _reference_time,
        _tracker,
        _update_tracker,
        _valid_slots,
    )

    if args.output.exists():
        raise FileExistsError(args.output)
    _verify_seal(args.preflight, args.expected_preflight_sha256)
    preflight = json.loads((args.preflight / "RESULT.json").read_text(encoding="utf-8"))
    if preflight.get("status") != "READY_FOR_MONITOR_OFFLINE_DIRECT_AB_PILOT_REVIEW":
        raise RuntimeError("direct A/B preflight is not pilot-ready")
    if (
        preflight.get("numeric_status") != "NUMERICALLY_QUALIFIED_OFFLINE_ONLY"
        or preflight.get("online_status") != "ONLINE_BLOCKED"
        or preflight.get("online_ready") is not False
    ):
        raise RuntimeError("direct A/B offline/online qualification boundary changed")
    allowlist = json.loads((args.preflight / "ALLOWLIST.json").read_text(encoding="utf-8"))
    reference = allowlist["v6_reference_preflight"]
    _verify_seal(
        ROOT / reference["path"], reference["expected_SHA256SUMS_sha256"]
    )
    for relative, digest in allowlist["source_hashes"].items():
        if _sha256(ROOT / relative) != digest:
            raise RuntimeError(f"source changed after preflight: {relative}")
    for owner, bindings in allowlist["v6_complete_bindings"].items():
        for relative, digest in bindings.items():
            if _sha256(ROOT / relative) != digest:
                raise RuntimeError(f"v6 {owner} binding changed: {relative}")
    for relative, digest in allowlist["raw_bindings_frozen_without_opening_raw"].items():
        if _sha256(ROOT / relative) != digest:
            raise RuntimeError(f"pilot raw binding changed: {relative}")

    started = time.perf_counter()
    trajectory, pose_clocks, pose_audit = _verified_pose_inputs()
    clocks = _clock_models(CLOCK_TABLE)
    clock_document = json.loads(CLOCK_TABLE.read_text(encoding="utf-8"))
    bridges = _beacon_boundary_bridges(CLOCK_TABLE)
    anchors, delays, tag_delay, layout_sigma = _load_layout()
    calibration = load_frozen_c2_3a()
    alignment, _forward = frozen_world_alignment(calibration)
    provider = _PoseProvider(trajectory=trajectory, clocks=pose_clocks, alignment=alignment)
    node_clocks = {
        node: DirectNodeLinkClock(
            node=node,
            a_ns_per_us=clock.a_ns_per_us,
            b_ns=clock.b_ns,
            boot_epoch=clock.boot_epoch,
            first_timer_us=int(clock_document["models"][node]["first_timer_us"]),
            last_timer_us=int(clock_document["models"][node]["last_timer_us"]),
        )
        for node, clock in clocks.items()
    }
    room_initial = np.array([
        float(np.mean(anchors[:, 0])), float(np.mean(anchors[:, 1])), 0.95,
    ])
    args.output.mkdir(parents=True)
    rows_path = args.output / "AB_SWEEPS.jsonl"
    support_path = args.output / "POSE_SUPPORT.jsonl"
    feature_sweep_ms: list[float] = []
    ray_only_ms: list[float] = []
    solver_pair_ms: list[float] = []
    pose_ages_ms: list[float] = []
    summary_values: dict[tuple[str, str], dict[str, list[float] | int]] = {}
    total_sweeps = 0
    total_links = 0
    written_bytes = 0
    decode_wall_s = 0.0
    failure_context: dict[str, Any] = {"tracker_updated": False}
    pose_support_history: dict[tuple[str, str], list[str]] = defaultdict(list)

    try:
        with (
            rows_path.open("w", encoding="utf-8") as stream,
            support_path.open("w", encoding="utf-8") as support_stream,
        ):
            for action in PILOT_ACTIONS:
                trackers = {node: _tracker(room_initial) for node in NODE_TO_SEGMENT}
                previous_outputs: dict[str, tuple[float, np.ndarray, np.ndarray]] = {}
                decode_started = time.perf_counter()
                with _accelerated_transport_crc():
                    episode = _load_episode(action, clocks, bridges)
                decode_wall_s += time.perf_counter() - decode_started
                for source_group_index, group in enumerate(episode["groups"]):
                    ordered_raw = sorted(
                        group,
                        key=lambda raw: (
                            clocks[raw.node].a_ns_per_us * raw.strobe_us
                            + clocks[raw.node].b_ns,
                            raw.node,
                        ),
                    )
                    for raw in ordered_raw:
                        if time.perf_counter() - started > PILOT_HARD_S - 10.0:
                            raise TimeoutError("direct A/B pilot exhausted seal reserve")
                        node = str(raw.node)
                        failure_context = {
                            "action": action,
                            "source_group_index": source_group_index,
                            "node": node,
                            "tracker_updated": False,
                        }
                        slots = tuple(_valid_slots(raw))
                        if len(slots) < 4 or len(set(slots)) != len(slots):
                            raise RuntimeError("canonical node sweep has fewer than four unique anchors")
                        reference_s = _reference_time([raw], clocks)
                        tracker = trackers[node]
                        predicted, dt = _prediction(tracker, reference_s)
                        velocity = np.asarray(tracker["velocity"], dtype=float).copy()
                        link_times_ns = {
                            anchor: node_clocks[node].link_time_ns(
                                event_boot_epoch=int(raw.boot),
                                strobe_us=int(raw.strobe_us),
                                t_round_us=float(raw.t_round_us[anchor]),
                            )
                            for anchor in slots
                        }
                        sweep_query_ns = min(link_times_ns.values())
                        root_at_snapshot = predicted + (
                            sweep_query_ns * 1e-9 - reference_s
                        ) * velocity
                        feature_started = time.perf_counter()
                        try:
                            snapshot = provider.snapshot(
                                action=action,
                                sweep_query_ns=sweep_query_ns,
                                root_world_m=root_at_snapshot,
                            )
                        except PoseUnavailableError as exc:
                            audit = exc.audit
                            pose_support_history[(action, node)].append("POSE_UNAVAILABLE")
                            support_row = {
                                "status": "POSE_UNAVAILABLE",
                                "action": action,
                                "source_group_index": source_group_index,
                                "node": node,
                                "reason": audit.reason,
                                "query_global_ns": audit.query_global_ns,
                                "selected_frame": audit.selected_frame,
                                "selected_timer_us": audit.selected_timer_us,
                                "selected_pose_global_ns": audit.selected_pose_global_ns,
                                "selected_span_id": audit.selected_span_id,
                                "signed_age_ns": audit.signed_age_ns,
                                "required_inequality": audit.required_inequality,
                                "evidence_constructed": False,
                                "range_payload_inspected_for_structural_validity": True,
                                "range_magnitude_used_in_geometry_or_weights": False,
                                "range_passed_to_solver": False,
                                "solver_calls": 0,
                                "tracker_updated": False,
                            }
                            support_stream.write(
                                json.dumps(support_row, sort_keys=True, allow_nan=False)
                                + "\n"
                            )
                            continue
                        history = pose_support_history[(action, node)]
                        if history and history[-1] == "POSE_UNAVAILABLE":
                            raise RuntimeError(
                                "nonterminal POSE_UNAVAILABLE followed by fresh pose"
                            )
                        history.append("FRESH")
                        support_stream.write(json.dumps({
                            "status": "FRESH",
                            "action": action,
                            "source_group_index": source_group_index,
                            "node": node,
                            "query_global_ns": snapshot.query_global_ns,
                            "selected_frame": snapshot.frame,
                            "selected_pose_global_ns": snapshot.pose_global_ns,
                            "signed_age_ns": snapshot.pose_age_ns,
                            "required_inequality": "0 < age_ns <= 5005000",
                        }, sort_keys=True, allow_nan=False) + "\n")
                        ray_started = time.perf_counter()
                        evidence = {
                            (node, anchor): direct_shadow_evidence(
                                node=node,
                                anchor_position_world_m=anchors[anchor],
                                snapshot=snapshot,
                                geometry=calibration.geometry,
                            )
                            for anchor in slots
                        }
                        ray_only_ms.append((time.perf_counter() - ray_started) * 1000.0)
                        feature_sweep_ms.append(
                            (time.perf_counter() - feature_started) * 1000.0
                        )
                        # Current ranges are first consumed only after every mask/weight
                        # feature for this sweep has been frozen above.
                        links = tuple(
                            SharedRangeLink(
                                node=node,
                                anchor=anchor,
                                range_m=(
                                    float(raw.ranges_mm[anchor]) / 1000.0
                                    - float(delays[anchor]) - float(tag_delay)
                                ),
                                tag_offset_world_m=snapshot.offsets_world_m[node],
                                link_dt_s=link_times_ns[anchor] * 1e-9 - reference_s,
                                sigma_m=_base_sigma(layout_sigma, int(raw.quality[anchor])),
                                facing_score=evidence[(node, anchor)].own_facing_score,
                            )
                            for anchor in slots
                        )
                        solver_started = time.perf_counter()
                        paired = solve_direct_ab(
                            links,
                            evidence_by_identity=evidence,
                            anchors_m=anchors,
                            initial_root_m=predicted,
                            root_velocity_mps=velocity,
                        )
                        failure_context.update({
                            "preoutcome_rank": paired.prepared.geometry.rank,
                            "preoutcome_condition": paired.prepared.geometry.condition,
                            "a_material": paired.prepared.a_material.__dict__,
                            "b_material": paired.prepared.b_material.__dict__,
                            "a_reason": paired.a_result.reason,
                            "a_nfev": paired.a_result.nfev,
                            "b_reason": paired.b_result.reason,
                            "b_nfev": paired.b_result.nfev,
                            "a_weights": [
                                link.information_weight for link in paired.prepared.a_links
                            ],
                            "b_weights": [
                                link.information_weight for link in paired.prepared.b_links
                            ],
                        })
                        solver_pair_ms.append((time.perf_counter() - solver_started) * 1000.0)
                        if not paired.a_result.success or not paired.b_result.success:
                            raise RuntimeError(
                                "paired solver failure: "
                                f"A={paired.a_result.reason},B={paired.b_result.reason}"
                            )
                        a_residual = np.asarray(paired.a_result.residuals_m, dtype=float)
                        b_residual = np.asarray(paired.b_result.residuals_m, dtype=float)
                        if a_residual.shape != b_residual.shape or a_residual.shape != (len(links),):
                            raise RuntimeError("paired physical residual identity changed")
                        displacement = float(np.linalg.norm(
                            paired.b_result.root_position_m - paired.a_result.root_position_m
                        ))
                        if node in previous_outputs:
                            previous_time, previous_a, previous_b = previous_outputs[node]
                            output_dt = float(reference_s - previous_time)
                            if output_dt <= 0.0:
                                raise RuntimeError("per-node A/B output time is not monotonic")
                            a_step = float(np.linalg.norm(
                                paired.a_result.root_position_m - previous_a
                            ))
                            b_step = float(np.linalg.norm(
                                paired.b_result.root_position_m - previous_b
                            ))
                            a_speed = a_step / output_dt
                            b_speed = b_step / output_dt
                        else:
                            output_dt = None
                            a_step = b_step = a_speed = b_speed = None
                        previous_outputs[node] = (
                            float(reference_s),
                            np.array(paired.a_result.root_position_m, copy=True),
                            np.array(paired.b_result.root_position_m, copy=True),
                        )
                        row = {
                            "action": action,
                            "source_group_index": source_group_index,
                            "node": node,
                            "reference_time_s": reference_s,
                            "pose_frame": snapshot.frame,
                            "pose_time_ns": snapshot.pose_global_ns,
                            "sweep_query_time_ns": snapshot.query_global_ns,
                            "pose_age_ms": snapshot.pose_age_ns * 1e-6,
                            "links_available": len(links),
                            "identities": [[link.node, link.anchor] for link in links],
                            "large_shadow_excluded": 0,
                            "small_shadow_additionally_excluded": 0,
                            "forced_retained": 0,
                            "forced_retain_reason": paired.prepared.forced_retain_reason,
                            "preoutcome_rank": paired.prepared.geometry.rank,
                            "preoutcome_condition": paired.prepared.geometry.condition,
                            "a_weights": [link.information_weight for link in paired.prepared.a_links],
                            "b_weights": [link.information_weight for link in paired.prepared.b_links],
                            "a_reliability_order": [list(value) for value in paired.prepared.a_material.ordered_identities],
                            "b_reliability_order": [list(value) for value in paired.prepared.b_material.ordered_identities],
                            "a_material_count": paired.prepared.a_material.material_count,
                            "b_material_count": paired.prepared.b_material.material_count,
                            "a_material_prefix": [list(value) for value in paired.prepared.a_material.support_prefix_identities],
                            "b_material_prefix": [list(value) for value in paired.prepared.b_material.support_prefix_identities],
                            "a_material_prefix_rank": paired.prepared.a_material.support_prefix_rank,
                            "b_material_prefix_rank": paired.prepared.b_material.support_prefix_rank,
                            "a_material_prefix_condition": paired.prepared.a_material.support_prefix_condition,
                            "b_material_prefix_condition": paired.prepared.b_material.support_prefix_condition,
                            "material_weight_floor": MATERIAL_WEIGHT_FLOOR,
                            "torso_severity": [row.torso_severity for row in paired.prepared.evidence],
                            "limb_severity": [row.limb_severity for row in paired.prepared.evidence],
                            "minimum_normalized_clearance": [
                                min((segment.normalized_clearance for segment in row.segments), default=None)
                                for row in paired.prepared.evidence
                            ],
                            "maximum_chord_length_m": [
                                max((segment.chord_length_m for segment in row.segments), default=0.0)
                                for row in paired.prepared.evidence
                            ],
                            "a_root_m": paired.a_result.root_position_m.tolist(),
                            "b_root_m": paired.b_result.root_position_m.tolist(),
                            "a_unweighted_physical_residual_rms_m": float(np.sqrt(np.mean(a_residual**2))),
                            "b_unweighted_physical_residual_rms_m": float(np.sqrt(np.mean(b_residual**2))),
                            "a_positive_tail_q90_m": float(np.quantile(np.maximum(a_residual, 0.0), 0.90)),
                            "b_positive_tail_q90_m": float(np.quantile(np.maximum(b_residual, 0.0), 0.90)),
                            "a_weighted_solver_cost": paired.a_result.cost,
                            "b_weighted_solver_cost": paired.b_result.cost,
                            "a_condition": paired.a_result.condition,
                            "b_condition": paired.b_result.condition,
                            "a_b_displacement_m": displacement,
                            "output_dt_s": output_dt,
                            "a_root_step_m": a_step,
                            "b_root_step_m": b_step,
                            "a_root_speed_mps": a_speed,
                            "b_root_speed_mps": b_speed,
                            "tracker_updated_after_pair": True,
                            "feature_current_range_dependency": False,
                            "B_updates_future_tracker": False,
                        }
                        serialized = json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
                        encoded = len(serialized.encode("utf-8"))
                        if total_sweeps + 1 > PILOT_ROW_CAP:
                            raise RuntimeError("direct A/B pilot row cap exceeded")
                        if written_bytes + encoded > PILOT_DISK_CAP_BYTES - 5_000_000:
                            raise RuntimeError("direct A/B pilot incremental disk cap exceeded")
                        if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss > PILOT_RSS_CAP_KB:
                            raise MemoryError("direct A/B pilot RSS cap exceeded")
                        stream.write(serialized)
                        written_bytes += encoded
                        total_sweeps += 1
                        total_links += len(links)
                        pose_ages_ms.append(snapshot.pose_age_ns * 1e-6)
                        bucket = summary_values.setdefault((action, node), {
                            "sweeps": 0,
                            "links": 0,
                            "a_rms": [], "b_rms": [], "a_tail": [], "b_tail": [],
                            "displacement": [], "a_condition": [], "b_condition": [],
                            "a_weight": [], "b_weight": [],
                            "a_step": [], "b_step": [], "a_speed": [], "b_speed": [],
                        })
                        bucket["sweeps"] = int(bucket["sweeps"]) + 1
                        bucket["links"] = int(bucket["links"]) + len(links)
                        for key, value in (
                            ("a_rms", row["a_unweighted_physical_residual_rms_m"]),
                            ("b_rms", row["b_unweighted_physical_residual_rms_m"]),
                            ("a_tail", row["a_positive_tail_q90_m"]),
                            ("b_tail", row["b_positive_tail_q90_m"]),
                            ("displacement", displacement),
                            ("a_condition", paired.a_result.condition),
                            ("b_condition", paired.b_result.condition),
                        ):
                            assert isinstance(bucket[key], list)
                            bucket[key].append(float(value))
                        for key, value in (
                            ("a_step", a_step), ("b_step", b_step),
                            ("a_speed", a_speed), ("b_speed", b_speed),
                        ):
                            if value is not None:
                                assert isinstance(bucket[key], list)
                                bucket[key].append(float(value))
                        assert isinstance(bucket["a_weight"], list)
                        assert isinstance(bucket["b_weight"], list)
                        bucket["a_weight"].extend(row["a_weights"])
                        bucket["b_weight"].extend(row["b_weights"])
                        # Both results and all metrics are frozen.  Only A owns the
                        # common incremental diagnostic tracker from this point.
                        commit_a_after_paired_results(
                            paired,
                            lambda root: _update_tracker(
                                tracker, root, reference_s, dt
                            ),
                        )
                        failure_context["tracker_updated"] = True
    except BaseException as exc:
        failure = _pilot_outcome(
            "BLOCKED_DIRECT_AB_PILOT",
            {
                "reason": f"{type(exc).__name__}: {exc}",
                "sweeps_completed": total_sweeps,
                "wall_s": time.perf_counter() - started,
                "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "failure_context": failure_context,
                "tracker_not_updated": failure_context.get("tracker_updated") is False,
            },
        )
        _write_json(args.output / "FAILURE.json", failure)
        _seal(args.output)
        raise

    pose_support = {}
    for action in PILOT_ACTIONS:
        for node in sorted(NODE_TO_SEGMENT):
            key = (action, node)
            try:
                audit = summarize_pose_support(pose_support_history.get(key, ()))
            except ValueError as exc:
                raise RuntimeError(f"{action}/{node}: pose support missing") from exc
            pose_support[f"{action}/{node}"] = audit
    pose_support_pass = all(bool(row["pass"]) for row in pose_support.values())
    p99_feature = _quantile(feature_sweep_ms, 0.99)
    summaries = {}
    for (action, node), values in sorted(summary_values.items()):
        summaries[f"{action}/{node}"] = {
            "sweeps": values["sweeps"],
            "links": values["links"],
            "large_shadow_excluded": 0,
            "small_shadow_additionally_excluded": 0,
            "forced_retained": 0,
            "solver_success_A": values["sweeps"],
            "solver_success_B": values["sweeps"],
            "median_A_unweighted_residual_rms_m": _quantile(values["a_rms"], 0.5),
            "median_B_unweighted_residual_rms_m": _quantile(values["b_rms"], 0.5),
            "median_A_positive_tail_q90_m": _quantile(values["a_tail"], 0.5),
            "median_B_positive_tail_q90_m": _quantile(values["b_tail"], 0.5),
            "A_B_displacement_p50_m": _quantile(values["displacement"], 0.5),
            "A_B_displacement_p99_m": _quantile(values["displacement"], 0.99),
            "A_condition_max": max(values["a_condition"]),
            "B_condition_max": max(values["b_condition"]),
            "A_root_step_p99_m": _quantile(values["a_step"], 0.99),
            "B_root_step_p99_m": _quantile(values["b_step"], 0.99),
            "A_root_speed_p99_mps": _quantile(values["a_speed"], 0.99),
            "B_root_speed_p99_mps": _quantile(values["b_speed"], 0.99),
            "A_weight_p50": _quantile(values["a_weight"], 0.5),
            "B_weight_p50": _quantile(values["b_weight"], 0.5),
        }
    wall = time.perf_counter() - started
    current_feature_timing_pass = (
        p99_feature is not None and p99_feature < FEATURE_SWEEP_P99_GATE_MS
    )
    result = _pilot_outcome(
        _pilot_completion_status(
            current_feature_timing_pass=current_feature_timing_pass
        ),
        {
            "actions": list(PILOT_ACTIONS),
            "epoch_stride": 1,
            "total_node_sweeps": total_sweeps,
            "total_physical_links": total_links,
            "solver_calls": {"A": total_sweeps, "B": total_sweeps},
            "all_solver_calls_successful": True,
            "all_positive_valid_links_retained": True,
            "occlusion_based_link_deletion": False,
            "forced_retained": 0,
            "material_weight_floor": MATERIAL_WEIGHT_FLOOR,
            "pose_support": pose_support,
            "pose_support_gate_pass": pose_support_pass,
            "feature_sweep_ms": {
                "scope": "strict-floor+pose/FK/cache+all-anchor shadow evidence",
                "p50": _quantile(feature_sweep_ms, 0.5),
                "p99": p99_feature,
                "maximum": max(feature_sweep_ms) if feature_sweep_ms else None,
                "gate_ms": FEATURE_SWEEP_P99_GATE_MS,
                "pass": p99_feature is not None
                and p99_feature < FEATURE_SWEEP_P99_GATE_MS,
            },
            "ray_only_ms_secondary": {
                "p50": _quantile(ray_only_ms, 0.5),
                "p99": _quantile(ray_only_ms, 0.99),
                "maximum": max(ray_only_ms) if ray_only_ms else None,
                "acceptance_gate": False,
            },
            "solver_pair_ms": {
                "p50": _quantile(solver_pair_ms, 0.5),
                "p99": _quantile(solver_pair_ms, 0.99),
                "maximum": max(solver_pair_ms) if solver_pair_ms else None,
            },
            "pose_age_ms": {
                "minimum": min(pose_ages_ms) if pose_ages_ms else None,
                "p50": _quantile(pose_ages_ms, 0.5),
                "maximum": max(pose_ages_ms) if pose_ages_ms else None,
                "all_in_open_closed_gate": bool(
                    pose_ages_ms
                    and min(pose_ages_ms) > 0.0
                    and max(pose_ages_ms) <= 5.005
                ),
            },
            "tracker_owner": "A_AFTER_PAIRED_RESULTS_FROZEN",
            "B_updates_future_tracker": False,
            "common_physical_residual_metrics": True,
            "pose_cache": {
                "maximum_entries": provider.maximum_cache_entries,
                "fixed_cap": POSE_CACHE_MAXIMUM,
            },
            "summaries": summaries,
            "decode_wall_s": decode_wall_s,
            "wall_s": wall,
            "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "output_bytes_before_seal": _directory_bytes(args.output),
            "pose_input_audit": pose_audit,
            "preflight_sha256": _sha256(args.preflight / "SHA256SUMS"),
            "H01_H02_opened_or_hashed": False,
            "held_link_LOO_used": False,
            "response_model_fit": False,
            "current_run_feature_timing_pass": current_feature_timing_pass,
        },
    )
    if not result["pose_age_ms"]["all_in_open_closed_gate"]:
        result["status"] = "BLOCKED_POSE_AGE"
    if not pose_support_pass:
        result["status"] = "BLOCKED_POSE_SUPPORT_COVERAGE"
    _write_json(args.output / "RESULT.json", result)
    (args.output / "REPORT.md").write_text(
        "# Direct body-shadow A/B pilot\n\n"
        f"Status: `{result['status']}`. This is a paired local incremental diagnostic: "
        "B does not own future tracker state. All physical links were retained with "
        "positive reliability weights; weighted objective values are not used as the "
        "physical comparison. See RESULT.json and AB_SWEEPS.jsonl.\n",
        encoding="utf-8",
    )
    final_bytes = _directory_bytes(args.output)
    if final_bytes > PILOT_DISK_CAP_BYTES:
        raise RuntimeError("direct A/B pilot exceeded final disk cap")
    result["output_bytes_before_seal"] = final_bytes
    _write_json(args.output / "RESULT.json", result)
    _seal(args.output)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--output", type=Path, required=True)
    preflight.add_argument("--focused-tests", required=True)
    preflight.add_argument("--focused-tests-passed", type=int, required=True)
    preflight.add_argument("--focused-tests-wall-s", type=float, required=True)
    preflight.add_argument("--executable-command", required=True)
    pilot = subparsers.add_parser("pilot")
    pilot.add_argument("--preflight", type=Path, required=True)
    pilot.add_argument("--expected-preflight-sha256", required=True)
    pilot.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = _preflight(args) if args.command == "preflight" else _pilot(args)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if not str(result["status"]).startswith("BLOCKED") else 2


if __name__ == "__main__":
    raise SystemExit(main())
