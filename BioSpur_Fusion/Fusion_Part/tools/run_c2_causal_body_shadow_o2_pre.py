#!/usr/bin/env python3
"""Pre-register and optionally cost-pilot causal C2 body-shadow validation.

``preflight`` opens only sealed pose/clock/layout metadata and artifacts.  It
does not decode a UWB capture.  ``pilot`` is the separately gated four-action,
stride-24 integrity/cost run and performs no model fitting.
"""

from __future__ import annotations

import argparse
import binascii
from collections import Counter
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from contextlib import contextmanager
import hashlib
import json
import math
import multiprocessing
import os

# Set before importing NumPy/SciPy in both the parent and spawn workers.  The
# O2 held-link evaluator parallelizes across processes, never inside BLAS.
for _thread_variable in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ[_thread_variable] = "1"

from pathlib import Path
import resource
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
from biospur_fusion.c2_uwb_calibration.causal_body_shadow_validation import (
    CausalPoseSnapshot,
    HeldRangeLabeler,
    NESTED_MODEL_CONTRACT,
    Native200CommonClock,
    NodeLinkClock,
    PILOT_ACTIONS,
    TRAIN_ACTIONS,
    VALIDATION_ACTIONS,
    causal_shadow_features,
    common_nuisance_values,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    NODE_TO_PROXY_POINT,
    frozen_world_alignment,
)
from biospur_fusion.c2_uwb_calibration.shared_root import (
    SharedRangeLink,
    solve_shared_root,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    _beacon_boundary_bridges,
    _clock_models,
)


ACCEPTED_RESULT = ROOT / (
    "logs/c2_native200_orientation_constrained_biomechanics_v4_20260904/"
    "FINAL_RESULT.json"
)
BASE_REPORT = ROOT / (
    "logs/c2_native200_calibration_v3_20260904/POSE_RESET_QMT_DIAGNOSTIC.json"
)
CLOCK_TABLE = ROOT / (
    "logs/c2_uwb_beacon_clock_20260903_141552/"
    "CLOCK_TABLE_CALIBRATION_ONLY.json"
)
FRONTEND_ARCHIVE = ROOT / (
    "logs/c2_basis_progressive_20260829T102836Z/CONTINUATION_SPRINT/"
    "C2_NONHINGE_TRAINING_REPLAY_001/FRONTEND_RECONSTRUCTION_INPUTS.npz"
)
FRONTEND_MANIFEST = FRONTEND_ARCHIVE.with_suffix(".json")
PELVIS_NODE = "BSFC2CC"
PILOT_STRIDE = 24
PILOT_HARD_S = 300.0
PILOT_DISK_CAP = 50_000_000
PILOT_RSS_CAP_KB = 1_500_000
FULL_RUNTIME_CAP_S = 45.0 * 60.0
FULL_RUNTIME_PROJECTION_MARGIN = 1.25
PAIR_REFERENCE = "BSFEC35/0"
PERF2_MAX_WORKERS = 4
THREAD_LIMIT_ENVIRONMENT = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _accelerated_crc16_ccitt_false(data: bytes) -> int:
    """C-backed, bit-exact implementation of the canonical transport CRC."""

    return int(binascii.crc_hqx(data, 0xFFFF))


@contextmanager
def _accelerated_transport_crc():
    """Temporarily accelerate only the canonical decoder's CRC primitive.

    The canonical COBS/header/payload/CRC checks and UWB parser remain the
    owners.  The replacement implements the same CRC-16/CCITT-FALSE recurrence
    in CPython's C backend and is restored after each raw episode decode.
    """

    import fusion_host_binary as transport

    original = transport.crc16_ccitt_false
    probe = b"123456789"
    if original(probe) != 0x29B1 or _accelerated_crc16_ccitt_false(probe) != 0x29B1:
        raise RuntimeError("accelerated transport CRC failed canonical check")
    transport.crc16_ccitt_false = _accelerated_crc16_ccitt_false
    try:
        yield
    finally:
        if transport.crc16_ccitt_false is not _accelerated_crc16_ccitt_false:
            raise RuntimeError("transport CRC backend changed during decode")
        transport.crc16_ccitt_false = original


def _assert_pilot_rows_byte_equivalent(candidate: Path, reference: Path) -> str:
    """Require exact row/order/numeric equivalence to the sealed O2 pilot."""

    candidate_digest = _sha256(candidate)
    reference_digest = _sha256(reference)
    if candidate_digest != reference_digest:
        raise RuntimeError("optimized pilot rows differ from sealed reference")
    return candidate_digest


