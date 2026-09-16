#!/usr/bin/env python3
"""One-shot non-promotable root A/B diagnostic for the indivisible C2 session."""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import resource
import stat
import sys
import time
from typing import Any, Mapping

import numpy as np

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
)
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FORMAL_MANIFEST_RELATIVE,
    FORMAL_MANIFEST_SHA256,
    FULL_WINDOW_SHA256,
    RAW_RELATIVE,
    SOURCE_SHA256,
    SOURCE_STAT_IDENTITY,
    FullSessionContinuousReader,
)
from biospur_fusion.c2_uwb_root_world.diagnostic_c2_static_owner import (
    DiagnosticC2StaticOwner,
)
from biospur_fusion.c2_uwb_root_world.full_session_root_ab_coordinator import (
    FullSessionRootABCoordinator,
)
from biospur_fusion.c2_uwb_root_world.continuous_full_session import (
    ContinuousSessionInventory,
    FULL_SESSION_BYTE_COUNT,
    FULL_SESSION_START_OFFSET,
    FULL_SESSION_STOP_OFFSET,
    load_continuous_session_inventory,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import _clock_models_document
from biospur_fusion.c2_uwb_root_world.split_fusion import FixedLagDriftConfig


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
CLOCK_RELATIVE = Path("logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json")
SCHEMA = "biospur.c2.full_session_root_ab.real_diagnostic.v1"
PRE_RUN_SCHEMA = "biospur.c2.full_session_root_ab.prerun.v1"
MAX_OUTPUT_BYTES = 250 * 1024 * 1024
ATTEMPT_ARTIFACTS = ("CLAIM.json", "STDOUT.txt", "STDERR.txt", "run")
PRIOR_FAILURE_RELATIVE = Path(
    "logs/c2_full_session_root_ab_real_attempt_failure_20260909T043216Z/FAILURE.json"
)
PRIOR_FAILURE_SHA256 = "1d492f9ae31a2827014dc2a85816ca6ab6571df6021fee096a7a8f71e19328b1"
RAW_FAILURE_RELATIVE = Path(
    "logs/c2_full_session_root_ab_real_prereg_recovery_20260909T044104Z/"
    "attempt1/FAILURE.json"
)
RAW_FAILURE_SHA256 = "533fb3426ec859aa3eeb20095d72736410ce9df5de6cd7abd106b03f867f1b6e"
REGION_FAILURE_RELATIVE = Path(
    "logs/c2_full_session_root_ab_real_prereg_sequence_fix_20260909T050351Z/"
    "attempt1/FAILURE.json"
)
REGION_FAILURE_SHA256 = "c40c79ca5a1f1b7ad33e6c6fc52ee7a331191871fb774c6623438d6bd5514178"
SEQUENCE_CLOSURE_RELATIVE = Path(
    "logs/c2_full_session_sequence_domain_source_closure_20260909T050128Z/RESULT.json"
)
SEQUENCE_CLOSURE_SHA256 = "6e4082acb14a05a0ea3c62ca734121ae8399876f9dc16449201e31303645cd7a"
LABEL_FREE_CLOSURE_RELATIVE = Path(
    "logs/c2_full_session_label_free_envelope_source_closure_20260909T052817Z/"
    "SOURCE_CLOSURE_MANIFEST.json"
)
LABEL_FREE_CLOSURE_SHA256 = "710e8bf48f2ac0ea782550062302966b0ffafbd62e517cf78bbf142ed2210fbe"
TIME_ENVELOPE_FAILURE_RELATIVE = Path(
    "logs/c2_full_session_root_ab_real_prereg_label_free_20260909T053212Z/"
    "attempt1/FAILURE.json"
)
TIME_ENVELOPE_FAILURE_SHA256 = "b7e5fb9bef8a615d94cfb40e2d3e08797e125c74c390abc9a199639f3a0b8ab7"
SOURCE_OWNED_CLOSURE_RELATIVE = Path(
    "logs/c2_full_session_source_owned_envelope_closure_20260909T054602Z/"
    "SOURCE_CLOSURE_MANIFEST.json"
)
SOURCE_OWNED_CLOSURE_SHA256 = "66ad8e0a6daa0130c613fd3776d3d85dd842fe91a69cac5863f0ef59e631fc14"


def required_bound_files() -> dict[str, Path]:
    """Exact preregistered owners/configuration used by this thin path."""

    return {
        "runner": Path(__file__).resolve(),
        "reader": WORKSPACE / "src/biospur_fusion/c2_coupled_progressive/continuous_full_session_reader.py",
        "frontend_clock": WORKSPACE / "src/biospur_fusion/c2_coupled_progressive/continuous_frontend.py",
        "stream_decoder": WORKSPACE / "src/biospur_fusion/c2_coupled_progressive/continuous_streaming_runner.py",
        "stage2_adapter": WORKSPACE / "src/biospur_fusion/c2_coupled_progressive/continuous_stage2_adapter.py",
        "inventory": WORKSPACE / "src/biospur_fusion/c2_uwb_root_world/continuous_full_session.py",
        "pelvis_vqf": WORKSPACE / "src/biospur_fusion/c2_uwb_root_world/continuous_root_ab.py",
        "orientation_owner": WORKSPACE / "src/biospur_fusion/c2_uwb_root_world/full_session_pelvis_orientation.py",
        "static_owner": WORKSPACE / "src/biospur_fusion/c2_uwb_root_world/diagnostic_c2_static_owner.py",
        "tight_owner": WORKSPACE / "src/biospur_fusion/c2_uwb_root_world/continuous_tight_root_owner.py",
        "coordinator": WORKSPACE / "src/biospur_fusion/c2_uwb_root_world/full_session_root_ab_coordinator.py",
        "range_solver": WORKSPACE / "src/biospur_fusion/c2_uwb_root_world/tight_range.py",
        "drift_owner": WORKSPACE / "src/biospur_fusion/c2_uwb_root_world/split_fusion.py",
        "clock_validator": WORKSPACE / "src/biospur_fusion/c2_uwb_root_world/run_calibration.py",
        "beacon_clock_source": WORKSPACE / "src/biospur_fusion/c2_uwb_root_world/beacon_clock.py",
        "shared_root_solver": WORKSPACE / "src/biospur_fusion/c2_uwb_calibration/shared_root.py",
        "direct_body_shadow": WORKSPACE / "src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py",
        "root_export": WORKSPACE / "src/biospur_fusion/root_r3/__init__.py",
        "root_estimator": WORKSPACE / "src/biospur_fusion/root_r3/estimator.py",
        "root_models": WORKSPACE / "src/biospur_fusion/root_r3/models.py",
        "typed_events": WORKSPACE / "src/biospur_fusion/ingest/events.py",
        "v47_ingest": WORKSPACE / "src/biospur_fusion/ingest/v47.py",
        "clock_table": WORKSPACE / CLOCK_RELATIVE,
        "layout": WORKSPACE.parent / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json",
        "action_inventory_audit": WORKSPACE / "logs/c2_five_node_pure_imu_v2_20260906_102300/CALIBRATION_INPUT_AUDIT.json",
        "full_hash_evidence": WORKSPACE / "logs/c2_continuous_root_ab_full_repaired_20260908T094802Z/full_run/RESULT.json",
        "formal_source_manifest": WORKSPACE / FORMAL_MANIFEST_RELATIVE,
        "source_closure": WORKSPACE / "logs/c2_diagnostic_static_owner_rejection_source_closure_20260909T035154Z/SOURCE_CLOSURE_MANIFEST.json",
        "source_closure_checksums": WORKSPACE / "logs/c2_diagnostic_static_owner_rejection_source_closure_20260909T035154Z/SHA256SUMS",
        "pretest_result": WORKSPACE / "logs/c2_full_session_root_ab_source_pretest_20260909T040553Z/RESULT.json",
        "pretest_checksums": WORKSPACE / "logs/c2_full_session_root_ab_source_pretest_20260909T040553Z/SHA256SUMS",
        "test_static": WORKSPACE / "tests/test_c2_diagnostic_c2_static_owner.py",
        "test_tight": WORKSPACE / "tests/test_c2_continuous_tight_root_owner.py",
        "test_coordinator": WORKSPACE / "tests/test_c2_full_session_root_ab_coordinator.py",
        "test_runner_preflight": WORKSPACE / "tests/test_c2_full_session_runner_preflight.py",
        "test_sequence_domain": WORKSPACE / "tests/test_c2_full_session_sequence_domains.py",
        "test_delivery": WORKSPACE / "tests/test_c2_continuous_full_session_delivery.py",
        "prior_shell_failure": WORKSPACE / PRIOR_FAILURE_RELATIVE,
        "prior_shell_failure_checksums": (
            WORKSPACE / PRIOR_FAILURE_RELATIVE.parent / "SHA256SUMS"
        ),
        "prior_raw_failure": WORKSPACE / RAW_FAILURE_RELATIVE,
        "prior_raw_failure_checksums": WORKSPACE / RAW_FAILURE_RELATIVE.parent / "SHA256SUMS",
        "prior_region_failure": WORKSPACE / REGION_FAILURE_RELATIVE,
        "prior_region_failure_checksums": (
            WORKSPACE / REGION_FAILURE_RELATIVE.parent / "SHA256SUMS"
        ),
        "sequence_domain_closure": WORKSPACE / SEQUENCE_CLOSURE_RELATIVE,
        "sequence_domain_closure_checksums": (
            WORKSPACE / SEQUENCE_CLOSURE_RELATIVE.parent / "SHA256SUMS"
        ),
        "label_free_envelope_closure": WORKSPACE / LABEL_FREE_CLOSURE_RELATIVE,
        "label_free_envelope_closure_checksums": (
            WORKSPACE / LABEL_FREE_CLOSURE_RELATIVE.parent / "SHA256SUMS"
        ),
        "prior_time_envelope_failure": WORKSPACE / TIME_ENVELOPE_FAILURE_RELATIVE,
        "prior_time_envelope_failure_checksums": (
            WORKSPACE / TIME_ENVELOPE_FAILURE_RELATIVE.parent / "SHA256SUMS"
        ),
        "source_owned_envelope_closure": WORKSPACE / SOURCE_OWNED_CLOSURE_RELATIVE,
        "source_owned_envelope_closure_checksums": (
            WORKSPACE / SOURCE_OWNED_CLOSURE_RELATIVE.parent / "SHA256SUMS"
        ),
    }


def _stable_bytes(path: Path) -> tuple[bytes, str]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                         | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"bound input is not regular: {path}")
        blocks: list[bytes] = []
        owner = hashlib.sha256()
        while block := os.read(descriptor, 1 << 20):
            blocks.append(block)
            owner.update(block)
        after = os.fstat(descriptor)
        identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
        if identity(before) != identity(after):
            raise RuntimeError(f"bound input changed while hashing: {path}")
        return b"".join(blocks), owner.hexdigest()
    finally:
        os.close(descriptor)


