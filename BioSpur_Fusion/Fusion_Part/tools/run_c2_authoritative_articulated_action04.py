#!/usr/bin/env python3
"""Preregister the bounded action04 articulated run without opening raw data."""
from __future__ import annotations

import argparse
from bisect import bisect_left
from dataclasses import asdict, dataclass
from functools import partial
import hashlib
import importlib
import json
import math
from numbers import Integral
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import time
import traceback
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DRY_OUTPUT = (
    ROOT
    / "logs/c2_authoritative_articulated_action04_analytic_jacobian_dry_revision_001_20260907T100000Z"
)
EXPECTED_RAW_OUTPUT = (
    ROOT
    / "logs/c2_authoritative_articulated_action04_raw_revision_011_20260907T120000Z"
)
ANALYTIC_JACOBIAN_PREREGISTRATION = (
    ROOT
    / "logs/c2_authoritative_articulated_analytic_jacobian_preregistration_20260907T090000Z"
)
ANALYTIC_JACOBIAN_PREREGISTRATION_SEAL_SHA256 = (
    "b158d33e119bb49aafef85534278029f69e873e54537c926f6cbe2f38cf154a2"
)
ANALYTIC_JACOBIAN_BENCHMARK = (
    ROOT
    / "logs/c2_authoritative_articulated_analytic_jacobian_benchmark_revision_001_20260907T093000Z"
)
ANALYTIC_JACOBIAN_BENCHMARK_SEAL_SHA256 = (
    "71a3044eca3d720db3872074fb4cdbb4309bbf898826d527dec4c10cbaec1c3f"
)
SINGLE_POSE_OWNER_REVISION = (
    ROOT
    / "logs/c2_authoritative_articulated_action04_single_pose_owner_dry_revision_001_20260907T073000Z"
)
SINGLE_POSE_OWNER_SEAL_SHA256 = (
    "70fb8e1a0edbc99fc8d60e14cad54b4637d41c1cad792ea72b003a4d6b1a809a"
)
PERFORMANCE_BASELINE_REVISION = (
    ROOT
    / "logs/c2_authoritative_articulated_no_raw_performance_baseline_revision_002_20260907T080500Z"
)
PERFORMANCE_BASELINE_SEAL_SHA256 = (
    "6b6d58ca025bf3dbc7fcc6e0e84309a603b514fd5e5e13287f2a97ac7cae4882"
)
PERFORMANCE_OPTIMIZED_REVISION = (
    ROOT
    / "logs/c2_authoritative_articulated_no_raw_performance_optimized_revision_001_20260907T081000Z"
)
PERFORMANCE_OPTIMIZED_SEAL_SHA256 = (
    "a7f4419724ac4b4598ea4a10742ef430991fc290af9e3657b27f97f04bc288d8"
)
ARTICULATED_REVISION = (
    ROOT / "logs/c2_direct_native200_articulated_phase2_revision_002_20260907T065629Z"
)
ARTICULATED_SEAL_SHA256 = (
    "d64b3a51129d0e6883750a0d6c31cbe8fb764d8670ae1859d22afcfa769667f3"
)
NATIVE200_WIRING_REVISION = (
    ROOT / "logs/c2_direct_native200_articulated_wiring_revision_006_20260907T074300Z"
)
NATIVE200_WIRING_SEAL_SHA256 = (
    "282ef87484802a04aab67965648eb7bb73f8420793d05232c6f033f6e8128f03"
)
ACTION04_NO_RAW_PREFLIGHT_REVISION = (
    ROOT
    / "logs/c2_authoritative_articulated_action04_no_raw_preflight_revision_007_20260907T075000Z"
)
ACTION04_NO_RAW_PREFLIGHT_SEAL_SHA256 = (
    "efa1201af6f79e35e5f917ec1e629ad999adea7543a36484cccf96eb920270f7"
)
SOURCE_PAIR_DERIVATIVE_REVISION = (
    ROOT
    / "logs/c2_authoritative_articulated_source_pair_derivative_revision_009_20260907T082449Z"
)
SOURCE_PAIR_DERIVATIVE_SEAL_SHA256 = (
    "dc393a0c49c9bf9970e73888092f3aef0b821af97d7a6ac4aea2a9392ba1b1b8"
)
SOURCE_PAIR_ORDER_PREFLIGHT_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_revision_010_pre_20260907T084604Z"
)
SOURCE_PAIR_ORDER_PREFLIGHT_SEAL_SHA256 = (
    "7c18b5532be7e8b44e100d00e35503267b18d332a58655d73db9df0fa908da7b"
)
TRANSPORT_REVISION = (
    ROOT
    / "logs/c2_robust_authoritative_root_u8_transport_revision_002_20260906T223000Z"
)
TRANSPORT_SEAL_SHA256 = (
    "71e35bf2f31fd6b65b21cc4ab31bd98f7191da9898c87478468e32a7d498436f"
)
CONTACT_PROFILE_REVISION = (
    ROOT / "logs/c2_17_ankle_contact_root_crosscheck_v2_20260904"
)
CONTACT_PROFILE_SEAL_SHA256 = (
    "a416819eb75acb9a76a7adca37eb300716a529bc5b0b76d1876986f054057957"
)
CONTACT_PROFILE_RESULT_SHA256 = (
    "d520cf0c395c5971e90ec2443c8cf3c1017435f1c9f4289ee4fb956d2ff66b93"
)
AXIS_OWNER_REPORT = (
    ROOT / "logs/c2_native200_calibration_v3_20260904/POSE_RESET_QMT_DIAGNOSTIC.json"
)
AXIS_OWNER_REPORT_SHA256 = (
    "cc96b1ba03c0a0ab36ca807ae8c86e0aea4284d0a918981be71df496315c3d4f"
)
AXIS_OWNER_TRAJECTORY_SHA256 = (
    "6f93d7a0efbe3d1bfcc8cef9e2dbd81c1d99ff4ba46b163eab37de9398eada12"
)
AXIS_OWNER_TRAJECTORY = (
    ROOT / "logs/c2_native200_calibration_v3_20260904/POSE_RESET_QMT_TRAJECTORY.npz"
)
AXIS_OWNER_PREFLIGHT = (
    ROOT / "logs/c2_direct_body_shadow_ab_preflight_v6_20260906T101143Z"
)
AXIS_OWNER_PREFLIGHT_SEAL_SHA256 = (
    "a304e22685fed141b239062fdc1e9f557abc3d2c6bdc5d545b93d105d3aed6a4"
)
AXIS_OWNER_ALLOWLIST_SHA256 = (
    "f9226a24a18c64ae60451a20433ba0c64601ecee77761484cc851e34c272413c"
)
FAILED_RAW_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_raw_revision_002_20260907T030000Z"
)
FAILED_RAW_SEAL_SHA256 = (
    "28c4cb112933edbbb7ed80e7cc17173e853d3915ca8fabd6aa685c25dd0aae77"
)
FAILED_INTEGER_TIME_RAW_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_raw_revision_003_20260907T034500Z"
)
FAILED_INTEGER_TIME_RAW_SEAL_SHA256 = (
    "de34dd1727fdd688e0d096661e70ef6dfa2221e2c1ccf884ef250e9a6ee5bb8a"
)
PRIOR_INTEGER_TIME_DRY_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_integer_time_dry_revision_001_20260907T043000Z"
)
PRIOR_INTEGER_TIME_DRY_SEAL_SHA256 = (
    "06a7b6ed98d43f14864d381c102f372187f5b004a23b8415c5aeef148566a50a"
)
PRIOR_SOURCE_TICK_DRY_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_source_tick_dry_revision_001_20260907T050000Z"
)
PRIOR_SOURCE_TICK_DRY_SEAL_SHA256 = (
    "43743d40051569ae3b624a0b21664561ab94bfade0bcb564c3146d524baa430c"
)
FAILED_READONLY_RAW_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_raw_revision_004_20260907T053000Z"
)
FAILED_READONLY_RAW_SEAL_SHA256 = (
    "12e022609149b82bf849b8da902077e0215eb5f096715fcca551054a7608d965"
)
FAILED_READONLY_AUDIT_RAW_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_raw_revision_005_20260907T060000Z"
)
FAILED_READONLY_AUDIT_RAW_SEAL_SHA256 = (
    "dafdbaaa9324a428cef52b9500c2781cb527a4d13a9dd46b5ae37e3832b8a54e"
)
FAILED_TEMPORAL_CLOSURE_RAW_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_raw_revision_006_20260907T063000Z"
)
FAILED_TEMPORAL_CLOSURE_RAW_SEAL_SHA256 = (
    "2044ed2a559469cd0f95774475e92332016f232ae18f9b066af86656503d3cbc"
)
FAILED_DUAL_POSE_OWNER_RAW_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_raw_revision_007_20260907T070000Z"
)
FAILED_DUAL_POSE_OWNER_RAW_SEAL_SHA256 = (
    "9b50919bf901ca53259dc759b4769d5df21086c579787b54fd3b4400102d3034"
)
FAILED_SOURCE_PAIR_RAW_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_raw_revision_008_20260907T100000Z"
)
FAILED_SOURCE_PAIR_RAW_SEAL_SHA256 = (
    "4cc42e006be93ca5d891c6b593d0618c98d8a2ef29901c3e55279aaa9df61f8a"
)
FAILED_SOURCE_BASE_RAW_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_raw_revision_010_20260907T110000Z"
)
FAILED_SOURCE_BASE_RAW_SEAL_SHA256 = (
    "5d3056093346e2a66e2f2e5c431bccf236fb6529c7378a8a2a187e803ad78b4b"
)
FAILED_CLOCK_DRY_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_clock_dry_revision_001_20260907T034500Z"
)
FAILED_CLOCK_DRY_SEAL_SHA256 = (
    "dcfa70ea01f0bcd60e567f9a88ef35d9a78f6a201cf2e2a640fb64c4a268be8f"
)
PRIOR_CLOCK_DRY_REVISION = (
    ROOT / "logs/c2_authoritative_articulated_action04_clock_dry_revision_002_20260907T035000Z"
)
PRIOR_CLOCK_DRY_SEAL_SHA256 = (
    "878e92a54641b7b1acb5ff66f9869d84e683f3da0c9a85c222ac6248b3f99bb1"
)
ACTION_INTERVAL_REVISION = (
    ROOT / "logs/c2_uwb_calibration_held_link_20260903_180908"
)
ACTION_INTERVAL_SEAL_SHA256 = (
    "33a0a24f01752864f4217adb90ae486c36652e000231d4d4c172680a8ea6ee72"
)
ACTION_INTERVAL_LINEAGE_SHA256 = (
    "754dd8daf8e8fc3b21e5251c4cc6c07dacf1ff3bb82b3a122dfd0ee68e465e9d"
)
ACTION04_COMMON_START_NS = 235_093_762_417_886
ACTION04_COMMON_STOP_NS_EXCLUSIVE = 235_123_792_451_482
POSE_ACCEPTED_TRAJECTORY = (
    ROOT / "logs/c2_native200_orientation_constrained_biomechanics_v4_20260904/"
    "ARTICULATED_CALIBRATION_TRAJECTORY.npz"
)
POSE_ACCEPTED_TRAJECTORY_SHA256 = (
    "94f9afb088c7f05a7dbcae0c7d6d2c18be76a6ca32e1d9b96861a8deb7962937"
)
POSE_FRONTEND_ARCHIVE = (
    ROOT / "logs/c2_basis_progressive_20260829T102836Z/CONTINUATION_SPRINT/"
    "C2_NONHINGE_TRAINING_REPLAY_001/FRONTEND_RECONSTRUCTION_INPUTS.npz"
)
POSE_FRONTEND_ARCHIVE_SHA256 = (
    "58f88f9fb59d64a20c9c3c1f29db2309eb2a38e6fb3bd62d982969b51bf54cd7"
)
POSE_FRONTEND_MANIFEST = POSE_FRONTEND_ARCHIVE.with_suffix(".json")
POSE_FRONTEND_MANIFEST_SHA256 = (
    "db1ed458cbc80ad07cd1a885d5ac42498ab6057ea560d6535524ae244b5e7a22"
)
POSE_CLOCK_TABLE = (
    ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/"
    "CLOCK_TABLE_CALIBRATION_ONLY.json"
)
POSE_CLOCK_TABLE_SHA256 = (
    "b3c18d2d0ece3826498d2adc3cd41f3e4412794557f8525adc2f73bfa4ae3a66"
)
EXACT_SOURCE_BASE_POSE_OWNER_DIGEST = hashlib.sha256(
    json.dumps(
        {
            "action": "04_shoulder_left",
            "trajectory_sha256": POSE_ACCEPTED_TRAJECTORY_SHA256,
            "clock_table_sha256": POSE_CLOCK_TABLE_SHA256,
            "lookup": "DirectNative200Clock.exact_tick",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
CORRECTED_OUTPUT_REPORT = (
    ROOT / "logs/c2_native200_orientation_constrained_biomechanics_v4_20260904/"
    "POSE_RESET_QMT_DIAGNOSTIC.json"
)
CORRECTED_OUTPUT_REPORT_SHA256 = (
    "ea69106cbcf14cb4d6e09a3ba40ef27c0f8d31abf0049102649a1e4faf6810a4"
)
TEST_SELECTORS = (
    "tests/test_c2_articulated_range.py",
    "tests/test_c2_authoritative_articulated_fusion.py",
    (
        "tests/test_c2_causal_update_transaction.py::"
        "test_articulated_accept_commits_root_pose_and_temporal_once"
    ),
    (
        "tests/test_c2_owner_bound_async_worker.py::"
        "test_two_group_persistent_async_matches_direct_after_every_event"
    ),
    "tests/test_c2_authoritative_articulated_action04_runner.py",
)
RAW_SUFFIXES = {
    ".bin", ".csv", ".dat", ".h5", ".hdf5", ".jsonl", ".npy", ".npz",
    ".parquet", ".pcap", ".pcapng",
}
RAW_PATH_MARKERS = (
    "/capture/", "/captures/", "/dataset/", "/datasets/", "/raw/",
    "c2_dataset",
)
HXX_PATH_MARKERS = ("/h01/", "/h02/", "h01_", "h02_")
ACTION = "04_shoulder_left"
RAW_START_S = 0.0
RAW_MAXIMUM_DURATION_S = 5.0
RAW_ATTEMPT = 1
ACTION04_RAW_SHA256 = (
    "8b855a577f07bdc8725afe25c0e6720348ff479af2954b7649967e15c19f26ee"
)


@dataclass(frozen=True)
class RawRunRequest:
    action: str
    start_s: float
    duration_s: float
    attempt: int
    authorized_runner_sha256: str


@dataclass(frozen=True)
class RawBranchPayload:
    counts: Mapping[str, int]
    groups: tuple[Mapping[str, Any], ...]
    runtime: Mapping[str, float]
    provenance: Mapping[str, Any]


@dataclass(frozen=True)
class _PreparedNative200Publication:
    source_time_s: float
    source_pair: Any
    previous_base_pose: Any
    current_base_pose: Any


def _strict_native200_pair_before(
    rows: list[Mapping[str, Any]], query_global_ns: float
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Select two actual consecutive source samples strictly before a group."""

    query = float(query_global_ns)
    if not math.isfinite(query):
        raise ValueError("native200 pair query must be finite")
    required = {
        "source_node", "source_boot_epoch", "source_timer_us",
        "source_global_ns", "source_clock_domain",
    }
    if len(rows) < 2 or any(not required.issubset(row) for row in rows):
        raise RuntimeError("missing native200 source tick ownership")
    global_ns = [row["source_global_ns"] for row in rows]
    if any(
        isinstance(value, bool) or not isinstance(value, Integral)
        for value in global_ns
    ):
        raise RuntimeError("native200 source global tick is not an integer")
    if any(right <= left for left, right in zip(global_ns, global_ns[1:])):
        raise RuntimeError("native200 source ticks are duplicate or reordered")
    current_index = bisect_left(global_ns, query) - 1
    if current_index < 1:
        raise RuntimeError("missing two strictly preceding native200 source ticks")
    previous_row = rows[current_index - 1]
    current_row = rows[current_index]
    if (
        previous_row["source_node"] != current_row["source_node"]
        or previous_row["source_clock_domain"] != "B306_TIMER2"
        or current_row["source_clock_domain"] != "B306_TIMER2"
        or previous_row["source_boot_epoch"] != current_row["source_boot_epoch"]
        or current_row["source_timer_us"] - previous_row["source_timer_us"]
        != 5_000
        or current_row["source_global_ns"] >= query
    ):
        raise RuntimeError(
            "native200 source pair is missing/gapped/reordered/cross-domain"
        )
    return previous_row, current_row


def _prepare_native200_publication(
    *, engine: Any, item: Any, event_time: float,
    all_pelvis_rows: tuple[Mapping[str, Any], ...],
    imu_row_by_sequence: Mapping[int, Mapping[str, Any]],
    clock_mapping_owner: Any,
    exact_base_at_source: Callable[[int, int], tuple[Any, Mapping[str, Any]]],
    base_pose_owner_digest: str,
) -> _PreparedNative200Publication | None:
    """Validate the exact decoded source pair before any owner mutation."""

    pose_token = engine.pose.publication_token()
    if not (
        event_time > pose_token.latest_sample_s + 1e-12
        and event_time + 1e-12 >= engine.pose.latest_availability_s
    ):
        return None
    try:
        source_row = imu_row_by_sequence[int(item.sequence)]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("native200 publication source row missing") from error
    required = {
        "time_s", "sequence", "source_node", "source_boot_epoch",
        "source_timer_us", "source_global_ns", "source_clock_domain",
        "source_clock_mapping_digest",
    }
    if not required.issubset(source_row):
        raise RuntimeError("native200 publication source ownership incomplete")
    previous_row, current_row = _strict_native200_pair_before(
        all_pelvis_rows, int(source_row["source_global_ns"]) + 1
    )
    expected_identity = {
        "source_node": clock_mapping_owner.node,
        "source_boot_epoch": clock_mapping_owner.boot_epoch,
        "source_clock_domain": clock_mapping_owner.clock_domain,
        "source_clock_mapping_digest": clock_mapping_owner.digest,
    }
    if any(
        row.get(field) != expected
        for row in (previous_row, current_row)
        for field, expected in expected_identity.items()
    ):
        raise RuntimeError("native200 publication source pair identity mismatch")
    if (
        int(current_row["sequence"]) != int(source_row["sequence"])
        or any(current_row.get(field) != source_row.get(field) for field in required)
    ):
        raise RuntimeError("native200 publication source pair mismatch")
    for row in (previous_row, current_row):
        row_time = float(row["time_s"])
        if (
            not math.isfinite(row_time)
            or round(row_time * 1e9) != int(row["source_global_ns"])
        ):
            raise RuntimeError("native200 publication source/global time mismatch")
    source_pair = engine.native200_source_pair(
        clock_mapping_owner=clock_mapping_owner,
        previous_timer_us=int(previous_row["source_timer_us"]),
        current_timer_us=int(current_row["source_timer_us"]),
        previous_global_ns=int(previous_row["source_global_ns"]),
        current_global_ns=int(current_row["source_global_ns"]),
    )
    previous_index, previous_base = exact_base_at_source(
        source_pair.previous_global_ns, source_pair.previous_timer_us
    )
    current_index, current_base = exact_base_at_source(
        source_pair.current_global_ns, source_pair.current_timer_us
    )
    if (
        int(previous_index.pose_global_ns) != source_pair.previous_global_ns
        or int(current_index.pose_global_ns) != source_pair.current_global_ns
    ):
        raise RuntimeError("native200 exact base/source tick mismatch")
    previous_base_pose = engine.exact_native200_base_pose(
        native200_source_pair=source_pair, role="previous",
        base_rotations_world=previous_base,
        base_pose_owner_digest=base_pose_owner_digest,
    )
    current_base_pose = engine.exact_native200_base_pose(
        native200_source_pair=source_pair, role="current",
        base_rotations_world=current_base,
        base_pose_owner_digest=base_pose_owner_digest,
    )
    return _PreparedNative200Publication(
        float(source_row["time_s"]), source_pair,
        previous_base_pose, current_base_pose,
    )


def _process_native200_timeline_event(
    *, engine: Any, item: Any, event_time: float,
    all_pelvis_rows: tuple[Mapping[str, Any], ...],
    imu_row_by_sequence: Mapping[int, Mapping[str, Any]],
    clock_mapping_owner: Any,
    pending_temporal_rows: list[dict[str, Any]],
    exact_base_at_source: Callable[[int, int], tuple[Any, Mapping[str, Any]]],
    base_pose_owner_digest: str,
) -> bool:
    """Prevalidate a publication pair, then preserve the existing add/sample order."""

    prepared = _prepare_native200_publication(
        engine=engine, item=item, event_time=event_time,
        all_pelvis_rows=all_pelvis_rows,
        imu_row_by_sequence=imu_row_by_sequence,
        clock_mapping_owner=clock_mapping_owner,
        exact_base_at_source=exact_base_at_source,
        base_pose_owner_digest=base_pose_owner_digest,
    )
    temporal_owner = None
    temporal_sample_before = None
    if prepared is not None:
        temporal_owner = getattr(
            engine.pose, "_CausalArticulatedPose__hinge_temporal_owner"
        )
        temporal_sample_before = temporal_owner._snapshot_token()
    engine.add_imu(item.payload)
    if prepared is None:
        return False
    engine.sample_native200_pose(
        time_s=prepared.source_time_s,
        native200_source_pair=prepared.source_pair,
        previous_base_pose=prepared.previous_base_pose,
        current_base_pose=prepared.current_base_pose,
    )
    temporal_sample_after = temporal_owner._snapshot_token()
    _close_pending_temporal_row(
        pending_temporal_rows,
        revision_before=temporal_sample_before.revision,
        revision_after=temporal_sample_after.revision,
        native200_time_s=event_time,
    )
    return True


def _select_temporal_closure_pair(
    rows: list[Mapping[str, Any]],
    final_uwb_availability_ns: int,
    clock_mapping_owner: Any,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Own the first actual consecutive pelvis sample after the last UWB group."""

    if (
        isinstance(final_uwb_availability_ns, bool)
        or not isinstance(final_uwb_availability_ns, Integral)
    ):
        raise ValueError("final UWB availability must be exact integer ns")
    required = {
        "source_node", "source_boot_epoch", "source_timer_us",
        "source_global_ns", "source_clock_domain", "source_clock_mapping_digest",
    }
    if len(rows) < 2 or any(not required.issubset(row) for row in rows):
        raise RuntimeError("missing temporal-closure source ownership")
    times = [row["source_global_ns"] for row in rows]
    if any(
        isinstance(value, bool) or not isinstance(value, Integral)
        for value in times
    ):
        raise RuntimeError("temporal-closure global tick is not an integer")
    if any(right <= left for left, right in zip(times, times[1:])):
        raise RuntimeError("temporal-closure source ticks are duplicate or reordered")
    closure_index = bisect_left(times, int(final_uwb_availability_ns) + 1)
    if closure_index < 1 or closure_index >= len(rows):
        raise RuntimeError("missing actual native200 sample after final UWB group")
    previous = rows[closure_index - 1]
    closure = rows[closure_index]
    if (
        previous["source_global_ns"] > final_uwb_availability_ns
        or closure["source_global_ns"] <= final_uwb_availability_ns
        or previous["source_node"] != clock_mapping_owner.node
        or closure["source_node"] != clock_mapping_owner.node
        or previous["source_boot_epoch"] != clock_mapping_owner.boot_epoch
        or closure["source_boot_epoch"] != clock_mapping_owner.boot_epoch
        or previous["source_clock_domain"] != clock_mapping_owner.clock_domain
        or closure["source_clock_domain"] != clock_mapping_owner.clock_domain
        or previous["source_clock_mapping_digest"] != clock_mapping_owner.digest
        or closure["source_clock_mapping_digest"] != clock_mapping_owner.digest
        or closure["source_timer_us"] - previous["source_timer_us"] != 5_000
        or previous["source_global_ns"]
        != clock_mapping_owner.global_ns(previous["source_timer_us"])
        or closure["source_global_ns"]
        != clock_mapping_owner.global_ns(closure["source_timer_us"])
    ):
        raise RuntimeError("temporal-closure sample is not consecutive/owned")
    return previous, closure


def _close_pending_temporal_row(
    pending_rows: list[dict[str, Any]],
    *,
    revision_before: int,
    revision_after: int,
    native200_time_s: float,
) -> int:
    """Close at most one preceding UWB group with one actual native200 sample."""

    if not pending_rows:
        return 0
    if len(pending_rows) != 1:
        raise RuntimeError("multiple UWB groups preceded one native200 sample")
    pending = pending_rows.pop()
    pending["next_native200_temporal_delta"] = revision_after - revision_before
    pending["next_native200_time_s"] = native200_time_s
    return 1


def _accepted_articulated_pose_frame(
    action_data: Mapping[str, Mapping[str, Any]],
    segments: tuple[str, ...],
    frame: int,
    alignment: Any,
    geometry: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive rotations and FK points once from the accepted pose owner."""

    from biospur_fusion.c2_coupled_progressive.native200_publication_producer import (
        accepted_pose_frame,
    )
    from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS

    if tuple(segments) != tuple(SEGMENTS):
        raise ValueError("accepted frame helper requires complete segment inventory")
    rotations, points, _normals = accepted_pose_frame(
        action_data, int(frame), alignment, geometry,
    )
    return rotations, points


RAW_BRANCH_SOURCE_PATHS = (
    "tools/c2_native200_contact_support.py",
    "tools/run_c2_direct_body_shadow_ab_pilot.py",
    "tools/evaluate_c2_pair_bias_gate.py",
    "tools/run_c2_orientation_constrained_biomechanics.py",
    "src/biospur_fusion/c2_3b_imu_ik/contracts.py",
    "src/biospur_fusion/c2_uwb_calibration/antenna_los.py",
    "src/biospur_fusion/c2_uwb_root_world/offline_unified_contact_wiring.py",
    "src/biospur_fusion/ingest/v47.py",
)
CURRENT_CAUSAL_OWNER_HASHES = {
    "src/biospur_fusion/c2_articulated_biomechanics/hinge_temporal.py": (
        "f53452d84e8d85f6ff6be6946ddb4deef089218f79f4847811f5c1b1f63e232a"
    ),
    "src/biospur_fusion/c2_uwb_calibration/causal_articulated_pose.py": (
        "be10a64a3d90131d1741f6dc3e5c21a7b9e17a1657be368d698aadf58471f149"
    ),
    "src/biospur_fusion/c2_uwb_root_world/causal_update_guard.py": (
        "d675b64550689c7ef446b3de472234419b976713acee684cae3e3f6feab82293"
    ),
    "src/biospur_fusion/c2_uwb_root_world/causal_update_transaction.py": (
        "e20379422089bd788e4d2a2372997a5ade214f4950c8ed67a4d57b67fbd13f28"
    ),
    "src/biospur_fusion/c2_uwb_root_world/authoritative_articulated_fusion.py": (
        "0e1ee936a3c266b7a6716ad76d92f6e082ad1b6b0adf69a8a3ee77ded46024c6"
    ),
    "src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py": (
        "1f3de5f1a2a9c808048bba6f30ac8e9e00e33edbced7378bbb2b150fcb0a08c1"
    ),
}
PHASE2_CAUSAL_OWNER_HASHES = {
    **CURRENT_CAUSAL_OWNER_HASHES,
    "src/biospur_fusion/c2_uwb_calibration/causal_articulated_pose.py": (
        "a861af24d6a9292e4dc85bf79339e54cfa1725e5adf441dbad3a5fed6796d413"
    ),
    "src/biospur_fusion/c2_uwb_root_world/authoritative_articulated_fusion.py": (
        "315117c686705393336c8f6aabff98663ba869b03e4a992080f542f8e4095e86"
    ),
}


def _bind_raw_branch_sources(bound: dict[str, str]) -> dict[str, str]:
    result = dict(bound)
    for relative in RAW_BRANCH_SOURCE_PATHS:
        result[relative] = _sha256(ROOT / relative)
    return dict(sorted(result.items()))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_manifest(directory: Path) -> list[tuple[str, Path, str]]:
    records: list[tuple[str, Path, str]] = []
    for line in (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split(maxsplit=1)
        relative = relative.lstrip("*")
        records.append((relative, (directory / relative).resolve(), digest))
    return records


def _verify_articulated_revision() -> dict[str, str]:
    seal = ARTICULATED_REVISION / "SHA256SUMS"
    if _sha256(seal) != ARTICULATED_SEAL_SHA256:
        raise RuntimeError("native200 articulated phase2 seal mismatch")
    sealed_source_hashes = {
        relative: digest
        for digest, relative in (
            line.split(maxsplit=1)
            for line in (ARTICULATED_REVISION / "SOURCE_HASHES.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        )
    }
    phase2_owned = set(PHASE2_CAUSAL_OWNER_HASHES) - {
        "src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py"
    }
    if any(
        sealed_source_hashes.get(relative) != PHASE2_CAUSAL_OWNER_HASHES[relative]
        for relative in phase2_owned
    ):
        raise RuntimeError("phase2 causal owner provenance mismatch")
    for revision, expected, label in (
        (NATIVE200_WIRING_REVISION, NATIVE200_WIRING_SEAL_SHA256, "revision006"),
        (
            ACTION04_NO_RAW_PREFLIGHT_REVISION,
            ACTION04_NO_RAW_PREFLIGHT_SEAL_SHA256,
            "revision007",
        ),
        (
            SOURCE_PAIR_DERIVATIVE_REVISION,
            SOURCE_PAIR_DERIVATIVE_SEAL_SHA256,
            "revision009",
        ),
        (
            SOURCE_PAIR_ORDER_PREFLIGHT_REVISION,
            SOURCE_PAIR_ORDER_PREFLIGHT_SEAL_SHA256,
            "revision010_pre",
        ),
    ):
        if _sha256(revision / "SHA256SUMS") != expected:
            raise RuntimeError(f"{label} seal mismatch")
    bound: dict[str, str] = {}
    for relative, expected in CURRENT_CAUSAL_OWNER_HASHES.items():
        actual = _sha256(ROOT / relative)
        if actual != expected:
            raise RuntimeError(f"current causal owner mismatch: {relative}")
        bound[relative] = actual
    return dict(sorted(bound.items()))


def _verify_transport_seal() -> None:
    seal = TRANSPORT_REVISION / "SHA256SUMS"
    if _sha256(seal) != TRANSPORT_SEAL_SHA256:
        raise RuntimeError("transport provenance seal mismatch")


def _load_sealed_action04_interval() -> dict[str, Any]:
    """Load the already-derived common-global action interval, never raw events."""

    seal = ACTION_INTERVAL_REVISION / "SHA256SUMS"
    lineage = ACTION_INTERVAL_REVISION / "CALIBRATION_INPUT_LINEAGE.json"
    if _sha256(seal) != ACTION_INTERVAL_SEAL_SHA256:
        raise RuntimeError("action-interval owner seal mismatch")
    members = {
        relative: expected
        for relative, _path, expected in _read_manifest(ACTION_INTERVAL_REVISION)
    }
    if members.get("CALIBRATION_INPUT_LINEAGE.json") != ACTION_INTERVAL_LINEAGE_SHA256:
        raise RuntimeError("action-interval lineage is not owned by its seal")
    if _sha256(lineage) != ACTION_INTERVAL_LINEAGE_SHA256:
        raise RuntimeError("action-interval lineage hash mismatch")
    document = json.loads(lineage.read_text(encoding="utf-8"))
    record = document["episodes"][ACTION]
    if (
        int(record["start_global_ns"]) != ACTION04_COMMON_START_NS
        or int(record["stop_global_ns_exclusive"])
        != ACTION04_COMMON_STOP_NS_EXCLUSIVE
        or record["measurement_time_source"] != "B306_TIMER2"
        or record["selection"] != "LABELLED_ACTION_BOUNDARY_VIA_LBD_HOST_BRIDGE"
    ):
        raise RuntimeError("sealed action04 common-global interval changed")
    return {
        "path": str(lineage.relative_to(ROOT)),
        "sha256": ACTION_INTERVAL_LINEAGE_SHA256,
        "seal_path": str(seal.relative_to(ROOT)),
        "seal_sha256": ACTION_INTERVAL_SEAL_SHA256,
        "start_global_ns": ACTION04_COMMON_START_NS,
        "stop_global_ns_exclusive": ACTION04_COMMON_STOP_NS_EXCLUSIVE,
        "measurement_time_source": record["measurement_time_source"],
    }


def _fraction_to_common_global_ns(
    fraction: float,
    *,
    action_start_global_ns: int,
    action_stop_global_ns: int,
) -> int:
    """Map an articulated fraction into its common-global clock domain."""

    value = float(fraction)
    start = int(action_start_global_ns)
    stop = int(action_stop_global_ns)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("articulated pose fraction must be finite and in [0, 1]")
    if stop <= start:
        raise ValueError("articulated common-global interval must be positive")
    return start + int(round(value * (stop - start)))


def _load_sealed_action04_pose_clock():
    """Build the Action04 strict-floor clock directly from sealed derived owners."""

    import numpy as np

    pose = importlib.import_module("run_c2_direct_body_shadow_ab_pilot")
    if (
        Path(pose.ACCEPTED_RESULT).resolve()
        != POSE_ACCEPTED_TRAJECTORY.parent.joinpath("FINAL_RESULT.json").resolve()
        or Path(pose.FRONTEND_ARCHIVE).resolve() != POSE_FRONTEND_ARCHIVE.resolve()
        or Path(pose.FRONTEND_MANIFEST).resolve() != POSE_FRONTEND_MANIFEST.resolve()
        or Path(pose.CLOCK_TABLE).resolve() != POSE_CLOCK_TABLE.resolve()
    ):
        raise RuntimeError("action04 pose-owner paths changed")
    accepted_result = json.loads(Path(pose.ACCEPTED_RESULT).read_text(encoding="utf-8"))
    base_report = json.loads(Path(pose.BASE_REPORT).read_text(encoding="utf-8"))
    clock_document = json.loads(POSE_CLOCK_TABLE.read_text(encoding="utf-8"))
    frontend_manifest = json.loads(POSE_FRONTEND_MANIFEST.read_text(encoding="utf-8"))
    if (
        (ROOT / accepted_result["calibration_trajectory"]["path"]).resolve()
        != POSE_ACCEPTED_TRAJECTORY.resolve()
        or accepted_result["calibration_trajectory"]["sha256"]
        != POSE_ACCEPTED_TRAJECTORY_SHA256
        or accepted_result.get("sample_rate_hz") != 200.0
        or accepted_result.get("pose_interpolation") is not False
        or accepted_result.get("mechanism_pass") is not True
        or base_report.get("grid_period_ns") != 5_000_000
        or base_report.get("physical_time_windows_unchanged") is not True
        or base_report["frontend"]["archive_sha256"]
        != POSE_FRONTEND_ARCHIVE_SHA256
        or base_report["frontend"]["manifest_sha256"]
        != POSE_FRONTEND_MANIFEST_SHA256
    ):
        raise RuntimeError("sealed native200 pose qualification changed")
    expected_paths = {
        POSE_ACCEPTED_TRAJECTORY: POSE_ACCEPTED_TRAJECTORY_SHA256,
        POSE_FRONTEND_ARCHIVE: POSE_FRONTEND_ARCHIVE_SHA256,
        POSE_FRONTEND_MANIFEST: POSE_FRONTEND_MANIFEST_SHA256,
        POSE_CLOCK_TABLE: POSE_CLOCK_TABLE_SHA256,
    }
    for path, expected in expected_paths.items():
        if _sha256(path) != expected:
            raise RuntimeError(f"common-global pose owner hash mismatch: {path}")
    clock_source = ROOT / "src/biospur_fusion/c2_uwb_root_world/beacon_clock.py"
    if clock_document.get("source_sha256") != _sha256(clock_source):
        raise RuntimeError("sealed clock source binding changed")
    pelvis_clock = pose._clock_models(POSE_CLOCK_TABLE)[pose.PELVIS_NODE]
    key = f"{pose.EPISODES.index(ACTION):02d}"
    with np.load(POSE_FRONTEND_ARCHIVE, allow_pickle=False) as frontend:
        values = {}
        for suffix in ("time_us", "derived_boot_epoch", "contiguous_span_id"):
            member = f"orientation/{key}/{pose.PELVIS_NODE}/{suffix}"
            value = np.array(frontend[member], copy=True)
            binding = frontend_manifest["array_bindings"][member]
            if (
                list(value.shape) != binding["shape"]
                or str(value.dtype) != binding["dtype"]
                or pose._array_sha256(value) != binding["sha256"]
            ):
                raise RuntimeError(f"frontend pose binding failed: {member}")
            values[suffix] = value
    if not np.all(
        values["derived_boot_epoch"].astype(np.int64) == int(pelvis_clock.boot_epoch)
    ):
        raise RuntimeError("pelvis boot differs from clock owner")
    with np.load(POSE_ACCEPTED_TRAJECTORY, allow_pickle=False) as trajectory:
        times = np.array(
            trajectory[f"trajectory/{key}/pelvis/time_root_s"], dtype=float
        )
        valid = np.logical_and.reduce([
            np.array(trajectory[f"trajectory/{key}/{segment}/mask"], dtype=bool)
            for segment in pose.SEGMENTS
        ])
    owner = pose.DirectNative200Clock(
        action=ACTION,
        time_root_s=times,
        source_pelvis_timer_us=values["time_us"].astype(np.int64),
        source_contiguous_span_id=values["contiguous_span_id"].astype(np.int64),
        common_clock_a_ns_per_us=pelvis_clock.a_ns_per_us,
        common_clock_b_ns=pelvis_clock.b_ns,
        valid_mask=valid,
    )
    support = clock_document["models"][pose.PELVIS_NODE]
    if (
        int(owner.timer_us[0]) < int(support["first_timer_us"])
        or int(owner.timer_us[-1]) > int(support["last_timer_us"])
    ):
        raise RuntimeError("action04 pose lies outside sealed clock support")
    return owner, expected_paths


def _verify_common_global_pose_adapter() -> dict[str, Any]:
    """Exercise the exact sealed action04 clock owner without opening raw data."""

    from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import (
        MAXIMUM_POSE_AGE_NS,
    )

    interval = _load_sealed_action04_interval()
    owner, expected_paths = _load_sealed_action04_pose_clock()
    if owner.action != ACTION:
        raise RuntimeError("action04 pose clock owner identity mismatch")

    # Exercise the complete bounded prefix, including both endpoints. The full
    # physical interval remains bound above; this runner is authorized for five
    # seconds only, so its articulated fraction must span that same prefix.
    prefix_stop_ns = ACTION04_COMMON_START_NS + int(
        round(RAW_MAXIMUM_DURATION_S * 1e9)
    )
    rows = []
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        query_ns = _fraction_to_common_global_ns(
            fraction,
            action_start_global_ns=ACTION04_COMMON_START_NS,
            action_stop_global_ns=prefix_stop_ns,
        )
        selected = owner.strict_floor(query_ns)
        if not (
            selected.pose_global_ns < query_ns
            and 0.0 < selected.age_ns <= MAXIMUM_POSE_AGE_NS
        ):
            raise RuntimeError("common-global strict-floor invariant failed")
        rows.append({
            "fraction": fraction,
            "query_global_ns": query_ns,
            "selected_action": owner.action,
            "selected_frame": selected.frame,
            "selected_pose_global_ns": selected.pose_global_ns,
            "age_ns": selected.age_ns,
        })
    first = rows[0]
    if (
        first["query_global_ns"] != ACTION04_COMMON_START_NS
        or first["selected_frame"] != 1032
        or first["selected_pose_global_ns"] != 235_093_758_190_563
        or first["age_ns"] != 4_227_323.0
    ):
        raise RuntimeError("action04 first common-global strict-floor owner changed")
    exact_query_ns = int(first["selected_pose_global_ns"])
    exact = owner.strict_floor(exact_query_ns)
    if not (
        exact.pose_global_ns < exact_query_ns
        and exact.frame < int(first["selected_frame"])
    ):
        raise RuntimeError("strict-floor equality selected an equal/future pose")
    published = owner.exact_tick(
        exact_query_ns,
        source_timer_us=int(owner.timer_us[int(first["selected_frame"])]),
    )
    if not (
        published.frame == int(first["selected_frame"])
        and published.pose_global_ns == exact_query_ns
        and published.age_ns == 0.0
    ):
        raise RuntimeError("native200 exact publication selected the wrong pose")
    if any(row["selected_action"] != ACTION for row in rows):
        raise RuntimeError("action03 tail leaked into action04 pose selection")
    return {
        "interval_owner": interval,
        "bounded_prefix_stop_global_ns": prefix_stop_ns,
        "maximum_pose_age_ns": MAXIMUM_POSE_AGE_NS,
        "fraction_rows": rows,
        "equal_timestamp_reselection": {
            "query_global_ns": exact_query_ns,
            "selected_frame": exact.frame,
            "selected_pose_global_ns": exact.pose_global_ns,
            "strictly_preceding": exact.pose_global_ns
            < exact_query_ns,
        },
        "exact_native200_publication": {
            "query_global_ns": exact_query_ns,
            "selected_frame": published.frame,
            "selected_timer_us": int(owner.timer_us[published.frame]),
            "selected_pose_global_ns": published.pose_global_ns,
            "same_tick": published.pose_global_ns == exact_query_ns,
        },
        "pose_owner_hashes": {
            str(path.relative_to(ROOT)): expected
            for path, expected in expected_paths.items()
        },
        "raw_uwb_opened": False,
        "H01_H02_opened_or_hashed": False,
    }


def _load_sealed_contact_profile_document() -> dict[str, Any]:
    """Load only the sealed derived profile; never reopen its 00/17 raw owners."""

    seal = CONTACT_PROFILE_REVISION / "SHA256SUMS"
    result = CONTACT_PROFILE_REVISION / "RESULT.json"
    if _sha256(seal) != CONTACT_PROFILE_SEAL_SHA256:
        raise RuntimeError("derived contact-profile seal mismatch")
    members = {relative: expected for relative, _path, expected in _read_manifest(
        CONTACT_PROFILE_REVISION
    )}
    if members.get("RESULT.json") != CONTACT_PROFILE_RESULT_SHA256:
        raise RuntimeError("derived contact-profile member is not seal-owned")
    if _sha256(result) != CONTACT_PROFILE_RESULT_SHA256:
        raise RuntimeError("derived contact-profile result mismatch")
    document = json.loads(result.read_text(encoding="utf-8"))
    calibration = document.get("contact_calibration")
    if not isinstance(calibration, dict) or calibration.get("source_actions") != [
        "00_initial_still", "17_final_still"
    ]:
        raise RuntimeError("derived contact-profile provenance is incomplete")
    return calibration


def _validate_axis_owner_document(document: Mapping[str, Any]) -> Path:
    import numpy as np

    expected_joints = {"elbow_left", "elbow_right", "knee_left", "knee_right"}
    axes = document.get("qmt_olsson_hinge_axes")
    if not isinstance(axes, Mapping) or set(axes) != expected_joints:
        raise RuntimeError("axis owner must contain exactly four QMT/Olsson hinges")
    for joint in sorted(expected_joints):
        record = axes[joint]
        parent = np.asarray(record["parent_axis_reset_segment"], dtype=float)
        child = np.asarray(record["child_axis_reset_segment"], dtype=float)
        if (
            parent.shape != (3,) or child.shape != (3,)
            or not np.isfinite(parent).all() or not np.isfinite(child).all()
            or not np.isclose(np.linalg.norm(parent), 1.0, atol=1e-9, rtol=0.0)
            or not np.isclose(np.linalg.norm(child), 1.0, atol=1e-9, rtol=0.0)
            or np.linalg.norm(np.cross(parent, child)) <= 1e-6
        ):
            raise RuntimeError(f"invalid finite/unit/nonparallel axis owner: {joint}")
    trajectory = document.get("trajectory")
    if not isinstance(trajectory, Mapping):
        raise RuntimeError("axis-owner trajectory provenance missing")
    path = (ROOT / str(trajectory.get("path"))).resolve()
    if path != AXIS_OWNER_TRAJECTORY.resolve():
        raise RuntimeError("axis-owner trajectory path mismatch")
    if trajectory.get("sha256") != AXIS_OWNER_TRAJECTORY_SHA256:
        raise RuntimeError("axis-owner trajectory declared hash mismatch")
    return path


def _load_and_fit_sealed_axis_owner() -> tuple[dict[str, Any], Any, dict[str, Any]]:
    """Exercise the exact sealed base-report adapter without calibration raw data."""

    base_owner_module = importlib.import_module("run_c2_direct_body_shadow_ab_pilot")
    if Path(base_owner_module.BASE_REPORT).resolve() != AXIS_OWNER_REPORT.resolve():
        raise RuntimeError("direct-body-shadow BASE_REPORT owner mismatch")
    preflight_seal = AXIS_OWNER_PREFLIGHT / "SHA256SUMS"
    allowlist_path = AXIS_OWNER_PREFLIGHT / "ALLOWLIST.json"
    if _sha256(preflight_seal) != AXIS_OWNER_PREFLIGHT_SEAL_SHA256:
        raise RuntimeError("axis-owner preflight seal mismatch")
    members = {
        relative: expected
        for relative, _path, expected in _read_manifest(AXIS_OWNER_PREFLIGHT)
    }
    if members.get("ALLOWLIST.json") != AXIS_OWNER_ALLOWLIST_SHA256:
        raise RuntimeError("axis-owner allowlist is not owned by preflight seal")
    if _sha256(allowlist_path) != AXIS_OWNER_ALLOWLIST_SHA256:
        raise RuntimeError("axis-owner allowlist hash mismatch")
    allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
    expected_inputs = allowlist["v6_complete_bindings"]["input_hashes"]
    report_relative = str(AXIS_OWNER_REPORT.relative_to(ROOT))
    trajectory_relative = str(AXIS_OWNER_TRAJECTORY.relative_to(ROOT))
    if expected_inputs.get(report_relative) != AXIS_OWNER_REPORT_SHA256:
        raise RuntimeError("axis-owner report absent from sealed allowlist")
    if expected_inputs.get(trajectory_relative) != AXIS_OWNER_TRAJECTORY_SHA256:
        raise RuntimeError("axis-owner trajectory absent from sealed allowlist")
    if _sha256(AXIS_OWNER_REPORT) != AXIS_OWNER_REPORT_SHA256:
        raise RuntimeError("axis-owner base report hash mismatch")
    report = json.loads(AXIS_OWNER_REPORT.read_text(encoding="utf-8"))
    trajectory_path = _validate_axis_owner_document(report)
    if _sha256(trajectory_path) != AXIS_OWNER_TRAJECTORY_SHA256:
        raise RuntimeError("axis-owner base trajectory hash mismatch")
    from biospur_fusion.c2_articulated_biomechanics.model import fit_articulated_model
    from run_c2_orientation_constrained_biomechanics import _load_trajectory
    trajectory = _load_trajectory(trajectory_path)
    model = fit_articulated_model(trajectory, report)
    if set(model) != {"elbow_left", "elbow_right", "knee_left", "knee_right"}:
        raise RuntimeError("axis-owner fit did not produce all four joints")
    return report, model, {
        "report_path": report_relative,
        "report_sha256": AXIS_OWNER_REPORT_SHA256,
        "trajectory_path": trajectory_relative,
        "trajectory_sha256": AXIS_OWNER_TRAJECTORY_SHA256,
        "allowlist_path": str(allowlist_path.relative_to(ROOT)),
        "allowlist_sha256": AXIS_OWNER_ALLOWLIST_SHA256,
        "preflight_seal_sha256": AXIS_OWNER_PREFLIGHT_SEAL_SHA256,
        "joints": sorted(model),
    }


def _verify_failed_raw_nonpromoted() -> dict[str, str]:
    seal = FAILED_RAW_REVISION / "SHA256SUMS"
    if _sha256(seal) != FAILED_RAW_SEAL_SHA256:
        raise RuntimeError("failed raw revision seal mismatch")
    failure = json.loads((FAILED_RAW_REVISION / "FAILURE.json").read_text(encoding="utf-8"))
    if failure.get("status") != "BLOCKED_ACTION04_AUTHORITATIVE_ARTICULATED":
        raise RuntimeError("failed raw revision was unexpectedly promoted")
    return {
        "path": str(FAILED_RAW_REVISION.relative_to(ROOT)),
        "seal_sha256": FAILED_RAW_SEAL_SHA256,
        "status": failure["status"],
        "promoted": False,
    }


def _verify_failed_source_pair_raw_nonpromoted() -> dict[str, Any]:
    seal = FAILED_SOURCE_PAIR_RAW_REVISION / "SHA256SUMS"
    if _sha256(seal) != FAILED_SOURCE_PAIR_RAW_SEAL_SHA256:
        raise RuntimeError("failed source-pair raw revision seal mismatch")
    failure = json.loads(
        (FAILED_SOURCE_PAIR_RAW_REVISION / "FAILURE.json").read_text(
            encoding="utf-8"
        )
    )
    if (
        failure.get("status") != "BLOCKED_ACTION04_AUTHORITATIVE_ARTICULATED"
        or failure.get("failure")
        != "PoseUnavailableError: POSE_UNAVAILABLE:NO_EXACT_VALID_NATIVE200_POSE"
    ):
        raise RuntimeError("failed source-pair raw revision was unexpectedly promoted")
    return {
        "path": str(FAILED_SOURCE_PAIR_RAW_REVISION.relative_to(ROOT)),
        "seal_sha256": FAILED_SOURCE_PAIR_RAW_SEAL_SHA256,
        "status": failure["status"],
        "failure": failure["failure"],
        "promoted": False,
    }


def _verify_failed_source_base_raw_nonpromoted() -> dict[str, Any]:
    seal = FAILED_SOURCE_BASE_RAW_REVISION / "SHA256SUMS"
    if _sha256(seal) != FAILED_SOURCE_BASE_RAW_SEAL_SHA256:
        raise RuntimeError("failed source-base raw revision seal mismatch")
    failure = json.loads(
        (FAILED_SOURCE_BASE_RAW_REVISION / "FAILURE.json").read_text(
            encoding="utf-8"
        )
    )
    if (
        failure.get("status") != "BLOCKED_ACTION04_AUTHORITATIVE_ARTICULATED"
        or failure.get("failure")
        != "PoseUnavailableError: POSE_UNAVAILABLE:NO_EXACT_VALID_NATIVE200_POSE"
    ):
        raise RuntimeError("failed source-base raw revision was unexpectedly promoted")
    return {
        "path": str(FAILED_SOURCE_BASE_RAW_REVISION.relative_to(ROOT)),
        "seal_sha256": FAILED_SOURCE_BASE_RAW_SEAL_SHA256,
        "status": failure["status"],
        "failure": failure["failure"],
        "promoted": False,
        "boundary": "ACTION_FRACTION_ADAPTER_USED_FOR_EXACT_SOURCE_BASE",
    }


def _verify_failed_integer_time_raw_nonpromoted() -> dict[str, str]:
    seal = FAILED_INTEGER_TIME_RAW_REVISION / "SHA256SUMS"
    if _sha256(seal) != FAILED_INTEGER_TIME_RAW_SEAL_SHA256:
        raise RuntimeError("failed integer-time raw revision seal mismatch")
    failure = json.loads(
        (FAILED_INTEGER_TIME_RAW_REVISION / "FAILURE.json").read_text(
            encoding="utf-8"
        )
    )
    if (
        failure.get("status") != "BLOCKED_ACTION04_AUTHORITATIVE_ARTICULATED"
        or failure.get("failure")
        != "ValueError: articulated epoch timing/ownership invalid"
    ):
        raise RuntimeError("failed integer-time raw revision was unexpectedly promoted")
    return {
        "path": str(FAILED_INTEGER_TIME_RAW_REVISION.relative_to(ROOT)),
        "seal_sha256": FAILED_INTEGER_TIME_RAW_SEAL_SHA256,
        "status": failure["status"],
        "failure": failure["failure"],
        "promoted": False,
    }


def _verify_prior_integer_time_dry_nonpromoted() -> dict[str, str]:
    seal = PRIOR_INTEGER_TIME_DRY_REVISION / "SHA256SUMS"
    if _sha256(seal) != PRIOR_INTEGER_TIME_DRY_SEAL_SHA256:
        raise RuntimeError("prior integer-time dry seal mismatch")
    result = json.loads(
        (PRIOR_INTEGER_TIME_DRY_REVISION / "RESULT.json").read_text(
            encoding="utf-8"
        )
    )
    if result.get("status") != "DRY_PREREGISTRATION_PASS":
        raise RuntimeError("prior integer-time dry status changed")
    return {
        "path": str(PRIOR_INTEGER_TIME_DRY_REVISION.relative_to(ROOT)),
        "seal_sha256": PRIOR_INTEGER_TIME_DRY_SEAL_SHA256,
        "status": result["status"],
        "promoted": False,
        "blocker": "BLOCKED_AT_NATIVE200_INTEGER_TICK_OWNER_PROPAGATION",
    }


def _verify_prior_source_tick_dry_nonpromoted() -> dict[str, str]:
    seal = PRIOR_SOURCE_TICK_DRY_REVISION / "SHA256SUMS"
    if _sha256(seal) != PRIOR_SOURCE_TICK_DRY_SEAL_SHA256:
        raise RuntimeError("prior source-tick dry seal mismatch")
    result = json.loads(
        (PRIOR_SOURCE_TICK_DRY_REVISION / "RESULT.json").read_text(
            encoding="utf-8"
        )
    )
    if result.get("status") != "DRY_PREREGISTRATION_PASS":
        raise RuntimeError("prior source-tick dry status changed")
    return {
        "path": str(PRIOR_SOURCE_TICK_DRY_REVISION.relative_to(ROOT)),
        "seal_sha256": PRIOR_SOURCE_TICK_DRY_SEAL_SHA256,
        "status": result["status"],
        "promoted": False,
        "blocker": "BLOCKED_AT_DECODED_IMU_GLOBAL_TIME_OWNERSHIP_ADAPTER",
    }


def _verify_failed_readonly_raw_nonpromoted() -> dict[str, Any]:
    seal = FAILED_READONLY_RAW_REVISION / "SHA256SUMS"
    if _sha256(seal) != FAILED_READONLY_RAW_SEAL_SHA256:
        raise RuntimeError("failed read-only raw revision seal mismatch")
    failure = json.loads(
        (FAILED_READONLY_RAW_REVISION / "FAILURE.json").read_text(
            encoding="utf-8"
        )
    )
    if (
        failure.get("status") != "BLOCKED_ACTION04_AUTHORITATIVE_ARTICULATED"
        or failure.get("failure") != "ValueError: buffer source array is read-only"
        or failure.get("hxx_opened_or_hashed") is not False
    ):
        raise RuntimeError("failed read-only raw revision was unexpectedly promoted")
    return {
        "path": str(FAILED_READONLY_RAW_REVISION.relative_to(ROOT)),
        "seal_sha256": FAILED_READONLY_RAW_SEAL_SHA256,
        "status": failure["status"],
        "failure": failure["failure"],
        "promoted": False,
    }


def _verify_failed_readonly_audit_raw_nonpromoted() -> dict[str, Any]:
    seal = FAILED_READONLY_AUDIT_RAW_REVISION / "SHA256SUMS"
    if _sha256(seal) != FAILED_READONLY_AUDIT_RAW_SEAL_SHA256:
        raise RuntimeError("failed read-only audit raw revision seal mismatch")
    failure = json.loads(
        (FAILED_READONLY_AUDIT_RAW_REVISION / "FAILURE.json").read_text(
            encoding="utf-8"
        )
    )
    traceback_text = "".join(failure.get("traceback", ()))
    if (
        failure.get("status") != "BLOCKED_ACTION04_AUTHORITATIVE_ARTICULATED"
        or failure.get("failure") != "ValueError: buffer source array is read-only"
        or failure.get("last_stage_marker") != "group.0.admit.complete"
        or "corrected_proxy_points" not in traceback_text
        or "Rotation.from_rotvec(delta)" not in traceback_text
        or failure.get("hxx_opened_or_hashed") is not False
    ):
        raise RuntimeError("failed read-only audit raw revision was unexpectedly promoted")
    return {
        "path": str(FAILED_READONLY_AUDIT_RAW_REVISION.relative_to(ROOT)),
        "seal_sha256": FAILED_READONLY_AUDIT_RAW_SEAL_SHA256,
        "status": failure["status"],
        "failure": failure["failure"],
        "last_stage_marker": failure["last_stage_marker"],
        "promoted": False,
    }


def _verify_failed_temporal_closure_raw_nonpromoted() -> dict[str, Any]:
    seal = FAILED_TEMPORAL_CLOSURE_RAW_REVISION / "SHA256SUMS"
    if _sha256(seal) != FAILED_TEMPORAL_CLOSURE_RAW_SEAL_SHA256:
        raise RuntimeError("failed temporal-closure raw revision seal mismatch")
    failure = json.loads(
        (FAILED_TEMPORAL_CLOSURE_RAW_REVISION / "FAILURE.json").read_text(
            encoding="utf-8"
        )
    )
    if (
        failure.get("status") != "BLOCKED_ACTION04_AUTHORITATIVE_ARTICULATED"
        or failure.get("failure")
        != "RuntimeError: final UWB group lacks a subsequent native200 pose sample"
        or failure.get("last_stage_marker") != "group.40.audit.complete"
        or failure.get("hxx_opened_or_hashed") is not False
    ):
        raise RuntimeError("failed temporal-closure raw revision was unexpectedly promoted")
    return {
        "path": str(FAILED_TEMPORAL_CLOSURE_RAW_REVISION.relative_to(ROOT)),
        "seal_sha256": FAILED_TEMPORAL_CLOSURE_RAW_SEAL_SHA256,
        "status": failure["status"],
        "failure": failure["failure"],
        "last_stage_marker": failure["last_stage_marker"],
        "promoted": False,
    }


def _verify_failed_dual_pose_owner_raw_nonpromoted() -> dict[str, Any]:
    seal = FAILED_DUAL_POSE_OWNER_RAW_REVISION / "SHA256SUMS"
    if _sha256(seal) != FAILED_DUAL_POSE_OWNER_RAW_SEAL_SHA256:
        raise RuntimeError("failed dual-pose-owner raw revision seal mismatch")
    result = json.loads(
        (FAILED_DUAL_POSE_OWNER_RAW_REVISION / "RESULT.json").read_text(
            encoding="utf-8"
        )
    )
    groups = json.loads(
        (FAILED_DUAL_POSE_OWNER_RAW_REVISION / "GROUPS.json").read_text(
            encoding="utf-8"
        )
    )
    foothold_rejections = [
        row for row in groups
        if row.get("reason")
        == "ARTICULATED_SOLVE_REJECTED:PROJECTED_FOOTHOLD_GATE_FAILURE"
    ]
    if (
        result.get("status") != "BLOCKED_ACTION04_AUTHORITATIVE_ARTICULATED"
        or result.get("counts", {}).get("imu_temporal_closure") != 1
        or len(foothold_rejections) != 40
        or result.get("hxx_opened_or_hashed") is not False
    ):
        raise RuntimeError("failed dual-pose-owner raw revision was unexpectedly promoted")
    return {
        "path": str(FAILED_DUAL_POSE_OWNER_RAW_REVISION.relative_to(ROOT)),
        "seal_sha256": FAILED_DUAL_POSE_OWNER_RAW_SEAL_SHA256,
        "status": result["status"],
        "foothold_rejections": len(foothold_rejections),
        "promoted": False,
    }


def _verify_no_raw_performance_boundary() -> dict[str, Any]:
    """Bind the single allowed FK optimization and its exact 41-group parity."""

    import numpy as np

    for revision, expected in (
        (SINGLE_POSE_OWNER_REVISION, SINGLE_POSE_OWNER_SEAL_SHA256),
        (PERFORMANCE_BASELINE_REVISION, PERFORMANCE_BASELINE_SEAL_SHA256),
        (PERFORMANCE_OPTIMIZED_REVISION, PERFORMANCE_OPTIMIZED_SEAL_SHA256),
    ):
        if _sha256(revision / "SHA256SUMS") != expected:
            raise RuntimeError("no-raw performance owner seal mismatch")
    baseline_result = json.loads(
        (PERFORMANCE_BASELINE_REVISION / "RESULT.json").read_text(encoding="utf-8")
    )
    optimized_result = json.loads(
        (PERFORMANCE_OPTIMIZED_REVISION / "RESULT.json").read_text(encoding="utf-8")
    )
    with np.load(
        PERFORMANCE_BASELINE_REVISION / "BASELINE_OUTPUTS.npz", allow_pickle=False
    ) as baseline, np.load(
        PERFORMANCE_OPTIMIZED_REVISION / "OPTIMIZED_OUTPUTS.npz", allow_pickle=False
    ) as optimized:
        exact_fields = ("frames", "queries_ns", "points", "outputs", "lengths")
        exact = all(
            np.array_equal(baseline[field], optimized[field], equal_nan=True)
            for field in exact_fields
        )
    if (
        baseline_result.get("groups") != 41
        or optimized_result.get("groups") != 41
        or baseline_result.get("imu_inventory_shape", {}).get("total") != 1007
        or optimized_result.get("imu_inventory_shape", {}).get("total") != 1007
        or baseline_result.get("raw_opened") is not False
        or optimized_result.get("raw_opened") is not False
        or baseline_result.get("hxx_opened") is not False
        or optimized_result.get("hxx_opened") is not False
        or not exact
        or optimized_result["service_p99_ms"] >= baseline_result["service_p99_ms"]
    ):
        raise RuntimeError("no-raw performance evidence is not an exact improvement")
    return {
        "single_pose_owner_seal_sha256": SINGLE_POSE_OWNER_SEAL_SHA256,
        "baseline_seal_sha256": PERFORMANCE_BASELINE_SEAL_SHA256,
        "optimized_seal_sha256": PERFORMANCE_OPTIMIZED_SEAL_SHA256,
        "groups": 41,
        "imu_total": 1007,
        "all_group_outputs_exact": exact,
        "baseline_p99_ms": baseline_result["service_p99_ms"],
        "optimized_p99_ms": optimized_result["service_p99_ms"],
        "baseline_max_ms": baseline_result["service_max_ms"],
        "optimized_max_ms": optimized_result["service_max_ms"],
        "p99_gate_pass": optimized_result["service_p99_ms"] < 150.0,
        "max_gate_pass": optimized_result["service_max_ms"] < 200.0,
        "performance_stage_promoted_to_raw": False,
    }


def _verify_analytic_jacobian_benchmark() -> dict[str, Any]:
    for revision, expected in (
        (
            ANALYTIC_JACOBIAN_PREREGISTRATION,
            ANALYTIC_JACOBIAN_PREREGISTRATION_SEAL_SHA256,
        ),
        (ANALYTIC_JACOBIAN_BENCHMARK, ANALYTIC_JACOBIAN_BENCHMARK_SEAL_SHA256),
    ):
        if _sha256(revision / "SHA256SUMS") != expected:
            raise RuntimeError("analytic Jacobian evidence seal mismatch")
    result = json.loads(
        (ANALYTIC_JACOBIAN_BENCHMARK / "RESULT.json").read_text(encoding="utf-8")
    )
    if (
        result.get("groups") != 41
        or result.get("imu_inventory_shape", {}).get("total") != 1007
        or result.get("successes") != 41
        or result.get("reasons_exact") is not True
        or result.get("fixed_root_exact") is not True
        or result.get("lengths_exact") is not True
        or result.get("maximum_packed_output_delta", math.inf) > 1e-7
        or result.get("p99_ms", math.inf) >= 150.0
        or result.get("max_ms", math.inf) >= 200.0
        or result.get("effective_utilization", math.inf) >= 1.0
        or result.get("raw_opened") is not False
        or result.get("hxx_opened") is not False
    ):
        raise RuntimeError("analytic Jacobian benchmark gate failed")
    return {
        "preregistration_seal_sha256": (
            ANALYTIC_JACOBIAN_PREREGISTRATION_SEAL_SHA256
        ),
        "benchmark_seal_sha256": ANALYTIC_JACOBIAN_BENCHMARK_SEAL_SHA256,
        "groups": result["groups"],
        "imu_total": result["imu_inventory_shape"]["total"],
        "maximum_output_delta": result["maximum_packed_output_delta"],
        "p99_ms": result["p99_ms"],
        "max_ms": result["max_ms"],
        "effective_utilization": result["effective_utilization"],
        "raw_promoted": False,
    }


def _verify_failed_clock_dry_nonpromoted() -> dict[str, Any]:
    seal = FAILED_CLOCK_DRY_REVISION / "SHA256SUMS"
    if _sha256(seal) != FAILED_CLOCK_DRY_SEAL_SHA256:
        raise RuntimeError("failed clock dry revision seal mismatch")
    result = json.loads(
        (FAILED_CLOCK_DRY_REVISION / "RESULT.json").read_text(encoding="utf-8")
    )
    if (
        result.get("status") != "DRY_PREREGISTRATION_FAIL"
        or result.get("failures") != ["HXX_file_open_detected"]
        or result.get("raw_data_opened") is not False
    ):
        raise RuntimeError("failed clock dry revision was unexpectedly promoted")
    return {
        "path": str(FAILED_CLOCK_DRY_REVISION.relative_to(ROOT)),
        "seal_sha256": FAILED_CLOCK_DRY_SEAL_SHA256,
        "status": result["status"],
        "failures": result["failures"],
        "raw_data_opened": False,
        "promoted": False,
    }


def _verify_prior_clock_dry() -> dict[str, Any]:
    seal = PRIOR_CLOCK_DRY_REVISION / "SHA256SUMS"
    if _sha256(seal) != PRIOR_CLOCK_DRY_SEAL_SHA256:
        raise RuntimeError("prior common-global clock dry seal mismatch")
    result = json.loads(
        (PRIOR_CLOCK_DRY_REVISION / "RESULT.json").read_text(encoding="utf-8")
    )
    if (
        result.get("status") != "DRY_PREREGISTRATION_PASS"
        or result.get("raw_data_opened") is not False
        or result.get("hxx_opened_or_hashed") is not False
    ):
        raise RuntimeError("prior common-global clock dry is not qualified")
    return {
        "path": str(PRIOR_CLOCK_DRY_REVISION.relative_to(ROOT)),
        "seal_sha256": PRIOR_CLOCK_DRY_SEAL_SHA256,
        "status": result["status"],
    }


def _process_group_members(process_group: int) -> list[int]:
    members: list[int] = []
    for item in Path("/proc").iterdir():
        if not item.name.isdigit():
            continue
        try:
            if os.getpgid(int(item.name)) == process_group:
                members.append(int(item.name))
        except (ProcessLookupError, PermissionError):
            continue
    return sorted(members)


def _trace_audit(trace_path: Path) -> dict[str, Any]:
    quoted = re.compile(r'"((?:[^"\\]|\\.)*)"')
    opened: list[str] = []
    for line in trace_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = quoted.search(line)
        if match:
            opened.append(bytes(match.group(1), "utf-8").decode("unicode_escape"))
    raw_paths: list[str] = []
    hxx_paths: list[str] = []
    allowed_derived = {
        str(AXIS_OWNER_TRAJECTORY.resolve()),
        str(POSE_ACCEPTED_TRAJECTORY.resolve()),
        str(POSE_FRONTEND_ARCHIVE.resolve()),
    }
    for value in opened:
        lowered = value.lower()
        if value not in allowed_derived and (
            Path(lowered).suffix in RAW_SUFFIXES or any(
            marker in lowered for marker in RAW_PATH_MARKERS
            )
        ):
            raw_paths.append(value)
        if any(marker in lowered for marker in HXX_PATH_MARKERS):
            hxx_paths.append(value)
    return {
        "open_events": len(opened),
        "raw_paths": sorted(set(raw_paths)),
        "hxx_paths": sorted(set(hxx_paths)),
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_fresh_json_atomic(path: Path, value: Any) -> None:
    """Publish one fresh failure record without touching estimator owners."""

    if path.exists():
        raise RuntimeError(f"fresh evidence member already exists: {path.name}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise RuntimeError("stale failure evidence temporary exists")
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        with temporary.open("xb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.link(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _failure_evidence(
    error: BaseException,
    *,
    stage_trace: tuple[str, ...],
    runner_sha256: str,
) -> dict[str, Any]:
    formatted = tuple(traceback.format_exception(error))
    return {
        "status": "BLOCKED_ACTION04_AUTHORITATIVE_ARTICULATED",
        "failure": f"{type(error).__name__}: {error}",
        "failure_type": type(error).__name__,
        "failure_message": str(error),
        "last_stage_marker": stage_trace[-1] if stage_trace else None,
        "stage_trace": list(stage_trace),
        "traceback": list(formatted),
        "runner_sha256": runner_sha256,
        "hxx_opened_or_hashed": False,
        "calibrated_R": False,
        "scientific_pass": False,
        "product_pass": False,
    }


def _seal(output: Path, external_paths: tuple[Path, ...]) -> str:
    members = tuple(sorted(path for path in output.iterdir() if path.name != "SHA256SUMS"))
    members += external_paths
    lines = [
        f"{_sha256(path)}  {os.path.relpath(path, output)}" for path in members
    ]
    seal = output / "SHA256SUMS"
    seal.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _sha256(seal)


def _validate_raw_request(request: RawRunRequest) -> None:
    if request.action != ACTION:
        raise ValueError("only action04 is preregistered")
    if request.start_s != RAW_START_S:
        raise ValueError("raw start must be exactly zero seconds")
    if not math.isfinite(request.duration_s) or not (
        0.0 < request.duration_s <= RAW_MAXIMUM_DURATION_S
    ):
        raise ValueError("raw duration must be finite and in (0, 5] seconds")
    if request.attempt != RAW_ATTEMPT:
        raise ValueError("raw attempt is fixed at one; retry is forbidden")
    if not re.fullmatch(r"[0-9a-f]{64}", request.authorized_runner_sha256):
        raise ValueError("externally authorized runner SHA256 is required")
    if request.authorized_runner_sha256 != _sha256(Path(__file__).resolve()):
        raise ValueError("current runner differs from externally authorized SHA256")


def _evaluate_raw_payload(
    request: RawRunRequest,
    loader: Callable[[RawRunRequest], RawBranchPayload],
) -> tuple[RawBranchPayload, dict[str, bool]]:
    """Traverse the exact production gate path after fail-closed request checks."""

    _validate_raw_request(request)
    payload = loader(request)
    counts = payload.counts
    expected_metric_imu = int(round(request.duration_s / 0.005))
    inventory = {
        "metric_imu": counts.get("imu_metric") == expected_metric_imu,
        "context_imu": counts.get("imu_context") == 6,
        "temporal_closure_imu": counts.get("imu_temporal_closure") == 1,
        "groups_present": int(counts.get("groups", 0)) > 0,
        "ten_sweeps_per_group": (
            counts.get("sweeps") == 10 * int(counts.get("groups", -1))
        ),
        "links_bounded": (
            4 * int(counts.get("sweeps", 0))
            <= int(counts.get("links", -1))
            <= 8 * int(counts.get("sweeps", 0))
        ),
    }
    if request.duration_s == 5.0:
        inventory.update({
            "frozen_first5_groups": counts.get("groups") == 41,
            "frozen_first5_sweeps": counts.get("sweeps") == 410,
            "frozen_first5_links": counts.get("links") == 3266,
        })
    group_rows = payload.groups
    def _atomic(row: Mapping[str, Any]) -> bool:
        expected = 1 if row["accepted"] else 0
        return (
            row["root_revision_after"] - row["root_revision_before"] == expected
            and row["pose_revision_after"] - row["pose_revision_before"] == expected
            and row["robust_revision_after"] - row["robust_revision_before"] == expected
            and row["temporal_revision_after"] == row["temporal_revision_before"]
            and row["contact_owner_digest_before"] == row["contact_owner_digest_after"]
        )

    def _partition(row: Mapping[str, Any]) -> bool:
        direct = set(row["direct_nodes"])
        propagated = set(row["propagated_nodes"])
        inventory = set(row["node_inventory"])
        trusted = set(row["trusted_nodes"])
        return (
            direct.isdisjoint(propagated)
            and direct | propagated == inventory
            and (not row["accepted"] or direct == trusted)
        )

    gates = {
        **inventory,
        "one_result_per_group": len(group_rows) == int(counts.get("groups", -1)),
        "u1_exactly_once_per_group": all(row["u1_calls"] == 1 for row in group_rows),
        "fixed_root_conditioning_exact": all(
            len(row["trusted_nodes"]) == 1
            or (row["fixed_root_solver_calls"] and all(row["fixed_root_solver_calls"]))
            for row in group_rows
        ),
        "atomic_revision_transition": all(_atomic(row) for row in group_rows),
        "direct_propagated_partition": all(_partition(row) for row in group_rows),
        "bias_selected_only": all(
            set(row["changed_bias_nodes"]).issubset(row["trusted_nodes"])
            for row in group_rows
        ),
        "left_right_and_bone_lengths": all(
            row["identity_error_maximum_m"] <= 1e-12
            and row["bone_length_error_maximum_m"] <= 1e-10
            for row in group_rows
        ),
        "stance_no_slide_or_swing": all(
            row["contact_constraint_count"] == 0
            or not row["accepted"]
            or row["maximum_foothold_residual_m"] <= row["contact_limit_m"] + 1e-12
            for row in group_rows
        ),
        "finite_root_posterior": all(
            all(math.isfinite(value) for value in row["root_position_flat"])
            and all(math.isfinite(value) for value in row["root_covariance_flat"])
            for row in group_rows
        ),
        "joint_covariance_unavailable": all(
            row["joint_covariance_status"] == "UNAVAILABLE_NOT_PROPAGATED"
            for row in group_rows
        ),
        "cross_covariance_unavailable": all(
            row["root_joint_cross_covariance_status"] == "UNAVAILABLE_NOT_PROPAGATED"
            for row in group_rows
        ),
        "native200_exact": all(row["native_period_s"] == 0.005 for row in group_rows),
        "strict_prelink_pose": all(
            all(pose < query for pose, query in row["pose_query_ns"])
            for row in group_rows
        ),
        "contact_snapshot_every_group": all(
            row["contact_snapshot_supplied"] for row in group_rows
        ),
        "next_native200_advances_temporal": all(
            row["next_native200_temporal_delta"] == 1 for row in group_rows
        ),
        "sources_unchanged": (
            payload.provenance["source_sha256_before"]
            == payload.provenance["source_sha256_after"]
        ),
        "data_owners_unchanged": (
            payload.provenance["data_sha256_before"]
            == payload.provenance["data_sha256_after"]
        ),
        "wall_under_300s": float(payload.runtime["wall_s"]) < 300.0,
        "rss_under_300mb": float(payload.runtime["maximum_rss_kib"]) < 300_000.0,
        "group_service_p99_under_150ms": (
            float(payload.runtime["group_service_p99_ms"]) < 150.0
        ),
        "group_service_maximum_under_200ms": (
            float(payload.runtime["group_service_maximum_ms"]) < 200.0
        ),
        "effective_utilization_under_one": (
            float(payload.runtime["effective_utilization"]) < 1.0
        ),
    }
    return payload, gates


def _rotation_matrix(quaternion_wxyz, alignment):
    from biospur_fusion.c2_uwb_calibration.antenna_los import rotation_from_wxyz

    return alignment @ rotation_from_wxyz(quaternion_wxyz)


def _real_action04_loader(
    request: RawRunRequest, *, _decoded_imu_sentinel=None,
    _stage_observer: Callable[[str], None] | None = None,
) -> RawBranchPayload:
    """Load and execute the real bounded action04 stream exactly once.

    Imports are deliberately local: validation of action/start/duration/attempt and
    output freshness occurs before this function can import a dataset-owning module.
    """

    observation_overhead_s = 0.0

    def stage(name: str) -> None:
        nonlocal observation_overhead_s
        if _stage_observer is not None:
            observation_started = time.monotonic()
            _stage_observer(str(name))
            observation_overhead_s += time.monotonic() - observation_started

    stage("loader.enter")
    import gc
    import numpy as np

    pose_support = importlib.import_module("run_c2_direct_body_shadow_ab_pilot")
    action_support = importlib.import_module("evaluate_c2_pair_bias_gate")
    native_support = importlib.import_module("c2_native200_contact_support")
    from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
    from biospur_fusion.c2_articulated_biomechanics.model import fit_articulated_model
    from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
        project_hinge_corrections,
    )
    from biospur_fusion.c2_uwb_calibration.adaptive_nodes import AdaptiveNodeTrustConfig
    from biospur_fusion.c2_uwb_calibration.articulated_range import (
        DEFAULT_POINT_CONSTRAINT_SIGMA_M,
        POINT_CONSTRAINT_GATE_SIGMA,
        corrected_proxy_points,
    )
    from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import (
        CausalArticulatedPose,
    )
    from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
    from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
        NODE_TO_PROXY_POINT,
        frozen_world_alignment,
    )
    from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent
    from biospur_fusion.c2_uwb_root_world.ankle_contact import (
        AnkleContactConfig,
        AnkleContactDetector,
        DualFootFootholdCorrector,
        FootContactEvidence,
        FootStillnessProfile,
        FootSupportState,
        positive_swing_cues,
    )
    from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import (
        AuthoritativeArticulatedFusion,
        Native200ClockMappingOwner,
    )
    from biospur_fusion.c2_uwb_root_world.causal_update_guard import (
        ReachabilityClass,
        ReachabilityEnvelope,
    )
    from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import (
        group_epoch_times_ns,
        validate_epoch_cadence,
    )
    from biospur_fusion.c2_timing_contract import canonical_clock_global_ns
    from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
        BShadowGeometryOwner,
        BShadowSnapshotOwner,
        BoundGroupPacket,
        DIAGNOSTIC_HORIZON_S,
        DIAGNOSTIC_MAXIMUM_IMU_GAP_S,
        U3SigmaOwner,
        U5BSigmaOwner,
        _prepare_dynamic_owner,
    )
    from biospur_fusion.c2_uwb_root_world.run_calibration import (
        DATASET,
        PHYSICAL_DIRECTORY,
        _action_bounds_global_ns,
        _beacon_boundary_bridges,
        _clock_models,
    )
    from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import (
        PoseTagLinkOwner,
        RangeInformationOwner,
        ReferenceOwnerBundle,
    )
    from biospur_fusion.c2_uwb_root_world.tight_range import RawRangeUpdateConfig
    from biospur_fusion.ingest.v47 import decode_measurements
    from biospur_fusion.root_r3.estimator import RootFilterConfig
    from biospur_fusion.root_r3.models import ImuSample, RootState
    from biospur_fusion.c2_3b_imu_ik.contracts import LINK_ROWS
    stage("loader.imports.complete")
    started = time.monotonic()
    raw = (
        DATASET
        / "actions"
        / PHYSICAL_DIRECTORY[ACTION]
        / "rep_01/raw/fusion_host_raw.cobs.bin"
    )
    clocks = _clock_models(POSE_CLOCK_TABLE)
    bridges = _beacon_boundary_bridges(POSE_CLOCK_TABLE)
    clock_document = json.loads(POSE_CLOCK_TABLE.read_text(encoding="utf-8"))
    node_clocks = {
        node: DirectNodeLinkClock(
            node, value.a_ns_per_us, value.b_ns, value.boot_epoch,
            int(clock_document["models"][node]["first_timer_us"]),
            int(clock_document["models"][node]["last_timer_us"]),
        )
        for node, value in clocks.items()
    }
    pelvis_clock = node_clocks[pose_support.PELVIS_NODE]
    pelvis_clock_mapping_owner = Native200ClockMappingOwner(
        node=pose_support.PELVIS_NODE,
        clock_domain="B306_TIMER2",
        boot_epoch=pelvis_clock.boot_epoch,
        a_ns_per_us=pelvis_clock.a_ns_per_us,
        b_ns=pelvis_clock.b_ns,
        clock_owner_sha256=POSE_CLOCK_TABLE_SHA256,
    )
    stage("pregroup.clock_owner.complete")
    if _decoded_imu_sentinel is not None:
        sentinel_rows, _ = native_support.pelvis_imu(
            _decoded_imu_sentinel, clocks[pose_support.PELVIS_NODE],
            ACTION04_COMMON_START_NS, 0.0, include_source_ticks=True,
            clock_mapping_owner=pelvis_clock_mapping_owner,
        )
        _strict_native200_pair_before(
            sentinel_rows, sentinel_rows[-1]["source_global_ns"] + 1
        )
        stage("pregroup.decoded_sentinel.complete")
        raise RuntimeError("CONTROLLED_DECODE_ADAPTER_BOUNDARY_SENTINEL")
    raw_sha256 = _sha256(raw)
    if raw_sha256 != ACTION04_RAW_SHA256:
        raise RuntimeError("action04 raw hash mismatch")
    stage("pregroup.raw_hash.complete")
    anchors, delays, tag_delay, layout_sigma = action_support._load_layout()
    calibration = load_frozen_c2_3a()
    alignment, _ = frozen_world_alignment(calibration)
    stage("pregroup.layout_calibration.complete")
    pose_clock, pose_owner_paths = _load_sealed_action04_pose_clock()
    key = f"{pose_support.EPISODES.index(ACTION):02d}"
    with np.load(POSE_ACCEPTED_TRAJECTORY, allow_pickle=False) as trajectory:
        action_data = {
            segment: {
                "time_root_s": np.array(
                    trajectory[f"trajectory/{key}/{segment}/time_root_s"]
                ),
                "quat_world_segment_wxyz": np.array(
                    trajectory[f"trajectory/{key}/{segment}/quat_world_segment_wxyz"]
                ),
                "mask": np.array(
                    trajectory[f"trajectory/{key}/{segment}/mask"], dtype=bool
                ),
            }
            for segment in pose_support.SEGMENTS
        }
    trajectory_owner = {"trajectory": {key: action_data}}
    pose_clocks = {ACTION: pose_clock}
    pose_audit = {
        "accepted_path": str(POSE_ACCEPTED_TRAJECTORY.relative_to(ROOT)),
        "accepted_sha256": POSE_ACCEPTED_TRAJECTORY_SHA256,
        "frontend_sha256": POSE_FRONTEND_ARCHIVE_SHA256,
        "clock_table_sha256": POSE_CLOCK_TABLE_SHA256,
        "owner_hashes": {
            str(path.relative_to(ROOT)): digest
            for path, digest in pose_owner_paths.items()
        },
        "loaded_actions": [ACTION],
        "raw_uwb_opened": False,
        "H01_H02_opened_or_hashed": False,
    }
    interval_audit = _load_sealed_action04_interval()
    provider = pose_support._PoseProvider(
        trajectory=trajectory_owner, clocks=pose_clocks, alignment=alignment
    )
    stage("pregroup.pose_owner.complete")
    episode = action_support._load_episode(ACTION, clocks, bridges)
    lo_ns, hi_ns, _ = _action_bounds_global_ns(
        PHYSICAL_DIRECTORY[ACTION], bridges
    )
    if (
        lo_ns != interval_audit["start_global_ns"]
        or hi_ns != interval_audit["stop_global_ns_exclusive"]
    ):
        raise RuntimeError("raw action boundary differs from sealed common-global owner")
    stop_ns = lo_ns + int(round(request.duration_s * 1e9))
    if stop_ns > hi_ns:
        raise RuntimeError("bounded raw prefix exceeds action support")
    groups = [
        group for group in episode["groups"]
        if lo_ns <= action_support._reference_time(group, clocks) * 1e9 < stop_ns
    ]
    validate_epoch_cadence([
        action_support._reference_time(group, clocks) * 1e9 for group in groups
    ])
    stage("pregroup.range_groups.complete")
    events, decode_audit = decode_measurements(raw)
    stage("pregroup.measurement_decode.complete")
    imu_rows, orientation_audit = native_support.pelvis_imu(
        events, clocks[pose_support.PELVIS_NODE], lo_ns, 0.0,
        include_source_ticks=True,
        clock_mapping_owner=pelvis_clock_mapping_owner,
    )
    orientation_audit["clock_owner_sha256"] = POSE_CLOCK_TABLE_SHA256
    all_pelvis_rows = tuple(imu_rows)
    stage("pregroup.pelvis_vqf.complete")
    contact_config = AnkleContactConfig()
    contact_document = _load_sealed_contact_profile_document()
    if contact_document["config"] != contact_config.__dict__:
        raise RuntimeError("derived contact-profile config differs from code owner")
    contact_profiles = {
        side: FootStillnessProfile(**contact_document["profiles"][side])
        for side in ("left", "right")
    }
    for profile in contact_profiles.values():
        profile.validate()
    ankle_rows = native_support.ankle_imu_rows(
        events, clocks, lo_ns, stop_ns
    )
    stage("pregroup.ankle_decode.complete")
    del events, episode
    gc.collect()
    final_availability_ns = int(round(max(
        group_epoch_times_ns(group, clocks=node_clocks)[2] for group in groups
    )))
    final_availability = final_availability_ns * 1e-9
    if final_availability - stop_ns * 1e-9 > DIAGNOSTIC_HORIZON_S + 1e-12:
        raise ValueError("diagnostic context horizon exceeded")
    metric_rows = [
        row for row in imu_rows if lo_ns * 1e-9 < float(row["time_s"]) < stop_ns * 1e-9
    ]
    submitted_rows = [
        row for row in imu_rows
        if lo_ns * 1e-9 < float(row["time_s"]) <= final_availability
    ]
    context_rows = [
        row for row in submitted_rows if float(row["time_s"]) >= stop_ns * 1e-9
    ]
    if (
        not submitted_rows
        or final_availability - float(submitted_rows[-1]["time_s"])
        > DIAGNOSTIC_MAXIMUM_IMU_GAP_S + 1e-12
    ):
        raise RuntimeError("diagnostic context IMU coverage incomplete")
    closure_previous, temporal_closure_row = _select_temporal_closure_pair(
        imu_rows, final_availability_ns, pelvis_clock_mapping_owner
    )
    if (
        temporal_closure_row["source_global_ns"] * 1e-9 - final_availability
        > DIAGNOSTIC_MAXIMUM_IMU_GAP_S + 1e-12
    ):
        raise RuntimeError("temporal-closure IMU coverage incomplete")
    if submitted_rows[-1]["source_global_ns"] != closure_previous["source_global_ns"]:
        raise RuntimeError("temporal-closure preceding sample is not submitted")
    imu_rows = [*submitted_rows, temporal_closure_row]
    stage("pregroup.temporal_closure.complete")

    config = RawRangeUpdateConfig(
        nominal_sigma_m=0.12,
        huber_threshold_sigma=2.5,
        maximum_iterations=8,
        convergence_tolerance=1e-7,
        covariance_floor=1e-12,
        positive_nlos_cauchy_scale_m=0.12,
        uncertainty_provenance=(
            "PROVISIONAL_UNCALIBRATED_DIAGNOSTIC_ACTION04_ARTICULATED_FIRST5S"
        ),
    )
    a_sigma = U3SigmaOwner(
        0.0564866166214546, 0.10,
        "SEALED_U3_LAYOUT_PLUS_FLOOR_ACTION04_REFERENCE",
    )
    if layout_sigma != a_sigma.layout_sigma_m:
        raise RuntimeError("layout sigma mismatch")
    b_sigma = U5BSigmaOwner(config, config.uncertainty_provenance)

    all_pose = []
    all_shadow = []
    for group in groups:
        pose_links = []
        for row in sorted(group, key=lambda value: str(value.node)):
            for anchor in range(8):
                query = node_clocks[row.node].link_time_ns(
                    event_boot_epoch=row.boot,
                    strobe_us=row.strobe_us,
                    t_round_us=float(row.t_round_us[anchor]),
                )
                snapshot = provider.snapshot(
                    action=ACTION, sweep_query_ns=query, root_world_m=np.zeros(3)
                )
                if not snapshot.pose_global_ns < query:
                    raise RuntimeError("pose is not strict pre-link")
                pose_links.append(PoseTagLinkOwner(
                    str(row.node), anchor, query, snapshot.pose_global_ns,
                    snapshot.offsets_world_m[row.node], np.zeros(3),
                    snapshot.frame, snapshot.frame, pose_audit["accepted_sha256"],
                ))
        all_pose.append(tuple(pose_links))
        shadow_snapshots = []
        for row in sorted(group, key=lambda value: str(value.node)):
            queries = [
                node_clocks[row.node].link_time_ns(
                    event_boot_epoch=row.boot,
                    strobe_us=row.strobe_us,
                    t_round_us=float(row.t_round_us[anchor]),
                )
                for anchor in action_support._valid_slots(row)
            ]
            snapshot = provider.snapshot(
                action=ACTION,
                sweep_query_ns=min(queries),
                root_world_m=np.zeros(3),
            )
            shadow_snapshots.append(BShadowSnapshotOwner(
                str(row.node), ACTION, snapshot.frame, snapshot.pose_global_ns,
                snapshot.query_global_ns, snapshot.offsets_world_m,
                snapshot.normals_world, snapshot.joints_relative_world_m,
                pose_audit["accepted_sha256"],
            ))
        all_shadow.append(BShadowGeometryOwner(
            calibration.geometry,
            tuple(shadow_snapshots),
            "FROZEN_C2_DISPLAY_PROXY_STRICT_PRE_LINK_POSE_ACTION04",
        ))

    initial = RootState(
        lo_ns * 1e-9,
        np.r_[np.array([np.mean(anchors[:, 0]), np.mean(anchors[:, 1]), 0.95]), np.zeros(6)],
        np.diag([1.0] * 6 + [0.04] * 3),
    )
    envelope = ReachabilityEnvelope(
        ReachabilityClass.NOMINAL, 20.0, 100.0, 1000.0, 1.0, 100.0,
        1000.0, 1.0, 1.0, 1.0, 0.01, 2, 20.0, 1e8,
        "U3_OFFLINE_FUNCTIONAL_FIXTURE_NOT_HUMAN_OR_PRODUCT_QUALIFICATION",
    )
    static_range = RangeInformationOwner(
        0.12, 0.12, {node: np.ones(8) for node in sorted(node_clocks)},
        "UNIT_INFORMATION_WEIGHT_EXACT_U3",
    )
    static_owner = ReferenceOwnerBundle(
        RootFilterConfig(fixed_lag_s=0.10), True, initial, anchors, node_clocks,
        delays, tag_delay, all_pose[0], static_range, envelope,
        AdaptiveNodeTrustConfig(), "SEALED_U3_ROOT_FILTER_CONFIG",
        "SEALED_U3_ACTION04_INITIAL_STATE", "SEALED_LAYOUT",
        f"CLOCK_TABLE_SHA256:{_sha256(POSE_CLOCK_TABLE)}",
        "U1_NOMINAL_ROOT_POSITION_POLICY",
    )
    packets = []
    for index, group in enumerate(groups):
        _, _, availability_ns = group_epoch_times_ns(group, clocks=node_clocks)
        availability_ns = canonical_clock_global_ns(availability_ns)
        packets.append(BoundGroupPacket(
            static_owner.digest,
            RootWorkerEvent(index, availability_ns * 1e-9, "UWB", tuple(group)),
            all_pose[index], (), a_sigma, b_sigma, all_shadow[index],
            availability_global_ns=availability_ns,
        ))
    stage("pregroup.static_packets.complete")
    imu_events = [
        RootWorkerEvent(
            int(row["sequence"]), float(row["time_s"]), "IMU",
            ImuSample(
                float(row["time_s"]), float(row["time_s"]), row["acceleration"],
                row["rotation_world"], int(row["sequence"]),
            ),
        )
        for row in imu_rows
    ]
    imu_row_by_sequence = {int(row["sequence"]): row for row in imu_rows}
    if len(imu_row_by_sequence) != len(imu_rows):
        raise RuntimeError("native200 source sequence is not unique")
    timeline = [
        (float(row["time_s"]), 0, row) for row in ankle_rows
        if lo_ns * 1e-9 < float(row["time_s"]) <= final_availability
    ]
    timeline.extend((event.availability_time_s, 1, event) for event in imu_events)
    timeline.extend((packet.event.availability_time_s, 2, packet) for packet in packets)
    timeline.sort(key=lambda value: (value[0], value[1]))

    _axis_report, hinge_model, axis_owner_audit = _load_and_fit_sealed_axis_owner()
    stage("pregroup.axis_model.complete")
    output_model_report_path = CORRECTED_OUTPUT_REPORT
    if _sha256(output_model_report_path) != CORRECTED_OUTPUT_REPORT_SHA256:
        raise RuntimeError("corrected output provenance report hash mismatch")
    output_model_report = json.loads(
        output_model_report_path.read_text(encoding="utf-8")
    )
    if "qmt_olsson_hinge_axes" in output_model_report:
        raise RuntimeError("corrected output report unexpectedly claims axis ownership")
    action_data = trajectory_owner["trajectory"][key]
    articulated_start_ns = lo_ns
    articulated_stop_ns = max(
        int(temporal_closure_row["source_global_ns"]), stop_ns
    )

    def pose_at_frame(index: int):
        return _accepted_articulated_pose_frame(
            action_data,
            tuple(pose_support.SEGMENTS),
            index,
            alignment,
            calibration.geometry,
        )

    def pose_at_common_global_ns(query_ns: int):
        selected = pose_clocks[ACTION].strict_floor(int(query_ns))
        rotations, points = pose_at_frame(selected.frame)
        return selected, rotations, points

    def pose_at_exact_native200_global_ns(query_ns: int):
        selected = pose_clocks[ACTION].exact_tick(int(query_ns))
        rotations, points = pose_at_frame(selected.frame)
        return selected, rotations, points

    def exact_base_at_source(global_ns: int, timer_us: int):
        selected = pose_clocks[ACTION].exact_tick(
            int(global_ns), source_timer_us=int(timer_us)
        )
        rotations, _points = pose_at_frame(selected.frame)
        return selected, rotations

    def pose_strictly_before_async_global_ns(query_ns: int):
        selected = pose_clocks[ACTION].strict_floor(int(query_ns))
        rotations, points = pose_at_frame(selected.frame)
        return selected, rotations, points

    def rotations_at_common_global_ns(query_ns: int):
        return pose_at_common_global_ns(query_ns)[1]

    def rotations_at_source_timer_us(timer_us: int):
        matches = np.flatnonzero(
            (pose_clocks[ACTION].timer_us == int(timer_us))
            & pose_clocks[ACTION].valid
        )
        if len(matches) != 1:
            raise RuntimeError("native200 source tick has no unique pose frame")
        return pose_at_frame(int(matches[0]))[0]

    def rotations_at_fraction(fraction: float):
        query_ns = _fraction_to_common_global_ns(
            fraction,
            action_start_global_ns=articulated_start_ns,
            action_stop_global_ns=articulated_stop_ns,
        )
        return pose_at_exact_native200_global_ns(query_ns)[1]

    def ankle_proxy_from_pose_owner(time_s: float):
        selected, _rotations, points = pose_strictly_before_async_global_ns(
            int(round(float(time_s) * 1e9))
        )
        valid_frames = np.flatnonzero(pose_clocks[ACTION].valid)
        local = int(np.searchsorted(valid_frames, selected.frame))
        if local < 1 or valid_frames[local] != selected.frame:
            raise RuntimeError("articulated ankle proxy lacks preceding pose frame")
        previous_frame = int(valid_frames[local - 1])
        if (
            pose_clocks[ACTION].contiguous_span_id[previous_frame]
            != pose_clocks[ACTION].contiguous_span_id[selected.frame]
        ):
            raise RuntimeError("articulated ankle proxy crosses a pose gap")
        _previous_rotations, previous_points = pose_at_frame(previous_frame)
        delta_s = (
            int(pose_clocks[ACTION].global_ns[selected.frame])
            - int(pose_clocks[ACTION].global_ns[previous_frame])
        ) * 1e-9
        if not delta_s > 0.0:
            raise RuntimeError("articulated ankle proxy time is not increasing")
        offsets = {
            "left": points[NODE_TO_PROXY_POINT["BSF6C53"]],
            "right": points[NODE_TO_PROXY_POINT["BSF8BC4"]],
        }
        previous_offsets = {
            "left": previous_points[NODE_TO_PROXY_POINT["BSF6C53"]],
            "right": previous_points[NODE_TO_PROXY_POINT["BSF8BC4"]],
        }
        velocities = {
            side: (offsets[side] - previous_offsets[side]) / delta_s
            for side in ("left", "right")
        }
        return offsets, velocities

    articulated_pose = CausalArticulatedPose(
        action_start_s=articulated_start_ns * 1e-9,
        action_stop_s=articulated_stop_ns * 1e-9,
        rotations_at_fraction=rotations_at_fraction,
        geometry=calibration.geometry,
        hinge_projector=partial(project_hinge_corrections, model=hinge_model),
    )
    engine = AuthoritativeArticulatedFusion(
        static_owner=static_owner, pose=articulated_pose,
        native200_clock_owner_sha256=POSE_CLOCK_TABLE_SHA256,
        native200_base_pose_owner_digest=EXACT_SOURCE_BASE_POSE_OWNER_DIGEST,
    )
    stage("pregroup.articulated_engine.complete")

    ankle_proxy = ankle_proxy_from_pose_owner
    detector = AnkleContactDetector(contact_profiles, contact_config)
    footholds = DualFootFootholdCorrector()
    latest_contact = {
        side: FootContactEvidence(
            side, lo_ns * 1e-9, 0.0, False, np.nan, np.nan, np.nan,
            "NO_SAMPLE", support_state=FootSupportState.UNOBSERVABLE.value,
        )
        for side in ("left", "right")
    }

    import pickle
    from biospur_fusion.c2_uwb_root_world import causal_update_transaction as tx_module
    from biospur_fusion.c2_uwb_root_world import authoritative_articulated_fusion as articulated_module

    behavior_modules = tuple(importlib.import_module(name) for name in (
        "biospur_fusion.c2_3a_kinematics",
        "biospur_fusion.c2_articulated_biomechanics.model",
        "biospur_fusion.c2_articulated_biomechanics.orientation_ik",
        "biospur_fusion.c2_articulated_biomechanics.hinge_temporal",
        "biospur_fusion.c2_uwb_calibration.adaptive_nodes",
        "biospur_fusion.c2_uwb_calibration.antenna_los",
        "biospur_fusion.c2_uwb_calibration.articulated_range",
        "biospur_fusion.c2_uwb_calibration.causal_articulated_pose",
        "biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab",
        "biospur_fusion.c2_uwb_calibration.frozen_body_proxy",
        "biospur_fusion.c2_uwb_root_world.ankle_contact",
        "biospur_fusion.c2_uwb_root_world.async_root_worker",
        "biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion",
        "biospur_fusion.c2_uwb_root_world.causal_update_guard",
        "biospur_fusion.c2_uwb_root_world.causal_update_transaction",
        "biospur_fusion.c2_uwb_root_world.offline_unified_contact_wiring",
        "biospur_fusion.c2_uwb_root_world.offline_unified_wiring",
        "biospur_fusion.c2_uwb_root_world.owner_bound_async_worker",
        "biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring",
        "biospur_fusion.c2_uwb_root_world.tight_range",
        "biospur_fusion.ingest.v47",
        "biospur_fusion.root_r3.estimator",
        "biospur_fusion.root_r3.models",
        "biospur_fusion.c2_3b_imu_ik.contracts",
    ))
    dependency_paths = tuple(dict.fromkeys((
        *(ROOT / path for path in RAW_BRANCH_SOURCE_PATHS),
        Path(__file__).resolve(),
        Path(importlib.import_module("run_c2_orientation_constrained_biomechanics").__file__).resolve(),
        *(Path(module.__file__).resolve() for module in behavior_modules),
    )))
    data_paths = tuple(dict.fromkeys((
        raw.resolve(), POSE_CLOCK_TABLE.resolve(), AXIS_OWNER_REPORT.resolve(),
        AXIS_OWNER_TRAJECTORY.resolve(), output_model_report_path.resolve(),
        (ROOT / pose_audit["accepted_path"]).resolve(),
        (CONTACT_PROFILE_REVISION / "RESULT.json").resolve(),
        (CONTACT_PROFILE_REVISION / "SHA256SUMS").resolve(),
        (AXIS_OWNER_PREFLIGHT / "ALLOWLIST.json").resolve(),
        (AXIS_OWNER_PREFLIGHT / "SHA256SUMS").resolve(),
    )))
    source_before = {str(path.relative_to(ROOT)): _sha256(path) for path in dependency_paths}
    data_before = {str(path.relative_to(ROOT)): _sha256(path) for path in data_paths}

    results = []
    group_measurements = []
    pending_temporal_rows = []
    service_ms = []
    for event_time, kind, item in timeline:
        if kind == 0:
            offsets, velocities = ankle_proxy(event_time)
            side = item["side"]
            cues = positive_swing_cues(
                query_time_s=event_time,
                analytic_ankle_offset_world_m=offsets,
                root_state=engine.root.current_state,
                foothold_corrector=footholds,
                evidence=latest_contact,
                maximum_root_age_s=1.5 * articulated_pose.derivative_period_s,
                positive_swing_height_m=contact_config.maximum_height_margin_m,
            )
            lower = min(offsets[value][2] for value in ("left", "right"))
            latest_contact[side] = detector.update(
                side, time_s=event_time,
                acceleration_mps2=item["acceleration"], gyro_rad_s=item["gyro"],
                relative_height_m=float(offsets[side][2] - lower),
                relative_speed_mps=float(np.linalg.norm(velocities[side])),
                positive_swing=bool(cues[side]["positive"]),
                swing_observable=bool(cues[side]["observable"]),
            )
            footholds.update(
                engine.root.current_state, evidence=latest_contact,
                ankle_offset_world_m=offsets,
                ankle_offset_velocity_world_mps=velocities,
            )
            continue
        if kind == 1:
            _process_native200_timeline_event(
                engine=engine, item=item, event_time=event_time,
                all_pelvis_rows=all_pelvis_rows,
                imu_row_by_sequence=imu_row_by_sequence,
                clock_mapping_owner=pelvis_clock_mapping_owner,
                pending_temporal_rows=pending_temporal_rows,
                exact_base_at_source=exact_base_at_source,
                base_pose_owner_digest=EXACT_SOURCE_BASE_POSE_OWNER_DIGEST,
            )
            continue
        group_index = len(results)
        stage(f"group.{group_index}.prepare.begin")
        service_started = time.monotonic()
        service_observation_started = observation_overhead_s
        plan = engine.robust.prepare(
            engine.static, engine.root, item,
            _prepare_dynamic_owner(engine.static, item),
        )
        stage(f"group.{group_index}.prepare.complete")
        previous = engine.pose.transition_snapshot()["target_correction"]
        owned_footholds = footholds.footholds_at_time(plan.measurement_s)
        constraints = {
            f"ankle_{side}": point.copy()
            for side, point in owned_footholds.items()
        }
        root_before = engine.root.publication_token()
        pose_before = engine.pose.publication_token()
        temporal_owner = getattr(
            engine.pose, "_CausalArticulatedPose__hinge_temporal_owner"
        )
        temporal_before = temporal_owner._snapshot_token()
        robust_before = engine.robust.snapshot()
        contact_before = hashlib.sha256(pickle.dumps(
            (footholds.__dict__, detector.__dict__), protocol=5
        )).hexdigest()
        solver_calls = []
        actual_solver = articulated_module.solve_articulated_ranges

        def counted_solver(*args, **kwargs):
            solved = actual_solver(*args, **kwargs)
            fixed = np.asarray(kwargs["fixed_root_position_m"], dtype=float)
            solver_calls.append(bool(
                solved.root_position_m is None
                or np.array_equal(np.asarray(solved.root_position_m), fixed)
            ))
            return solved

        u1_calls = 0
        actual_guard = tx_module.evaluate_candidate_transition

        def counted_guard(*args, **kwargs):
            nonlocal u1_calls
            u1_calls += 1
            return actual_guard(*args, **kwargs)

        articulated_module.solve_articulated_ranges = counted_solver
        tx_module.evaluate_candidate_transition = counted_guard
        stage(f"group.{group_index}.source_pair.begin")
        previous_source, current_source = _strict_native200_pair_before(
            imu_rows, plan.measurement_s * 1e9
        )
        source_pair = engine.native200_source_pair(
            clock_mapping_owner=pelvis_clock_mapping_owner,
            previous_timer_us=previous_source["source_timer_us"],
            current_timer_us=current_source["source_timer_us"],
            previous_global_ns=previous_source["source_global_ns"],
            current_global_ns=current_source["source_global_ns"],
        )
        stage(f"group.{group_index}.source_pair.complete")
        stage(f"group.{group_index}.epoch.begin")
        base_rotations = rotations_at_source_timer_us(
            source_pair.current_timer_us
        )
        epoch = engine.epoch(
            measurement_time_s=plan.measurement_s,
            availability_time_s=plan.availability_s,
            previous_orientation_time_s=source_pair.previous_global_ns * 1e-9,
            base_rotations_world=base_rotations,
            previous_correction_rotvec=previous,
            point_constraints_world_m=constraints,
            provenance="SEALED_ACTION04_NATIVE200_STRICT_PRELINK_POSE",
            native200_source_pair=source_pair,
        )
        stage(f"group.{group_index}.epoch.complete")
        stage(f"group.{group_index}.admit.begin")
        try:
            result = engine.admit(item, epoch)
        finally:
            tx_module.evaluate_candidate_transition = actual_guard
            articulated_module.solve_articulated_ranges = actual_solver
        stage(f"group.{group_index}.admit.complete")
        service_ms.append(
            (
                time.monotonic()
                - service_started
                - (observation_overhead_s - service_observation_started)
            )
            * 1000.0
        )
        results.append(result)
        root_after = engine.root.publication_token()
        pose_after = engine.pose.publication_token()
        temporal_after = temporal_owner._snapshot_token()
        robust_after = engine.robust.snapshot()
        contact_after = hashlib.sha256(pickle.dumps(
            (footholds.__dict__, detector.__dict__), protocol=5
        )).hexdigest()
        changed_bias_nodes = {
            after[0] for before, after in zip(robust_before[1], robust_after[1])
            if pickle.dumps(before[1], protocol=5) != pickle.dumps(after[1], protocol=5)
        }
        trusted = set(result.trusted_nodes)
        points = corrected_proxy_points(
            base_rotations, result.segment_correction_rotvec, calibration.geometry
        )
        bone_errors = [abs(
            np.linalg.norm(points[distal] - points[proximal])
            - calibration.geometry.segment_length_m[segment]
        ) for segment, proximal, distal in LINK_ROWS if segment in calibration.geometry.segment_length_m]
        identity_errors = [float(np.linalg.norm(
            result.node_position_m[node] - (result.root_position_m + points[point])
        )) for node, point in articulated_module.NODE_TO_PROXY_POINT.items()]
        contact_limit = POINT_CONSTRAINT_GATE_SIGMA * DEFAULT_POINT_CONSTRAINT_SIGMA_M
        measurement = {
            "sequence": result.sequence,
            "accepted": result.accepted,
            "u1_calls": u1_calls,
            "fixed_root_solver_calls": solver_calls,
            "root_revision_before": root_before.revision,
            "root_revision_after": root_after.revision,
            "pose_revision_before": pose_before.revision,
            "pose_revision_after": pose_after.revision,
            "robust_revision_before": robust_before[0],
            "robust_revision_after": robust_after[0],
            "temporal_revision_before": temporal_before.revision,
            "temporal_revision_after": temporal_after.revision,
            "contact_owner_digest_before": contact_before,
            "contact_owner_digest_after": contact_after,
            "trusted_nodes": sorted(trusted),
            "direct_nodes": list(result.direct_nodes),
            "propagated_nodes": list(result.propagated_nodes),
            "node_inventory": sorted(engine.static.clocks),
            "changed_bias_nodes": sorted(changed_bias_nodes),
            "identity_error_maximum_m": max(identity_errors, default=0.0),
            "bone_length_error_maximum_m": max(bone_errors, default=0.0),
            "contact_constraint_count": len(constraints),
            "maximum_foothold_residual_m": result.maximum_foothold_residual_m,
            "contact_limit_m": contact_limit,
            "contact_snapshot_supplied": True,
            "contact_sides": sorted(owned_footholds),
            "root_position_flat": np.asarray(result.root_position_m).ravel().tolist(),
            "root_covariance_flat": np.asarray(result.root_covariance_m2).ravel().tolist(),
            "joint_covariance_status": result.joint_covariance_status,
            "root_joint_cross_covariance_status": result.root_joint_cross_covariance_status,
            "native_period_s": result.native_period_s,
            "native200_source_pair": {
                "node": source_pair.node,
                "boot_epoch": source_pair.boot_epoch,
                "previous_timer_us": source_pair.previous_timer_us,
                "current_timer_us": source_pair.current_timer_us,
                "previous_global_ns": source_pair.previous_global_ns,
                "current_global_ns": source_pair.current_global_ns,
                "clock_owner_sha256": source_pair.clock_owner_sha256,
                "mapping_digest": source_pair.mapping_digest,
            },
            "pose_query_ns": [
                [link.pose_time_ns, link.query_time_ns] for link in item.pose_links
            ],
            "next_native200_temporal_delta": None,
            "next_native200_time_s": None,
        }
        group_measurements.append(measurement)
        pending_temporal_rows.append(measurement)
        stage(f"group.{group_index}.audit.complete")

    if pending_temporal_rows:
        raise RuntimeError("final UWB group lacks a subsequent native200 pose sample")

    elapsed = time.monotonic() - started - observation_overhead_s
    service = np.asarray(service_ms, dtype=float)
    sweeps = sum(len(packet.event.payload) for packet in packets)
    links = sum(
        sum(
            1 for anchor in range(8)
            if row.valid_mask & (1 << anchor)
            and int(row.anchor_ids[anchor]) == anchor
            and 0 < row.ranges_mm[anchor] < 0xFFFF
            and np.isfinite(row.t_round_us[anchor])
        )
        for packet in packets for row in packet.event.payload
    )
    groups_out = tuple({
        "sequence": result.sequence,
        "accepted": result.accepted,
        "reason": result.reason,
        "x_of_10": len(result.trusted_nodes),
        "direct_nodes": list(result.direct_nodes),
        "propagated_nodes": list(result.propagated_nodes),
        "root_position_m": result.root_position_m.tolist(),
        "root_covariance_m2": result.root_covariance_m2.tolist(),
        "joint_covariance_status": result.joint_covariance_status,
        "root_joint_cross_covariance_status": result.root_joint_cross_covariance_status,
        **measurement,
    } for result, measurement in zip(results, group_measurements))
    source_after = {str(path.relative_to(ROOT)): _sha256(path) for path in dependency_paths}
    data_after = {str(path.relative_to(ROOT)): _sha256(path) for path in data_paths}
    runtime = {
        "wall_s": elapsed,
        "maximum_rss_kib": float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "group_service_p99_ms": float(np.quantile(service, 0.99)),
        "group_service_maximum_ms": float(np.max(service)),
        "effective_utilization": float(np.mean(service) / 120.048),
    }
    stage("loader.success.complete")
    return RawBranchPayload(
        counts={
            "imu_metric": len(metric_rows), "imu_context": len(context_rows),
            "imu_temporal_closure": 1,
            "groups": len(groups), "sweeps": sweeps, "links": links,
        },
        groups=groups_out,
        runtime=runtime,
        provenance={
            "raw_path": str(raw.relative_to(ROOT)), "raw_sha256": raw_sha256,
            "source_sha256_before": source_before,
            "source_sha256_after": source_after,
            "data_sha256_before": data_before,
            "data_sha256_after": data_after,
            "clock_sha256": _sha256(POSE_CLOCK_TABLE),
            "action_interval_owner": interval_audit,
            "temporal_closure_owner": {
                "source_node": temporal_closure_row["source_node"],
                "source_boot_epoch": temporal_closure_row["source_boot_epoch"],
                "previous_timer_us": closure_previous["source_timer_us"],
                "closure_timer_us": temporal_closure_row["source_timer_us"],
                "previous_global_ns": closure_previous["source_global_ns"],
                "closure_global_ns": temporal_closure_row["source_global_ns"],
                "clock_mapping_digest": temporal_closure_row[
                    "source_clock_mapping_digest"
                ],
            },
            "pose_owner": pose_audit,
            "decode": asdict(decode_audit),
            "orientation": orientation_audit,
            "axis_model_owner": axis_owner_audit,
            "corrected_output_report_provenance_only": {
                "path": str(output_model_report_path.relative_to(ROOT)),
                "sha256": _sha256(output_model_report_path),
                "owns_axes": False,
            },
            "calibrated_R": False,
            "scientific_pass": False,
            "product_pass": False,
        },
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--action")
    parser.add_argument("--start-s", type=float)
    parser.add_argument("--duration-s", type=float)
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--authorized-runner-sha256")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    explicit = (
        arguments.action, arguments.start_s, arguments.duration_s,
        arguments.attempt, arguments.authorized_runner_sha256,
    )
    if arguments.dry_run:
        if any(value is not None for value in explicit):
            parser.error("dry mode does not accept raw execution arguments")
        arguments.request = RawRunRequest(
            ACTION, 0.0, 5.0, 1, _sha256(Path(__file__).resolve())
        )
        expected_output = EXPECTED_DRY_OUTPUT
    else:
        if any(value is None for value in explicit):
            parser.error("raw mode requires --action --start-s --duration-s --attempt")
        arguments.request = RawRunRequest(*explicit)
        try:
            _validate_raw_request(arguments.request)
        except ValueError as error:
            parser.error(str(error))
        expected_output = EXPECTED_RAW_OUTPUT
    output = (ROOT / arguments.output).resolve() if not arguments.output.is_absolute() else arguments.output.resolve()
    if output != expected_output:
        parser.error(f"output must be the preregistered fresh path: {expected_output}")
    arguments.output = output
    return arguments


def _run_raw(arguments: argparse.Namespace) -> int:
    """Execute only the preregistered bounded raw branch and seal either verdict."""

    if arguments.output.exists():
        raise RuntimeError("retry/stale-output rejected before raw loader")
    bound_sources = _bind_raw_branch_sources(_verify_articulated_revision())
    runner_test = ROOT / "tests/test_c2_authoritative_articulated_action04_runner.py"
    bound_sources[str(runner_test.relative_to(ROOT))] = _sha256(runner_test)
    _verify_transport_seal()
    _load_sealed_contact_profile_document()
    _axis_report, _axis_model, axis_owner_audit = _load_and_fit_sealed_axis_owner()
    common_pose_clock_audit = _verify_common_global_pose_adapter()
    failed_raw_audit = _verify_failed_raw_nonpromoted()
    failed_source_pair_raw_audit = _verify_failed_source_pair_raw_nonpromoted()
    failed_source_base_raw_audit = _verify_failed_source_base_raw_nonpromoted()
    failed_integer_time_raw_audit = _verify_failed_integer_time_raw_nonpromoted()
    prior_integer_time_dry_audit = _verify_prior_integer_time_dry_nonpromoted()
    prior_source_tick_dry_audit = _verify_prior_source_tick_dry_nonpromoted()
    failed_readonly_raw_audit = _verify_failed_readonly_raw_nonpromoted()
    failed_readonly_audit_raw_audit = (
        _verify_failed_readonly_audit_raw_nonpromoted()
    )
    failed_temporal_closure_raw_audit = (
        _verify_failed_temporal_closure_raw_nonpromoted()
    )
    failed_dual_pose_owner_raw_audit = (
        _verify_failed_dual_pose_owner_raw_nonpromoted()
    )
    performance_boundary_audit = _verify_no_raw_performance_boundary()
    analytic_jacobian_audit = _verify_analytic_jacobian_benchmark()
    failed_clock_dry_audit = _verify_failed_clock_dry_nonpromoted()
    prior_clock_dry_audit = _verify_prior_clock_dry()
    runner = Path(__file__).resolve()
    runner_sha256 = _sha256(runner)
    arguments.output.mkdir(parents=False)
    request = arguments.request
    command = (
        f"{sys.executable} {runner.relative_to(ROOT)} --action {request.action} "
        f"--start-s {request.start_s:g} --duration-s {request.duration_s:g} "
        f"--attempt {request.attempt} --authorized-runner-sha256 "
        f"{request.authorized_runner_sha256} --output {arguments.output.relative_to(ROOT)}"
    )
    contract = {
        "schema": "biospur.c2.authoritative-articulated.action04.raw.v1",
        "status": "FROZEN_BEFORE_RAW_LOADER",
        "action": request.action,
        "start_s": request.start_s,
        "duration_s": request.duration_s,
        "attempt": request.attempt,
        "externally_authorized_runner_sha256": request.authorized_runner_sha256,
        "maximum_attempts": 1,
        "hxx_execution_authorized": False,
        "full_execution_authorized": False,
        "command": command,
        "runner_sha256": runner_sha256,
        "bound_articulated_seal_sha256": ARTICULATED_SEAL_SHA256,
        "bound_transport_seal_sha256": TRANSPORT_SEAL_SHA256,
        "contact_profile_owner": {
            "artifact": str((CONTACT_PROFILE_REVISION / "RESULT.json").relative_to(ROOT)),
            "artifact_sha256": CONTACT_PROFILE_RESULT_SHA256,
            "seal": str((CONTACT_PROFILE_REVISION / "SHA256SUMS").relative_to(ROOT)),
            "seal_sha256": CONTACT_PROFILE_SEAL_SHA256,
            "raw_recalibration_allowed": False,
        },
        "axis_model_owner": axis_owner_audit,
        "common_global_pose_clock": common_pose_clock_audit,
        "corrected_output_report_provenance_only": {
            "path": str(CORRECTED_OUTPUT_REPORT.relative_to(ROOT)),
            "sha256": CORRECTED_OUTPUT_REPORT_SHA256,
            "owns_axes": False,
        },
        "failed_raw_revision_nonpromoted": failed_raw_audit,
        "failed_source_pair_raw_revision_nonpromoted": failed_source_pair_raw_audit,
        "failed_source_base_raw_revision_nonpromoted": failed_source_base_raw_audit,
        "source_pair_derivative_revision": {
            "path": str(SOURCE_PAIR_DERIVATIVE_REVISION.relative_to(ROOT)),
            "seal_sha256": SOURCE_PAIR_DERIVATIVE_SEAL_SHA256,
        },
        "failed_integer_time_raw_revision_nonpromoted": failed_integer_time_raw_audit,
        "prior_integer_time_dry_revision_nonpromoted": prior_integer_time_dry_audit,
        "prior_source_tick_dry_revision_nonpromoted": prior_source_tick_dry_audit,
        "failed_readonly_raw_revision_nonpromoted": failed_readonly_raw_audit,
        "failed_readonly_audit_raw_revision_nonpromoted": (
            failed_readonly_audit_raw_audit
        ),
        "failed_temporal_closure_raw_revision_nonpromoted": (
            failed_temporal_closure_raw_audit
        ),
        "failed_dual_pose_owner_raw_revision_nonpromoted": (
            failed_dual_pose_owner_raw_audit
        ),
        "no_raw_performance_boundary": performance_boundary_audit,
        "analytic_jacobian_benchmark": analytic_jacobian_audit,
        "failed_clock_dry_revision_nonpromoted": failed_clock_dry_audit,
        "prior_common_global_clock_dry": prior_clock_dry_audit,
        "source_and_test_sha256": bound_sources,
        "inventory_gate": {
            "native_imu_period_s": 0.005,
            "context_imu": 6,
            "temporal_closure_imu": 1,
            "total_submitted_imu": 1007,
            "node_sweeps_per_group": 10,
            "valid_links_per_sweep": [4, 8],
            "first5_exact": {"imu_metric": 1000, "groups": 41, "sweeps": 410, "links": 3266},
        },
        "semantic_gate": {
            "robust_root_is_only_translation": True,
            "articulated_fixed_root_only": True,
            "atomic_root_pose_robust_commit": True,
            "direct_trusted_and_fk_propagated_partition": True,
            "u1_once_per_group": True,
            "strict_prelink_native200_pose": True,
            "cross_covariance_unavailable_not_fabricated": True,
        },
        "runtime_gate": {
            "wall_s": 300.0,
            "rss_kib": 300000,
            "group_service_p99_ms": 150.0,
            "group_service_maximum_ms": 200.0,
            "effective_utilization": 1.0,
            "evidence_bytes": 10000000,
        },
        "calibrated_R": False,
        "scientific_pass": False,
        "product_pass": False,
    }
    _write_json(arguments.output / "CONTRACT.json", contract)
    (arguments.output / "COMMAND.txt").write_text(command + "\n", encoding="utf-8")
    stage_trace: list[str] = []
    try:
        payload, gates = _evaluate_raw_payload(
            request,
            lambda owned_request: _real_action04_loader(
                owned_request, _stage_observer=stage_trace.append
            ),
        )
        failures = sorted(key for key, passed in gates.items() if not passed)
        result = {
            "status": (
                "ACTION04_AUTHORITATIVE_ARTICULATED_DIAGNOSTIC_PASS"
                if not failures else "BLOCKED_ACTION04_AUTHORITATIVE_ARTICULATED"
            ),
            "failures": failures,
            "request": {
                "action": request.action, "start_s": request.start_s,
                "duration_s": request.duration_s, "attempt": request.attempt,
            },
            "counts": dict(payload.counts),
            "gates": gates,
            "runtime": dict(payload.runtime),
            "provenance": dict(payload.provenance),
            "runner_sha256": runner_sha256,
            "hxx_opened_or_hashed": False,
            "calibrated_R": False,
            "scientific_pass": False,
            "product_pass": False,
        }
        _write_json(arguments.output / "GROUPS.json", list(payload.groups))
        _write_json(arguments.output / "RESULT.json", result)
        (arguments.output / "REPORT.md").write_text(
            "# Action04 authoritative articulated first-five-second run\n\n"
            f"Status: `{result['status']}`. The robust shared root is the only root "
            "translation and the existing fixed-root articulated IK/FK is committed "
            "through the same causal transaction. This remains an uncalibrated-R "
            "engineering diagnostic, not a scientific or product pass.\n",
            encoding="utf-8",
        )
    except BaseException as error:
        result = _failure_evidence(
            error, stage_trace=tuple(stage_trace), runner_sha256=runner_sha256
        )
        _write_fresh_json_atomic(arguments.output / "FAILURE.json", result)
    evidence_bytes = sum(
        path.stat().st_size for path in arguments.output.iterdir() if path.is_file()
    )
    if evidence_bytes >= 10_000_000:
        raise RuntimeError("raw evidence cap exceeded")
    seal_digest = _seal(
        arguments.output,
        (
            runner,
            ARTICULATED_REVISION / "SHA256SUMS",
            NATIVE200_WIRING_REVISION / "SHA256SUMS",
            ACTION04_NO_RAW_PREFLIGHT_REVISION / "SHA256SUMS",
            SOURCE_PAIR_DERIVATIVE_REVISION / "SHA256SUMS",
            SOURCE_PAIR_ORDER_PREFLIGHT_REVISION / "SHA256SUMS",
            TRANSPORT_REVISION / "SHA256SUMS",
            CONTACT_PROFILE_REVISION / "SHA256SUMS",
            CONTACT_PROFILE_REVISION / "RESULT.json",
            AXIS_OWNER_REPORT,
            AXIS_OWNER_TRAJECTORY,
            AXIS_OWNER_PREFLIGHT / "ALLOWLIST.json",
            AXIS_OWNER_PREFLIGHT / "SHA256SUMS",
            CORRECTED_OUTPUT_REPORT,
            FAILED_RAW_REVISION / "SHA256SUMS",
            FAILED_INTEGER_TIME_RAW_REVISION / "SHA256SUMS",
            PRIOR_INTEGER_TIME_DRY_REVISION / "SHA256SUMS",
            PRIOR_SOURCE_TICK_DRY_REVISION / "SHA256SUMS",
            FAILED_READONLY_RAW_REVISION / "SHA256SUMS",
            FAILED_READONLY_AUDIT_RAW_REVISION / "SHA256SUMS",
            FAILED_TEMPORAL_CLOSURE_RAW_REVISION / "SHA256SUMS",
            FAILED_DUAL_POSE_OWNER_RAW_REVISION / "SHA256SUMS",
            FAILED_SOURCE_PAIR_RAW_REVISION / "SHA256SUMS",
            FAILED_SOURCE_BASE_RAW_REVISION / "SHA256SUMS",
            SINGLE_POSE_OWNER_REVISION / "SHA256SUMS",
            PERFORMANCE_BASELINE_REVISION / "SHA256SUMS",
            PERFORMANCE_OPTIMIZED_REVISION / "SHA256SUMS",
            ANALYTIC_JACOBIAN_PREREGISTRATION / "SHA256SUMS",
            ANALYTIC_JACOBIAN_BENCHMARK / "SHA256SUMS",
            FAILED_CLOCK_DRY_REVISION / "SHA256SUMS",
            PRIOR_CLOCK_DRY_REVISION / "SHA256SUMS",
            ACTION_INTERVAL_REVISION / "CALIBRATION_INPUT_LINEAGE.json",
            ACTION_INTERVAL_REVISION / "SHA256SUMS",
            POSE_ACCEPTED_TRAJECTORY,
            POSE_FRONTEND_ARCHIVE,
            POSE_FRONTEND_MANIFEST,
            POSE_CLOCK_TABLE,
            *(ROOT / path for path in bound_sources),
        ),
    )
    print(json.dumps({
        "status": result["status"], "output": str(arguments.output),
        "runner_sha256": runner_sha256, "seal_sha256": seal_digest,
        "evidence_bytes": evidence_bytes,
    }, sort_keys=True))
    return 0 if result["status"].endswith("DIAGNOSTIC_PASS") else 2


def main() -> int:
    arguments = _parse_args()
    if arguments.output.exists():
        raise RuntimeError("retry/stale-output rejected: preregistered output already exists")

    if not arguments.dry_run:
        return _run_raw(arguments)

    bound_sources = _bind_raw_branch_sources(_verify_articulated_revision())
    runner_test = ROOT / "tests/test_c2_authoritative_articulated_action04_runner.py"
    bound_sources[str(runner_test.relative_to(ROOT))] = _sha256(runner_test)
    _verify_transport_seal()
    _load_sealed_contact_profile_document()
    _axis_report, _axis_model, axis_owner_audit = _load_and_fit_sealed_axis_owner()
    common_pose_clock_audit = _verify_common_global_pose_adapter()
    failed_raw_audit = _verify_failed_raw_nonpromoted()
    failed_source_pair_raw_audit = _verify_failed_source_pair_raw_nonpromoted()
    failed_source_base_raw_audit = _verify_failed_source_base_raw_nonpromoted()
    failed_integer_time_raw_audit = _verify_failed_integer_time_raw_nonpromoted()
    prior_integer_time_dry_audit = _verify_prior_integer_time_dry_nonpromoted()
    prior_source_tick_dry_audit = _verify_prior_source_tick_dry_nonpromoted()
    failed_readonly_raw_audit = _verify_failed_readonly_raw_nonpromoted()
    failed_readonly_audit_raw_audit = (
        _verify_failed_readonly_audit_raw_nonpromoted()
    )
    failed_temporal_closure_raw_audit = (
        _verify_failed_temporal_closure_raw_nonpromoted()
    )
    failed_dual_pose_owner_raw_audit = (
        _verify_failed_dual_pose_owner_raw_nonpromoted()
    )
    performance_boundary_audit = _verify_no_raw_performance_boundary()
    analytic_jacobian_audit = _verify_analytic_jacobian_benchmark()
    failed_clock_dry_audit = _verify_failed_clock_dry_nonpromoted()
    prior_clock_dry_audit = _verify_prior_clock_dry()
    runner = Path(__file__).resolve()
    runner_sha256 = _sha256(runner)

    arguments.output.mkdir(parents=False)
    contract = {
        "schema": "biospur.c2.authoritative-articulated.action04.dry-preregistration.v1",
        "mode": "NO_RAW_DRY_RUN_ONLY",
        "action": "04_shoulder_left",
        "maximum_raw_duration_s": 5.0,
        "maximum_attempts": 1,
        "raw_execution_authorized": False,
        "hxx_execution_authorized": False,
        "full_execution_authorized": False,
        "calibrated_R": False,
        "scientific_pass": False,
        "product_pass": False,
        "bound_articulated_revision": str(ARTICULATED_REVISION.relative_to(ROOT)),
        "bound_articulated_seal_sha256": ARTICULATED_SEAL_SHA256,
        "bound_transport_revision": str(TRANSPORT_REVISION.relative_to(ROOT)),
        "bound_transport_seal_sha256": TRANSPORT_SEAL_SHA256,
        "contact_profile_owner": {
            "artifact": str((CONTACT_PROFILE_REVISION / "RESULT.json").relative_to(ROOT)),
            "artifact_sha256": CONTACT_PROFILE_RESULT_SHA256,
            "seal": str((CONTACT_PROFILE_REVISION / "SHA256SUMS").relative_to(ROOT)),
            "seal_sha256": CONTACT_PROFILE_SEAL_SHA256,
            "raw_recalibration_allowed": False,
        },
        "axis_model_owner": axis_owner_audit,
        "common_global_pose_clock": common_pose_clock_audit,
        "corrected_output_report_provenance_only": {
            "path": str(CORRECTED_OUTPUT_REPORT.relative_to(ROOT)),
            "sha256": CORRECTED_OUTPUT_REPORT_SHA256,
            "owns_axes": False,
        },
        "failed_raw_revision_nonpromoted": failed_raw_audit,
        "failed_source_pair_raw_revision_nonpromoted": failed_source_pair_raw_audit,
        "failed_source_base_raw_revision_nonpromoted": failed_source_base_raw_audit,
        "source_pair_derivative_revision": {
            "path": str(SOURCE_PAIR_DERIVATIVE_REVISION.relative_to(ROOT)),
            "seal_sha256": SOURCE_PAIR_DERIVATIVE_SEAL_SHA256,
        },
        "failed_integer_time_raw_revision_nonpromoted": failed_integer_time_raw_audit,
        "prior_integer_time_dry_revision_nonpromoted": prior_integer_time_dry_audit,
        "prior_source_tick_dry_revision_nonpromoted": prior_source_tick_dry_audit,
        "failed_readonly_raw_revision_nonpromoted": failed_readonly_raw_audit,
        "failed_readonly_audit_raw_revision_nonpromoted": (
            failed_readonly_audit_raw_audit
        ),
        "failed_temporal_closure_raw_revision_nonpromoted": (
            failed_temporal_closure_raw_audit
        ),
        "failed_dual_pose_owner_raw_revision_nonpromoted": (
            failed_dual_pose_owner_raw_audit
        ),
        "no_raw_performance_boundary": performance_boundary_audit,
        "analytic_jacobian_benchmark": analytic_jacobian_audit,
        "failed_clock_dry_revision_nonpromoted": failed_clock_dry_audit,
        "prior_common_global_clock_dry": prior_clock_dry_audit,
        "runner_sha256": runner_sha256,
        "source_and_test_sha256": bound_sources,
        "fixtures": {
            "shared_root_node_availability": [10, 4, 1],
            "stance_slip_fail_closed": True,
            "swing_fall_allowed": True,
            "u1_atomic_rollback": True,
            "native200_cadence": True,
            "integer_nanosecond_cadence_owner": True,
            "separate_temporal_closure_imu": True,
            "submitted_imu_inventory": {
                "metric": 1000, "uwb_delivery_context": 6,
                "temporal_closure": 1, "total": 1007,
            },
            "observed_consecutive_pelvis_source_ticks": True,
            "decoded_none_global_time_single_clock_adapter": True,
            "async_output_drain": True,
            "common_global_fraction_clock": True,
        },
    }
    _write_json(arguments.output / "CONTRACT.json", contract)

    trace_path = arguments.output / "OPEN_TRACE.txt"
    command = [
        "/usr/bin/strace", "-f", "-qq", "-e", "trace=open,openat,creat",
        "-o", str(trace_path), sys.executable, "-m", "pytest", "-q", *TEST_SELECTORS,
    ]
    (arguments.output / "TEST_COMMAND.txt").write_text(
        " ".join(command) + "\n", encoding="utf-8"
    )
    started = time.monotonic()
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env={
            **os.environ,
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "PYTHONPATH": "src:tools:.",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    process_group = process.pid
    try:
        stdout, stderr = process.communicate(timeout=120.0)
    except subprocess.TimeoutExpired:
        os.killpg(process_group, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            os.killpg(process_group, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=5.0)
        raise RuntimeError("focused no-raw dry fixture exceeded 120 s")
    wall_s = time.monotonic() - started
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    maximum_rss_kib = int(usage_after.ru_maxrss)
    residual_pids = _process_group_members(process_group)
    (arguments.output / "TEST_STDOUT.txt").write_text(stdout, encoding="utf-8")
    (arguments.output / "TEST_STDERR.txt").write_text(stderr, encoding="utf-8")
    trace = _trace_audit(trace_path)
    trace["process_group"] = process_group
    trace["residual_pids"] = residual_pids
    _write_json(arguments.output / "TRACE_AUDIT.json", trace)

    passed = re.search(r"(\d+) passed", stdout)
    failures: list[str] = []
    if process.returncode != 0:
        failures.append(f"pytest_exit_{process.returncode}")
    if passed is None or int(passed.group(1)) < 11:
        failures.append("expected_fixture_count_not_observed")
    if trace["raw_paths"]:
        failures.append("raw_file_open_detected")
    if trace["hxx_paths"]:
        failures.append("HXX_file_open_detected")
    if residual_pids:
        failures.append("residual_process_detected")
    if wall_s > 120.0:
        failures.append("test_wall_budget_exceeded")
    if maximum_rss_kib >= 300_000:
        failures.append("rss_budget_exceeded")

    result = {
        "status": "DRY_PREREGISTRATION_PASS" if not failures else "DRY_PREREGISTRATION_FAIL",
        "failures": failures,
        "runner_sha256": runner_sha256,
        "tests": {
            "exit_status": process.returncode,
            "passed": int(passed.group(1)) if passed else None,
            "wall_s": wall_s,
            "maximum_rss_kib": maximum_rss_kib,
            "rusage_user_s_delta": usage_after.ru_utime - usage_before.ru_utime,
            "rusage_system_s_delta": usage_after.ru_stime - usage_before.ru_stime,
        },
        "raw_data_opened": bool(trace["raw_paths"]),
        "hxx_opened_or_hashed": bool(trace["hxx_paths"]),
        "residual_pids": residual_pids,
        "action04_raw_run_started": False,
        "attempts_consumed": 0,
        "mechanism_qualification_inherited": not failures,
        "scientific_pass": False,
        "product_pass": False,
        "next_state": "HOLD_FOR_INDEPENDENT_RUNNER_HASH_AUDIT",
    }
    _write_json(arguments.output / "RESULT.json", result)
    report = (
        "# Action04 articulated dry preregistration\n\n"
        f"Status: `{result['status']}`.\n\n"
        "This run used only deterministic no-raw fixtures. It exercised the "
        "10/10, 4/10 and 1/10 shared-root paths, stance-slip rejection, "
        "swing/fall allowance, U1 rollback, native-200 cadence and asynchronous "
        "output drain. It separately owned one actual native-200 temporal-closure "
        "sample after the final UWB group without changing the 1000 metric plus "
        "six delivery-context inventory. It also exercised the sealed Action04 common-global "
        "fraction-to-strict-floor adapter, including endpoint, interior, stale-"
        "domain and equal-timestamp guards. No action04 raw data or H01/H02 input "
        "was opened.\n\n"
        "The next state is HOLD. A real five-second action04 run remains "
        "unauthorized until an independent monitor confirms this exact runner hash.\n"
    )
    (arguments.output / "REPORT.md").write_text(report, encoding="utf-8")
    evidence_bytes = sum(path.stat().st_size for path in arguments.output.iterdir())
    if evidence_bytes >= 10_000_000:
        failures.append("evidence_budget_exceeded")
        result["status"] = "DRY_PREREGISTRATION_FAIL"
        result["failures"] = failures
        _write_json(arguments.output / "RESULT.json", result)
    seal_digest = _seal(
        arguments.output,
        (
            runner,
            ARTICULATED_REVISION / "SHA256SUMS",
            NATIVE200_WIRING_REVISION / "SHA256SUMS",
            ACTION04_NO_RAW_PREFLIGHT_REVISION / "SHA256SUMS",
            SOURCE_PAIR_DERIVATIVE_REVISION / "SHA256SUMS",
            SOURCE_PAIR_ORDER_PREFLIGHT_REVISION / "SHA256SUMS",
            TRANSPORT_REVISION / "SHA256SUMS",
            CONTACT_PROFILE_REVISION / "SHA256SUMS",
            CONTACT_PROFILE_REVISION / "RESULT.json",
            AXIS_OWNER_REPORT,
            AXIS_OWNER_TRAJECTORY,
            AXIS_OWNER_PREFLIGHT / "ALLOWLIST.json",
            AXIS_OWNER_PREFLIGHT / "SHA256SUMS",
            CORRECTED_OUTPUT_REPORT,
            FAILED_RAW_REVISION / "SHA256SUMS",
            FAILED_INTEGER_TIME_RAW_REVISION / "SHA256SUMS",
            PRIOR_INTEGER_TIME_DRY_REVISION / "SHA256SUMS",
            PRIOR_SOURCE_TICK_DRY_REVISION / "SHA256SUMS",
            FAILED_READONLY_RAW_REVISION / "SHA256SUMS",
            FAILED_READONLY_AUDIT_RAW_REVISION / "SHA256SUMS",
            FAILED_TEMPORAL_CLOSURE_RAW_REVISION / "SHA256SUMS",
            FAILED_DUAL_POSE_OWNER_RAW_REVISION / "SHA256SUMS",
            FAILED_SOURCE_PAIR_RAW_REVISION / "SHA256SUMS",
            FAILED_SOURCE_BASE_RAW_REVISION / "SHA256SUMS",
            SINGLE_POSE_OWNER_REVISION / "SHA256SUMS",
            PERFORMANCE_BASELINE_REVISION / "SHA256SUMS",
            PERFORMANCE_OPTIMIZED_REVISION / "SHA256SUMS",
            ANALYTIC_JACOBIAN_PREREGISTRATION / "SHA256SUMS",
            ANALYTIC_JACOBIAN_BENCHMARK / "SHA256SUMS",
            FAILED_CLOCK_DRY_REVISION / "SHA256SUMS",
            PRIOR_CLOCK_DRY_REVISION / "SHA256SUMS",
            ACTION_INTERVAL_REVISION / "CALIBRATION_INPUT_LINEAGE.json",
            ACTION_INTERVAL_REVISION / "SHA256SUMS",
            POSE_ACCEPTED_TRAJECTORY,
            POSE_FRONTEND_ARCHIVE,
            POSE_FRONTEND_MANIFEST,
            POSE_CLOCK_TABLE,
            *(ROOT / path for path in bound_sources),
        ),
    )
    print(json.dumps({
        "status": result["status"],
        "output": str(arguments.output),
        "runner_sha256": runner_sha256,
        "seal_sha256": seal_digest,
        "tests_passed": result["tests"]["passed"],
        "wall_s": wall_s,
        "maximum_rss_kib": maximum_rss_kib,
    }, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