def _held_label_chunk(
    payload: tuple[
        tuple[SharedRangeLink, ...],
        tuple[int, ...],
        np.ndarray,
        np.ndarray,
        np.ndarray,
    ]
) -> tuple[tuple[int, Any | None, str | None], ...]:
    """Solve one node's independent held links in a spawn worker."""

    links, indices, anchors, initial_root, velocity = payload
    labeler = HeldRangeLabeler(anchors_m=anchors)
    output = []
    for index in indices:
        try:
            label = labeler.label(
                links,
                target=links[index],
                initial_root_m=initial_root,
                root_velocity_mps=velocity,
            )
        except ValueError as exc:
            output.append((index, None, str(exc)))
        else:
            output.append((index, label, None))
    return tuple(output)


def _collect_indexed_future_results(
    futures: Mapping[Future[Any], int], *, expected_count: int
) -> tuple[tuple[Any | None, str | None], ...]:
    """Collect out-of-order workers into the exact original row ordering."""

    ordered: list[tuple[Any | None, str | None] | None] = [None] * expected_count
    try:
        for future in as_completed(futures):
            for index, value, error in future.result():
                if not 0 <= int(index) < expected_count or ordered[index] is not None:
                    raise RuntimeError("parallel held-label index contract failed")
                ordered[index] = (value, error)
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    if any(value is None for value in ordered):
        raise RuntimeError("parallel held-label result is incomplete")
    return tuple(value for value in ordered if value is not None)


def _parallel_held_labels(
    executor: ProcessPoolExecutor,
    links: tuple[SharedRangeLink, ...],
    *,
    anchors: np.ndarray,
    initial_root: np.ndarray,
    velocity: np.ndarray,
) -> tuple[tuple[Any | None, str | None], ...]:
    """Submit independent same-node LOO calls without changing their inputs."""

    by_node: dict[str, list[int]] = {}
    for index, link in enumerate(links):
        by_node.setdefault(str(link.node), []).append(index)
    payload_common = (
        np.array(anchors, copy=True),
        np.array(initial_root, copy=True),
        np.array(velocity, copy=True),
    )
    futures = {
        executor.submit(
            _held_label_chunk,
            (links, tuple(indices), *payload_common),
        ): ordinal
        for ordinal, indices in enumerate(by_node.values())
    }
    return _collect_indexed_future_results(futures, expected_count=len(links))


def _worker_runtime_probe(delay_s: float = 0.0) -> tuple[int, Mapping[str, str]]:
    """Focused-test probe for spawn isolation and nested-thread caps."""

    if delay_s > 0.0:
        time.sleep(float(delay_s))
    return os.getpid(), {
        name: os.environ.get(name, "") for name in THREAD_LIMIT_ENVIRONMENT
    }


def _worker_record_pid_then_fail(pid_path: str) -> None:
    """Focused-test worker proving exception propagation and teardown."""

    Path(pid_path).write_text(f"{os.getpid()}\n", encoding="utf-8")
    raise RuntimeError("deliberate spawn worker failure")


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


def _verify_sealed_directory(path: Path, expected_seal_sha256: str) -> None:
    """Verify a complete evidence seal before any raw-input call is allowed."""

    seal = path / "SHA256SUMS"
    if not seal.is_file() or _sha256(seal) != str(expected_seal_sha256):
        raise RuntimeError("preflight SHA256SUMS digest mismatch")
    expected: dict[str, str] = {}
    for line in seal.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if separator != "  " or len(digest) != 64 or not relative:
            raise RuntimeError("malformed preflight SHA256SUMS")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RuntimeError("unsafe preflight seal member")
        if relative in expected:
            raise RuntimeError("duplicate preflight seal member")
        expected[relative] = digest
    actual = {
        str(item.relative_to(path))
        for item in path.rglob("*")
        if item.is_file() and item.name != "SHA256SUMS"
    }
    if set(expected) != actual:
        raise RuntimeError("preflight seal member set mismatch")
    for relative, digest in expected.items():
        if _sha256(path / relative) != digest:
            raise RuntimeError(f"preflight sealed file changed: {relative}")


def _load_trajectory(path: Path) -> dict[str, Any]:
    output: dict[str, Any] = {"trajectory": {}}
    with np.load(path, allow_pickle=False) as archive:
        for episode_index in range(len(EPISODES)):
            key = f"{episode_index:02d}"
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
        output["output_coordinate_convention"] = {
            "matrix_world_output_from_internal": np.array(
                archive[
                    "output_coordinates/matrix_world_output_from_internal"
                ]
            ),
            "plane_normal_world_internal": np.array(
                archive["output_coordinates/plane_normal_world_internal"]
            ),
        }
    return output