def _sha256(path: Path) -> str:
    return _stable_bytes(path)[1]


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _write_json_new(path: Path, document: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(_jsonable(document), stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def _load_prerun(manifest_path: Path, sha_path: Path) -> tuple[dict[str, Any], str]:
    checksum_bytes, _checksum_sha = _stable_bytes(sha_path)
    checksum_lines = checksum_bytes.decode("ascii").splitlines()
    checksum_fields = checksum_lines[0].split(maxsplit=1) if checksum_lines else []
    if (len(checksum_lines) != 1 or len(checksum_fields) != 2
            or checksum_fields[1].lstrip("*") != "PRE_RUN.json"):
        raise RuntimeError("PRE_RUN checksum sidecar is malformed")
    manifest_bytes, manifest_sha = _stable_bytes(manifest_path)
    if manifest_sha != checksum_fields[0]:
        raise RuntimeError("PRE_RUN manifest SHA-256 mismatch")
    document = json.loads(manifest_bytes.decode("utf-8"))
    if type(document) is not dict:
        raise RuntimeError("PRE_RUN manifest must be an exact JSON object")
    return document, manifest_sha


def _directory_identity(path: Path) -> tuple[int, int, int, int, int, int]:
    row = path.lstat()
    if stat.S_ISLNK(row.st_mode) or not stat.S_ISDIR(row.st_mode):
        raise RuntimeError("attempt path is a symlink or non-directory")
    return (row.st_dev, row.st_ino, stat.S_IMODE(row.st_mode), row.st_uid,
            row.st_gid, row.st_mtime_ns)


def _claim_attempt(document: Mapping[str, Any], manifest_sha: str,
                   attempt_dir: Path) -> Path:
    registered = document.get("attempt_directory", {})
    expected = (WORKSPACE / str(registered.get("path", ""))).absolute()
    if attempt_dir.absolute() != expected or attempt_dir.resolve(strict=True) != expected:
        raise RuntimeError("attempt directory is foreign or non-canonical")
    if tuple(registered.get("identity", ())) != _directory_identity(attempt_dir):
        raise RuntimeError("attempt directory identity changed")
    if registered.get("initially_empty") is not True or any(attempt_dir.iterdir()):
        raise RuntimeError("attempt directory is not preregistered empty state")
    if any((attempt_dir / name).exists() for name in ATTEMPT_ARTIFACTS):
        raise RuntimeError("attempt artifact already exists")
    claim = attempt_dir / "CLAIM.json"
    descriptor = os.open(
        claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o444,
    )
    try:
        payload = (json.dumps({
            "schema": "biospur.c2.full_session_root_ab.claim.v1",
            "manifest_sha256": manifest_sha,
            "attempt": 1,
        }, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        if os.write(descriptor, payload) != len(payload):
            raise RuntimeError("short write while claiming one-shot attempt")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return claim


def _clock_owner() -> ContinuousClockOwner:
    path = WORKSPACE / CLOCK_RELATIVE
    encoded, owner_sha = _stable_bytes(path)
    document = json.loads(encoded.decode("utf-8"))
    models = _clock_models_document(
        document,
        source_sha256=_sha256(required_bound_files()["beacon_clock_source"]),
    )
    source_sha = str(document["source_sha256"])
    bindings = []
    for node, model in sorted(models.items()):
        mapping_digest = hashlib.sha256(json.dumps({
            "node": node, "boot_epoch": model.boot_epoch,
            "a_ns_per_us": model.a_ns_per_us, "b_ns": model.b_ns,
            "clock_owner_sha256": owner_sha,
        }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        bindings.append(NodeClockBinding(
            node, model.boot_epoch, "B306_TIMER2", mapping_digest,
            model.a_ns_per_us, model.b_ns, owner_sha, source_sha,
        ))
    if len(bindings) != 10:
        raise RuntimeError("full-session clock owner lacks exact ten-node inventory")
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(bindings))


def _posthoc_action_metrics(
    trajectory: tuple[Any, ...], inventory: ContinuousSessionInventory,
) -> list[dict[str, Any]]:
    """Localize completed-run drift without feeding labels back into behavior."""

    rows: list[dict[str, Any]] = []
    for region in inventory.regions:
        if region.kind != "ACTION":
            continue
        samples = [row for row in trajectory if region.contains_ns(
            int(round(row.measurement_time_s * 1e9)),
            final=region.ordinal == len(inventory.regions) - 1,
        )]
        if not samples:
            rows.append({"action_id": region.action_id, "samples": 0})
            continue
        a = np.asarray([row.a_position_m for row in samples], dtype=np.float64)
        b = np.asarray([row.b_position_m for row in samples], dtype=np.float64)
        rows.append({
            "action_id": region.action_id,
            "samples": len(samples),
            "a_endpoint_displacement_m": float(np.linalg.norm(a[-1] - a[0])),
            "b_endpoint_displacement_m": float(np.linalg.norm(b[-1] - b[0])),
            "a_path_m": float(np.linalg.norm(np.diff(a, axis=0), axis=1).sum()),
            "b_path_m": float(np.linalg.norm(np.diff(b, axis=0), axis=1).sum()),
            "a_max_radius_from_action_start_m": float(
                np.linalg.norm(a - a[0], axis=1).max()),
            "b_max_radius_from_action_start_m": float(
                np.linalg.norm(b - b[0], axis=1).max()),
        })
    return rows


def _verify_prerun(manifest_path: Path, sha_path: Path, output: Path,
                   attempt_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    document, expected = _load_prerun(manifest_path, sha_path)
    scope = document.get("scope", {})
    raw = document.get("raw_stat_only", {})
    if (
        document.get("schema") != PRE_RUN_SCHEMA
        or document.get("attempt_count_before") != 0
        or scope.get("indivisible_regions") != 37
        or scope.get("acquired_actions") != 19
        or scope.get("inter_action_gaps") != 18
        or scope.get("slot_01_marker_only") is not True
        or scope.get("runtime_sensor_admission")
        != "AUTHENTICATED_CONTIGUOUS_FULL_SESSION_BYTE_WINDOW"
        or scope.get("byte_partitions_use") != "SOURCE_ACCOUNTING_ONLY"
        or scope.get("observed_global_time_bounds_use") != "REPORTING_ONLY"
        or scope.get("action_labels_control_behavior") is not False
        or scope.get("action_labels_use") != "POSTHOC_LOCALIZATION_ONLY"
        or scope.get("action_gap_attribution_after_complete_success") is not True
        or scope.get("per_action_metrics") != "POSTHOC_ONLY_AFTER_COMPLETE_RUN"
        or scope.get("per_action_metrics_excluded_from_behavior_scoring_gates") is not True
        or scope.get("resets_or_held_or_interpolated_samples") is not False
        or document.get("product_ready") is not False
        or document.get("scientific_pass") is not False
        or document.get("prior_failed_attempt_sha256") != [
            PRIOR_FAILURE_SHA256, RAW_FAILURE_SHA256, REGION_FAILURE_SHA256,
            TIME_ENVELOPE_FAILURE_SHA256,
        ]
        or document.get("sequence_domain_closure_sha256") != SEQUENCE_CLOSURE_SHA256
        or document.get("label_free_envelope_closure_sha256")
        != LABEL_FREE_CLOSURE_SHA256
        or document.get("source_owned_envelope_closure_sha256")
        != SOURCE_OWNED_CLOSURE_SHA256
        or document.get("limits") != {
            "hard_wall_seconds": 900, "maximum_rss_bytes": 2_147_483_648,
            "maximum_output_bytes": MAX_OUTPUT_BYTES, "maximum_attempts": 1,
            "maximum_threads": 1,
        }
        or document.get("expected_outputs") != ["run/RESULT.json", "run/TRAJECTORY.npz"]
        or output != (WORKSPACE / document.get("output_relative", "")).resolve()
        or output.parent != attempt_dir.resolve(strict=True)
        or output.exists()
        or raw.get("path") != RAW_RELATIVE.as_posix()
        or raw.get("formal_manifest_sha256") != FORMAL_MANIFEST_SHA256
        or raw.get("full_window_start_offset") != FULL_SESSION_START_OFFSET
        or raw.get("full_window_stop_offset") != FULL_SESSION_STOP_OFFSET
        or raw.get("full_window_bytes") != FULL_SESSION_BYTE_COUNT
    ):
        raise RuntimeError("invalid or consumed full-session preregistration")
    required = required_bound_files()
    observed_roles = {str(row.get("role")) for row in document.get("bound_files", [])}
    if observed_roles != set(required) or len(observed_roles) != len(document.get("bound_files", [])):
        raise RuntimeError("pre-run bound-file role closure mismatch")
    for row in document.get("bound_files", []):
        role = row["role"]
        expected_path = required[role].resolve()
        if role == "layout":
            expected_text = (
                "../B306_Part/deployments/current_room_autopos_20260811_183541/"
                "V4IO_LAYOUT.json"
            )
        else:
            expected_text = expected_path.relative_to(WORKSPACE.resolve()).as_posix()
        if row["path"] != expected_text:
            raise RuntimeError(f"pre-run bound-file path is not canonical: {role}")
        path = (WORKSPACE / row["path"]).resolve(strict=True)
        if path != expected_path:
            raise RuntimeError(f"pre-run bound-file path changed: {row['role']}")
        if path == (WORKSPACE / RAW_RELATIVE).resolve():
            raise RuntimeError("raw payload cannot appear in hashed bound files")
        if _sha256(path) != row["sha256"]:
            raise RuntimeError(f"pre-run bound file changed: {row['path']}")
    raw_unresolved = WORKSPACE / RAW_RELATIVE
    raw_stat = raw_unresolved.lstat()
    if stat.S_ISLNK(raw_stat.st_mode) or not stat.S_ISREG(raw_stat.st_mode):
        raise RuntimeError("raw canonical path is a symlink or non-regular file")
    raw_path = raw_unresolved.resolve(strict=True)
    if raw_path != raw_unresolved.absolute():
        raise RuntimeError("raw canonical path resolves outside its registered identity")
    observed = (raw_stat.st_dev, raw_stat.st_ino, raw_stat.st_size, raw_stat.st_mtime_ns)
    if (
        observed != tuple(raw.get("identity", ()))
        or observed != SOURCE_STAT_IDENTITY
        or raw.get("authoritative_full_sha256") != SOURCE_SHA256
        or raw.get("full_window_sha256") != FULL_WINDOW_SHA256
    ):
        raise RuntimeError("raw stat-only preregistration changed")
    return ({"path": str(manifest_path), "sha256": expected,
             "bound_files": len(document["bound_files"])}, document)


def run(*, output: Path, manifest: Path, manifest_sha: Path,
        attempt_dir: Path) -> None:
    verification, document = _verify_prerun(
        manifest, manifest_sha, output, attempt_dir,
    )
    _claim_attempt(document, verification["sha256"], attempt_dir)
    stdout_fd = os.open(attempt_dir / "STDOUT.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    stderr_fd = os.open(attempt_dir / "STDERR.txt", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    os.dup2(stdout_fd, sys.stdout.fileno()); os.dup2(stderr_fd, sys.stderr.fileno())
    os.close(stdout_fd); os.close(stderr_fd)
    output.mkdir(parents=False)
    started = time.monotonic()
    clock_owner = _clock_owner()
    static = DiagnosticC2StaticOwner.from_sealed_archives()
    reader = FullSessionContinuousReader(root=WORKSPACE, clock_owner=clock_owner)
    coordinator = FullSessionRootABCoordinator(static=static, clock_owner=clock_owner)
    summary = coordinator.run(reader)
    inventory = load_continuous_session_inventory(WORKSPACE)
    trajectory = summary.trajectory
    times = np.asarray([row.measurement_time_s for row in trajectory], dtype=np.float64)
    availability = np.asarray([row.availability_time_s for row in trajectory], dtype=np.float64)
    positions_a = np.asarray([row.a_position_m for row in trajectory], dtype=np.float64)
    positions_b = np.asarray([row.b_position_m for row in trajectory], dtype=np.float64)
    if not (len(times) == summary.trajectory_sample_count
            and np.isfinite(times).all() and np.isfinite(availability).all()
            and np.isfinite(positions_a).all() and np.isfinite(positions_b).all()):
        raise RuntimeError("full-session trajectory output is incomplete or non-finite")
    np.savez_compressed(output / "TRAJECTORY.npz", measurement_time_s=times,
                        availability_time_s=availability,
                        position_a_m=positions_a, position_b_m=positions_b)
    reasons = dict(summary.uwb_reasons)
    result = {
        "schema": SCHEMA,
        "qualification": summary.qualification,
        "product_ready": False,
        "scientific_pass": False,
        "scope": {"regions": 37, "one_continuous_session": True,
                  "action_labels_control_behavior": False,
                  "action_labels_use": "POSTHOC_LOCALIZATION_ONLY",
                  "per_action_metrics_excluded_from_behavior_scoring_gates": True},
        "pre_run": verification,
        "reader": _jsonable(summary.reader_audit),
        "conservation": {"events_consumed": summary.events_consumed,
                         "prebootstrap_not_applied": summary.prebootstrap_events,
                         "imu_committed": summary.imu_committed,
                         "trajectory_samples": summary.trajectory_sample_count},
        "global_metrics": {
            "a_endpoint_m": summary.a_endpoint_m, "b_endpoint_m": summary.b_endpoint_m,
            "endpoint_ab_delta_m": float(np.linalg.norm(
                np.asarray(summary.b_endpoint_m) - np.asarray(summary.a_endpoint_m))),
            "a_path_m": summary.a_path_m, "b_path_m": summary.b_path_m,
            "path_ab_delta_m": summary.b_path_m - summary.a_path_m,
            "a_max_radius_m": summary.a_max_radius_m,
            "b_max_radius_m": summary.b_max_radius_m,
            "max_radius_ab_delta_m": summary.b_max_radius_m - summary.a_max_radius_m,
        },
        "uwb": {"attempted": summary.uwb_attempted,
                "accepted": summary.uwb_accepted, "rejected": summary.uwb_rejected,
                "reasons": reasons,
                "position_correction_l1_m": summary.uwb_position_correction_l1_m,
                "velocity_correction_l1_mps": summary.uwb_velocity_correction_l1_mps,
                "nis_outlier_rejections": reasons.get("REJECT_NIS", 0),
                "frozen_limits": {
                    "nis_limit_3d": static.root_config.nis_limit_3d,
                    "maximum_position_influence_m":
                        static.root_config.maximum_position_influence_m,
                    "maximum_velocity_step_mps":
                        FixedLagDriftConfig().maximum_velocity_step_mps,
                    "velocity_bias_mode": "VELOCITY_ONLY_BIAS_DISABLED",
                },
                "events": _jsonable(summary.uwb_audit)},
        "posthoc_action_diagnostics": _posthoc_action_metrics(
            trajectory, inventory,
        ),
        "identity": {"bootstrap_event_id": summary.bootstrap_event_id,
                     "imu_source_chain_sha256": summary.imu_source_chain_sha256,
                     "trajectory_time_sha256": summary.trajectory_time_sha256},
        "runtime": {"wall_seconds": time.monotonic() - started,
                    "maximum_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    "output_limit_bytes": MAX_OUTPUT_BYTES},
        "outputs": {"trajectory": "TRAJECTORY.npz"},
    }
    _write_json_new(output / "RESULT.json", result)
    output_bytes = sum(path.stat().st_size for path in output.iterdir())
    if output_bytes > MAX_OUTPUT_BYTES:
        raise RuntimeError("full-session diagnostic exceeded output-size limit")
    for path in output.iterdir():
        path.chmod(0o444)
    output.chmod(0o555)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pre-run-manifest", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256-file", type=Path, required=True)
    parser.add_argument("--attempt-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(output=args.output.resolve(), manifest=args.pre_run_manifest.resolve(),
        manifest_sha=args.expected_manifest_sha256_file.resolve(),
        attempt_dir=args.attempt_dir.absolute())


if __name__ == "__main__":
    main()