def _verified_inputs() -> tuple[
    dict[str, Any], dict[str, Native200CommonClock], dict[str, Any]
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
            raise RuntimeError(f"sealed input hash mismatch: {path}")
    if accepted_result.get("sample_rate_hz") != 200.0:
        raise RuntimeError("accepted analytic trajectory is not native 200 Hz")
    if accepted_result.get("pose_interpolation") is not False:
        raise RuntimeError("accepted analytic trajectory used interpolation")
    if not accepted_result.get("mechanism_pass"):
        raise RuntimeError("accepted analytic trajectory mechanism gate failed")
    if base_report.get("physical_time_windows_unchanged") is not True:
        raise RuntimeError("base pose report does not preserve physical time")
    if base_report.get("grid_period_ns") != 5_000_000:
        raise RuntimeError("base pose report is not a 5 ms grid")
    if clock_document.get("source_sha256") != _sha256(
        ROOT / "src/biospur_fusion/c2_uwb_root_world/beacon_clock.py"
    ):
        raise RuntimeError("clock table no longer binds its source")
    clocks = _clock_models(CLOCK_TABLE)
    accepted = _load_trajectory(accepted_path)
    base = _load_trajectory(base_path)
    pelvis_clock = clocks[PELVIS_NODE]
    owners: dict[str, Native200CommonClock] = {}
    episode_audit: dict[str, Any] = {}
    with np.load(FRONTEND_ARCHIVE, allow_pickle=False) as frontend:
        for index, action in enumerate(EPISODES):
            key = f"{index:02d}"
            source_key = f"orientation/{key}/{PELVIS_NODE}/time_us"
            boot_key = f"orientation/{key}/{PELVIS_NODE}/derived_boot_epoch"
            span_key = f"orientation/{key}/{PELVIS_NODE}/contiguous_span_id"
            source_timer_raw = np.array(frontend[source_key], copy=True)
            source_boot_raw = np.array(frontend[boot_key], copy=True)
            source_span_raw = np.array(frontend[span_key], copy=True)
            for member, value in (
                (source_key, source_timer_raw),
                (boot_key, source_boot_raw),
                (span_key, source_span_raw),
            ):
                binding = frontend_manifest["array_bindings"][member]
                if (
                    list(value.shape) != binding["shape"]
                    or str(value.dtype) != binding["dtype"]
                    or _array_sha256(value) != binding["sha256"]
                ):
                    raise RuntimeError(f"frontend member binding failed: {member}")
            source_timer = source_timer_raw.astype(np.int64)
            source_boot = source_boot_raw.astype(np.int64)
            source_span = source_span_raw.astype(np.int64)
            if not np.all(source_boot == int(pelvis_clock.boot_epoch)):
                raise RuntimeError(f"{action}: pelvis boot differs from clock owner")
            accepted_times = np.asarray(
                accepted["trajectory"][key]["pelvis"]["time_root_s"], dtype=float
            )
            common_mask = np.logical_and.reduce([
                np.asarray(accepted["trajectory"][key][segment]["mask"], dtype=bool)
                for segment in SEGMENTS
            ])
            for segment in SEGMENTS:
                accepted_row = accepted["trajectory"][key][segment]
                base_row = base["trajectory"][key][segment]
                if not np.array_equal(
                    accepted_row["time_root_s"], base_row["time_root_s"]
                ):
                    raise RuntimeError(f"{action}/{segment}: IK changed pose time")
                if not np.array_equal(accepted_row["time_root_s"], accepted_times):
                    raise RuntimeError(f"{action}/{segment}: segment clocks differ")
            owner = Native200CommonClock(
                action=action,
                time_root_s=accepted_times,
                source_pelvis_timer_us=source_timer,
                source_contiguous_span_id=source_span,
                common_clock_a_ns_per_us=pelvis_clock.a_ns_per_us,
                common_clock_b_ns=pelvis_clock.b_ns,
                valid_mask=common_mask,
            )
            pelvis_support = clock_document["models"][PELVIS_NODE]
            if (
                int(owner.timer_us[0]) < int(pelvis_support["first_timer_us"])
                or int(owner.timer_us[-1]) > int(pelvis_support["last_timer_us"])
            ):
                raise RuntimeError(f"{action}: pose is outside sealed clock support")
            owners[action] = owner
            episode_audit[action] = owner.audit()
    source_hashes = {
        str(path.relative_to(ROOT)): _sha256(path)
        for path in (
            ACCEPTED_RESULT,
            accepted_path,
            BASE_REPORT,
            base_path,
            FRONTEND_ARCHIVE,
            FRONTEND_MANIFEST,
            CLOCK_TABLE,
            ROOT / "src/biospur_fusion/c2_uwb_root_world/beacon_clock.py",
            ROOT / "src/biospur_fusion/c2_uwb_calibration/shared_root.py",
            ROOT / "src/biospur_fusion/c2_uwb_calibration/antenna_los.py",
            ROOT / "src/biospur_fusion/c2_uwb_calibration/body_occlusion.py",
            ROOT / "src/biospur_fusion/c2_uwb_calibration/frozen_body_proxy.py",
            ROOT / "src/biospur_fusion/c2_uwb_calibration/causal_body_shadow_validation.py",
            ROOT / "tests/test_c2_causal_body_shadow_validation.py",
            Path(__file__).resolve(),
        )
    }
    audit = {
        "source_hashes": source_hashes,
        "clock_table_contract": clock_document["clock_contract"],
        "pose_clock_owner": (
            "accepted time_root_s == hash-bound frontend pelvis BSFC2CC TIMER2 "
            "time_us; mapped by sealed LBD/B306 pelvis ClockModel"
        ),
        "link_clock_owner": (
            "each target node's own sealed LBD/B306 ClockModel maps "
            "strobe_us + measured t_round_us[anchor]/2"
        ),
        "progress_or_formal_bound_scaling": False,
        "episodes": episode_audit,
        "frontend_consumed_members": 3 * len(EPISODES),
        "raw_uwb_opened": False,
    }
    return accepted, owners, audit


def _preregister(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise FileExistsError(args.output)
    started = time.perf_counter()
    accepted, _owners, clock_audit = _verified_inputs()
    del accepted
    contracts = {
        "CLOCK_OWNER.json": {
            "schema": "biospur.c2.o2_pre.clock_owner.v1",
            **clock_audit,
        },
        "SPLIT.json": {
            "schema": "biospur.c2.o2_pre.split.v1",
            "method": "chronological whole-action prefix; frozen before ranges",
            "train_actions": list(TRAIN_ACTIONS),
            "validation_actions": list(VALIDATION_ACTIONS),
            "train_fraction": len(TRAIN_ACTIONS) / len(EPISODES),
            "pilot_actions": list(PILOT_ACTIONS),
            "pilot_split_role": {
                action: ("TRAIN" if action in TRAIN_ACTIONS else "VALIDATION")
                for action in PILOT_ACTIONS
            },
        },
        "MODEL_CONTRACT.json": {
            "schema": "biospur.c2.o2_pre.nested_features.v1",
            **dict(NESTED_MODEL_CONTRACT),
            "feature_equations": {
                "own_inward_probability": "(1-cos(outward_normal,anchor-tag))/2",
                "per_segment_exposure": (
                    "(1-exp(-s_along_ray/0.05m))*exp(-0.5*(clearance/radius)^2)"
                ),
                "family_union": "1-product(1-per_segment_exposure)",
                "B0": "common_nuisance + beta_own_inward*own_inward_probability",
                "B1": "B0 + beta_torso*torso_exposure",
                "B2": "B1 + beta_other_limb*other_limb_exposure",
            },
            "near_field": (
                "origin-connected other-body proxy is AMBIGUOUS and contributes "
                "zero usable shadow; link is retained"
            ),
            "no_hands_or_feet": True,
            "hard_range_deletion": False,
        },
        "ELIGIBILITY.json": {
            "schema": "biospur.c2.o2_pre.eligibility.v1",
            "held_identity": "exact node+anchor occurs exactly once",
            "training_scope": "same node, all current-epoch copies of held identity removed",
            "unique_remaining_anchors_minimum": 4,
            "pre_outcome_geometry_origin": "strict causal prior root+tag offset+dt*velocity",
            "pre_outcome_rank": 3,
            "pre_outcome_condition_maximum": 1e8,
            "eligible_solver_failure": "HARD_FAIL",
            "feature_freeze": "before any current-epoch tracker update",
        },
        "PILOT_CONTRACT.json": {
            "schema": "biospur.c2.o2_pre.pilot.v1",
            "actions": list(PILOT_ACTIONS),
            "epoch_stride": PILOT_STRIDE,
            "purpose": "cost and causal-integrity only; no fit or threshold selection",
            "hard_wall_s": PILOT_HARD_S,
            "disk_cap_bytes": PILOT_DISK_CAP,
            "rss_cap_kb": PILOT_RSS_CAP_KB,
            "maximum_processes": 1,
            "projected_link_row_cap": 5_000,
            "projected_bytes": 8_000_000,
            "full_projection_stop_s": FULL_RUNTIME_CAP_S,
            "full_projection_conservative_margin": FULL_RUNTIME_PROJECTION_MARGIN,
            "command": (
                "timeout --signal=TERM --kill-after=5s 300s env "
                "PYTHONPATH=src:. .venv-v0/bin/python "
                "tools/run_c2_causal_body_shadow_o2_pre.py pilot "
                "--preflight <sealed-preflight> "
                "--expected-preflight-sha256 <exact-sha256sums-digest> "
                "--output <fresh-output>"
            ),
        },
        "ARCHITECTURE.json": {
            "offline_only": (
                "same-node held-anchor LOO solve, label construction, train/validation fit"
            ),
            "future_online": (
                "strict pose floor plus own-facing and fixed body proxy feature evaluation"
            ),
            "future_online_complexity": "O(links*9_body_segments) at 8.33Hz",
            "future_online_memory": "fixed one committed root/pose plus 10x8 link state",
            "future_dependency": False,
            "current_epoch_ranges_used_for_features": False,
            "production_solver_integration": False,
            "scientific_pass_possible": False,
        },
    }
    args.output.mkdir(parents=True)
    for name, value in contracts.items():
        _write_json(args.output / name, value)
    wall = time.perf_counter() - started
    result = {
        "schema": "biospur.c2.o2_pre.preflight_result.v1",
        "status": "READY_FOR_MONITOR_PILOT_REVIEW",
        "clock_owner_pass": True,
        "raw_uwb_opened": False,
        "pilot_started": False,
        "focused_tests": args.focused_tests,
        "wall_s": wall,
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    _write_json(args.output / "RESULT.json", result)
    (args.output / "REPORT.md").write_text(
        "# O2-PRE preflight\n\n"
        "Exact pose/common-clock ownership and leakage-proof mechanism contracts "
        "are frozen. No UWB capture was decoded. The four-action pilot remains "
        "subject to independent monitor GO.\n",
        encoding="utf-8",
    )
    result["output_bytes"] = _directory_bytes(args.output)
    result["seal_file"] = "SHA256SUMS"
    _write_json(args.output / "RESULT.json", result)
    _seal(args.output)
    return result


class _PoseProvider:
    def __init__(
        self,
        *,
        trajectory: dict[str, Any],
        clocks: Mapping[str, Native200CommonClock],
        alignment: np.ndarray,
        geometry: Any,
    ) -> None:
        self.trajectory = trajectory
        self.clocks = clocks
        self.alignment = np.asarray(alignment, dtype=float)
        self.geometry = geometry
        self.config = load_effective_config()
        self.model = display_models(self.config)[1]
        self._cache: dict[tuple[str, int], tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray]]] = {}

    def snapshot(
        self, *, action: str, link_time_ns: float, root_world_m: np.ndarray
    ) -> CausalPoseSnapshot:
        owner = self.clocks[action]
        index = owner.strict_floor(link_time_ns)
        key = f"{EPISODES.index(action):02d}"
        cache_key = (action, index.frame)
        if cache_key not in self._cache:
            internal = joints_for_frame(
                self.trajectory,
                key,
                index.frame,
                self.model,
                self.config,
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
        offsets, normals, joints = self._cache[cache_key]
        return CausalPoseSnapshot(
            action=action,
            frame=index.frame,
            pose_global_ns=index.pose_global_ns,
            query_global_ns=index.link_global_ns,
            pose_age_ns=index.age_ns,
            root_world_m=root_world_m,
            offsets_world_m=offsets,
            normals_world=normals,
            joints_relative_world_m=joints,
        )


def _pilot(args: argparse.Namespace) -> dict[str, Any]:
    # Imports below are intentionally absent from preflight: they own raw UWB
    # decode and are reached only after an independently sealed GO.
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
    _verify_sealed_directory(
        args.preflight, args.expected_preflight_sha256
    )
    _verify_sealed_directory(
        args.equivalence_pilot, args.expected_equivalence_pilot_sha256
    )
    preflight_result = json.loads(
        (args.preflight / "RESULT.json").read_text(encoding="utf-8")
    )
    if preflight_result.get("status") != "READY_FOR_MONITOR_PILOT_REVIEW":
        raise RuntimeError("preflight is not eligible for pilot review")
    started = time.perf_counter()
    trajectory, pose_clocks, input_audit = _verified_inputs()
    clocks = _clock_models(CLOCK_TABLE)
    clock_document = json.loads(CLOCK_TABLE.read_text(encoding="utf-8"))
    bridges = _beacon_boundary_bridges(CLOCK_TABLE)
    anchors, delays, tag_delay, layout_sigma = _load_layout()
    calibration = load_frozen_c2_3a()
    alignment, _forward = frozen_world_alignment(calibration)
    provider = _PoseProvider(
        trajectory=trajectory,
        clocks=pose_clocks,
        alignment=alignment,
        geometry=calibration.geometry,
    )
    node_clocks = {
        node: NodeLinkClock(
            node,
            clock.a_ns_per_us,
            clock.b_ns,
            clock.boot_epoch,
            int(clock_document["models"][node]["first_timer_us"]),
            int(clock_document["models"][node]["last_timer_us"]),
        )
        for node, clock in clocks.items()
    }
    room_initial = np.array([
        float(np.mean(anchors[:, 0])),
        float(np.mean(anchors[:, 1])),
        0.95,
    ])
    args.output.mkdir(parents=True)
    rows_path = args.output / "PILOT_ROWS.jsonl"
    failures: Counter[str] = Counter()
    action_counts: dict[str, Any] = {}
    total_groups = 0
    total_labels = 0
    ages: list[float] = []
    written_row_bytes = 0
    decode_wall_s = 0.0
    held_label_wall_s = 0.0
    tracker_solve_wall_s = 0.0
    spawn_context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=PERF2_MAX_WORKERS,
        mp_context=spawn_context,
    ) as held_executor, rows_path.open("w", encoding="utf-8") as rows_stream:
        for action in PILOT_ACTIONS:
            decode_started = time.perf_counter()
            with _accelerated_transport_crc():
                episode = _load_episode(action, clocks, bridges)
            decode_wall_s += time.perf_counter() - decode_started
            tracker = _tracker(room_initial)
            action_labels = 0
            sampled_groups = episode["groups"][::PILOT_STRIDE]
            for sampled_index, group in enumerate(sampled_groups):
                if time.perf_counter() - started > PILOT_HARD_S:
                    raise TimeoutError("O2-PRE pilot exceeded 300 seconds")
                reference_s = _reference_time(group, clocks)
                predicted_root, dt = _prediction(tracker, reference_s)
                velocity = np.asarray(tracker["velocity"], dtype=float)
                exact: list[tuple[SharedRangeLink, Any, Any, Any]] = []
                identities = []
                for raw in group:
                    node_clock = node_clocks[raw.node]
                    for anchor in _valid_slots(raw):
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
                                action=action,
                                link_time_ns=link_ns,
                                root_world_m=root_at_link,
                            )
                        except ValueError:
                            failures["POSE_AGE_OR_GAP_INELIGIBLE"] += 1
                            continue
                        corrected = (
                            float(raw.ranges_mm[anchor]) / 1000.0
                            - float(delays[anchor])
                            - float(tag_delay)
                        )
                        sigma = _base_sigma(
                            layout_sigma, int(raw.quality[anchor])
                        )
                        link = SharedRangeLink(
                            node=raw.node,
                            anchor=int(anchor),
                            range_m=corrected,
                            tag_offset_world_m=snapshot.offsets_world_m[raw.node],
                            link_dt_s=link_dt,
                            sigma_m=sigma,
                        )
                        feature = causal_shadow_features(
                            node=raw.node,
                            anchor_position_world_m=anchors[anchor],
                            snapshot=snapshot,
                            geometry=calibration.geometry,
                        )
                        origin = root_at_link + snapshot.offsets_world_m[raw.node]
                        nuisance = common_nuisance_values(
                            node=raw.node,
                            anchor=anchor,
                            causal_tag_origin_m=origin,
                            anchor_position_m=anchors[anchor],
                            base_sigma_m=sigma,
                        )
                        exact.append((link, snapshot, feature, nuisance))
                        identities.append((raw.node, int(anchor)))
                        ages.append(snapshot.pose_age_ns * 1e-6)
                if len(identities) != len(set(identities)):
                    raise RuntimeError("duplicate node-anchor identity in pilot epoch")
                links = [row[0] for row in exact]
                label_started = time.perf_counter()
                label_outcomes = _parallel_held_labels(
                    held_executor,
                    tuple(links),
                    anchors=anchors,
                    initial_root=predicted_root,
                    velocity=velocity,
                )
                held_label_wall_s += time.perf_counter() - label_started
                frozen_rows = []
                for (link, snapshot, feature, nuisance), outcome in zip(
                    exact, label_outcomes, strict=True
                ):
                    label, error = outcome
                    if error is not None:
                        failures[error] += 1
                        continue
                    if label is None:
                        raise RuntimeError("parallel held-label outcome is empty")
                    frozen_rows.append({
                        "action": action,
                        "split_role": (
                            "TRAIN" if action in TRAIN_ACTIONS else "VALIDATION"
                        ),
                        "sampled_epoch_index": sampled_index,
                        "source_epoch_index": sampled_index * PILOT_STRIDE,
                        "node": link.node,
                        "anchor": int(link.anchor),
                        "pose_frame": snapshot.frame,
                        "pose_age_ms": snapshot.pose_age_ns * 1e-6,
                        "link_time_ns": snapshot.query_global_ns,
                        "loo_prediction_m": label.predicted_range_m,
                        "signed_innovation_m": label.signed_innovation_m,
                        "eligibility_rank": label.eligibility_rank,
                        "eligibility_condition": label.eligibility_condition,
                        "solver_rank": label.rank,
                        "solver_condition": label.condition,
                        "omitted_identity_count": label.omitted_identity_count,
                        "own_facing_score": feature.own_facing_score,
                        "own_inward_probability": feature.own_inward_probability,
                        "torso_exposure": feature.torso_exposure,
                        "other_limb_exposure": feature.other_limb_exposure,
                        "combined_other_body_exposure": feature.combined_other_body_exposure,
                        "near_field_ambiguous_segments": list(
                            feature.near_field_ambiguous_segments
                        ),
                        "incident_segments_excluded": list(
                            feature.incident_segments_excluded
                        ),
                        "nuisance": dict(nuisance),
                        "feature_current_epoch_range_dependency": False,
                    })
                # Labels and features are fully frozen before this update.
                for row in frozen_rows:
                    if total_labels + 1 > 5_000:
                        raise RuntimeError("O2-PRE pilot row cap exceeded")
                    serialized = json.dumps(
                        row, sort_keys=True, allow_nan=False
                    ) + "\n"
                    encoded_bytes = len(serialized.encode("utf-8"))
                    # Reserve 5 MB for RESULT/REPORT/seal and fail before the
                    # write that would make the cap unrecoverable.
                    if written_row_bytes + encoded_bytes > PILOT_DISK_CAP - 5_000_000:
                        raise RuntimeError("O2-PRE pilot incremental disk cap exceeded")
                    if (
                        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                        > PILOT_RSS_CAP_KB
                    ):
                        raise MemoryError("O2-PRE pilot RSS cap exceeded")
                    rows_stream.write(serialized)
                    written_row_bytes += encoded_bytes
                    total_labels += 1
                action_labels += len(frozen_rows)
                total_groups += 1
                tracker_solve_started = time.perf_counter()
                all_result = solve_shared_root(
                    links,
                    anchors_m=anchors,
                    initial_root_m=predicted_root,
                    root_velocity_mps=velocity,
                )
                tracker_solve_wall_s += (
                    time.perf_counter() - tracker_solve_started
                )
                if all_result.success:
                    _update_tracker(
                        tracker, all_result.root_position_m, reference_s, dt
                    )
                else:
                    failures[f"TRACKER_{all_result.reason}"] += 1
            action_counts[action] = {
                "sampled_groups": len(sampled_groups),
                "successful_labels": action_labels,
            }
    child_peak_rss_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    row_digest = _assert_pilot_rows_byte_equivalent(
        rows_path, args.equivalence_pilot / "PILOT_ROWS.jsonl"
    )
    wall = time.perf_counter() - started
    scale = len(EPISODES) * PILOT_STRIDE / len(PILOT_ACTIONS)
    projected_full_s = wall * scale * FULL_RUNTIME_PROJECTION_MARGIN
    result = {
        "schema": "biospur.c2.o2_pre.pilot_result.v1",
        "status": (
            "COST_INTEGRITY_PILOT_COMPLETE"
            if projected_full_s <= FULL_RUNTIME_CAP_S
            else "BLOCKED_FULL_RUNTIME_PROJECTION"
        ),
        "scientific_pass": False,
        "model_fit_performed": False,
        "actions": list(PILOT_ACTIONS),
        "epoch_stride": PILOT_STRIDE,
        "action_counts": action_counts,
        "sampled_groups": total_groups,
        "successful_held_labels": total_labels,
        "failure_counts": dict(failures),
        "pose_age_ms": {
            "minimum": min(ages) if ages else None,
            "median": float(np.median(ages)) if ages else None,
            "maximum": max(ages) if ages else None,
            "all_in_open_closed_gate": bool(
                ages and min(ages) > 0.0 and max(ages) <= 5.005
            ),
        },
        "raw_uwb_opened": True,
        "current_epoch_ranges_used_for_features": False,
        "labels_frozen_before_tracker_update": True,
        "sealed_pilot_row_equivalence": {
            "byte_identical": True,
            "sha256": row_digest,
            "reference_seal_sha256": args.expected_equivalence_pilot_sha256,
        },
        "transport_crc_backend": {
            "algorithm": "CRC-16/CCITT-FALSE",
            "canonical_decoder_and_parser_unchanged": True,
            "implementation": "binascii.crc_hqx(data, 0xffff)",
            "scope": "raw episode decode context only",
        },
        "offline_parallel_held_label_executor": {
            "enabled": True,
            "online_path_uses_executor": False,
            "start_method": "spawn",
            "max_workers": PERF2_MAX_WORKERS,
            "process_cap_authority": (
                "O2-PERF2 explicitly supersedes sealed v3 maximum_processes=1 "
                "for offline held-link validation only"
            ),
            "task_partition": "one task per node within each sampled epoch",
            "collection_order": "original exact-link index",
            "nested_thread_limits": {
                name: os.environ[name] for name in THREAD_LIMIT_ENVIRONMENT
            },
            "shutdown_waited": True,
        },
        "decode_wall_s": decode_wall_s,
        "held_label_wall_s": held_label_wall_s,
        "tracker_solve_wall_s": tracker_solve_wall_s,
        "other_wall_s": wall - decode_wall_s - held_label_wall_s - tracker_solve_wall_s,
        "wall_s": wall,
        "projected_full_19_action_stride1_wall_s": projected_full_s,
        "full_projection_conservative_margin": FULL_RUNTIME_PROJECTION_MARGIN,
        "full_projection_includes_setup": True,
        "full_projection_cap_s": FULL_RUNTIME_CAP_S,
        "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "child_peak_rss_kb": child_peak_rss_kb,
        "preflight_sha256sums_sha256": _sha256(args.preflight / "SHA256SUMS"),
        "input_audit": input_audit,
    }
    _write_json(args.output / "RESULT.json", result)
    (args.output / "REPORT.md").write_text(
        "# O2-PRE four-action cost/integrity pilot\n\n"
        f"Status: `{result['status']}`. This run fitted no model and cannot "
        "support a body-shadow or scientific claim.\n",
        encoding="utf-8",
    )
    size = _directory_bytes(args.output)
    result["output_bytes_before_final_seal"] = size
    result["disk_gate_pass"] = size <= PILOT_DISK_CAP
    result["rss_gate_pass"] = bool(
        result["peak_rss_kb"] <= PILOT_RSS_CAP_KB
        and result["child_peak_rss_kb"] <= PILOT_RSS_CAP_KB
    )
    if not result["disk_gate_pass"] or not result["rss_gate_pass"]:
        result["status"] = "BLOCKED_RESOURCE_GATE"
    _write_json(args.output / "RESULT.json", result)
    result["seal_file"] = "SHA256SUMS"
    _write_json(args.output / "RESULT.json", result)
    _seal(args.output)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--output", type=Path, required=True)
    preflight.add_argument("--focused-tests", required=True)
    pilot = sub.add_parser("pilot")
    pilot.add_argument("--preflight", type=Path, required=True)
    pilot.add_argument("--expected-preflight-sha256", required=True)
    pilot.add_argument("--equivalence-pilot", type=Path, required=True)
    pilot.add_argument("--expected-equivalence-pilot-sha256", required=True)
    pilot.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for value in (
        args.output,
        getattr(args, "preflight", ROOT),
        getattr(args, "equivalence_pilot", ROOT),
    ):
        value = value.resolve()
        if value != ROOT and ROOT not in value.parents:
            raise SystemExit("all O2-PRE paths must remain in Fusion_Part")
    args.output = args.output.resolve()
    if hasattr(args, "preflight"):
        args.preflight = args.preflight.resolve()
        args.equivalence_pilot = args.equivalence_pilot.resolve()
    result = _preregister(args) if args.mode == "preflight" else _pilot(args)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if not str(result["status"]).startswith("BLOCKED") else 2


if __name__ == "__main__":
    raise SystemExit(main())
