"""Manifest-bound C1 raw-range/T4/M1 readers and exact lineage reconstruction."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np


REPOSITORY = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion")
M1_PATH = Path("/tmp/biospur_pure_imu_mvp_m1_20260823T135120Z/CAPTURE1_MVP_REPLAY_DATA.npz")
SCHEDULE_PATH = Path("/tmp/biospur_c123_uwb_beacon_causal_replay_20260823T171739Z/C123_UWB_EVENT_SCHEDULE.npz")
CLOCK_PATH = Path("/tmp/biospur_c123_uwb_beacon_causal_replay_20260823T171739Z/CLOCK_DOMAIN_AND_TIMESTAMP_CONTRACT.json")
EPOCH_AUDIT_PATH = Path("/tmp/biospur_c123_uwb_beacon_causal_replay_20260823T171739Z/C123_BEACON_EPOCH_MAPPING_AUDIT.json")
RAW_PATH = Path("/tmp/biospur_c123_uwb_counterfactual_20260823T155948Z/RAW_UWB_TAG_TRAJECTORIES.npz")
PROVENANCE_PATH = Path("/tmp/biospur_c123_uwb_root_r2_20260824T035440Z/C1_INPUT_AND_PARTITION_PROVENANCE.json")
RAW_COBS_PATH = REPOSITORY / "Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/continuous_collector/fusion_host_raw.cobs.bin"
FRAME_AUDIT_PATH = REPOSITORY / "Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/FRAME_BINDING_RESULT.json"
LAYOUT_PATH = REPOSITORY / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
CAPTURE_MANIFEST_PATH = REPOSITORY / "Fusion_Part/config/captures/v47_ten_node_body_calibration_20260814_093601.json"


SEGMENT_POINT_JOINTS = {
    "torso": ("pelvis", "torso_top"),
    "pelvis": ("pelvis", "pelvis"),
    "upper_arm_left": ("shoulder_left", "elbow_left"),
    "forearm_left": ("elbow_left", "wrist_left"),
    "upper_arm_right": ("shoulder_right", "elbow_right"),
    "forearm_right": ("elbow_right", "wrist_right"),
    "thigh_left": ("hip_left", "knee_left"),
    "shank_left": ("knee_left", "ankle_left"),
    "thigh_right": ("hip_right", "knee_right"),
    "shank_right": ("knee_right", "ankle_right"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(16 << 20):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class C1Data:
    nodes: tuple[str, ...]
    segments: tuple[str, ...]
    node_index: np.ndarray
    source_index: np.ndarray
    epoch: np.ndarray
    sweep: np.ndarray
    measurement_s: np.ndarray
    availability_s: np.ndarray
    strobe_s: np.ndarray
    t4_xyz_m: np.ndarray
    t4_used_mask: np.ndarray
    t4_covariance_diag_m2: np.ndarray
    t4_residual_m: np.ndarray
    root_relative_n_m: np.ndarray
    raw_root_relative_n_m: np.ndarray
    m1_source_left: np.ndarray
    m1_source_right: np.ndarray
    m1_interpolated: np.ndarray
    raw_record_index: np.ndarray
    raw_start: np.ndarray
    raw_end: np.ndarray
    packet_sequence: np.ndarray
    anchor_id: np.ndarray
    raw_range_m: np.ndarray
    raw_valid: np.ndarray
    raw_measurement_s: np.ndarray
    raw_availability_s: np.ndarray
    quality_percent: np.ndarray
    t_round_us: np.ndarray
    cfo_ppm: np.ndarray
    anchors_v4_m: np.ndarray
    anchor_delay_m: np.ndarray
    clock_slopes: np.ndarray
    m1: dict[str, np.ndarray]

    @property
    def event_count(self) -> int:
        return int(len(self.node_index))

    @property
    def raw_event_count(self) -> int:
        return int(np.sum(self.raw_valid))

    def raw_event_id(self, event: int, slot: int) -> str:
        return f"C1:{self.nodes[int(self.node_index[event])]}:record={int(self.raw_record_index[event])}:anchor={int(self.anchor_id[event, slot])}"

    def t4_event_id(self, event: int) -> str:
        return f"C1:{self.nodes[int(self.node_index[event])]}:record={int(self.raw_record_index[event])}:T4"

    def constituent_ids(self, event: int) -> tuple[str, ...]:
        mask = int(self.t4_used_mask[event])
        return tuple(self.raw_event_id(event, slot) for slot in range(8)
                     if mask & (1 << int(self.anchor_id[event, slot])))


def _load_m1() -> dict[str, np.ndarray]:
    with np.load(M1_PATH, allow_pickle=False) as archive:
        return {name: archive[name] for name in archive.files}


def m1_relative_points(m1: dict[str, np.ndarray], node_index: np.ndarray,
                       query_s: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Linearly interpolate frozen FK for offline frame diagnostics only."""

    times = np.asarray(m1["time_s"], float)
    joints = [str(value) for value in m1["joint_names"]]
    lookup = {name: index for index, name in enumerate(joints)}
    segments = [str(value) for value in m1["segment_names"]]
    positions = np.asarray(m1["joint_positions_m"], float)
    available = np.asarray(m1["joint_available"], bool)
    right = np.searchsorted(times, query_s, side="right")
    right = np.clip(right, 1, len(times) - 1)
    left = right - 1
    result = np.full((len(query_s), 3), np.nan)
    valid = np.zeros(len(query_s), bool)
    interpolated = np.zeros(len(query_s), bool)
    for node in np.unique(node_index):
        rows = np.flatnonzero(node_index == node)
        a_name, b_name = SEGMENT_POINT_JOINTS[segments[int(node)]]
        a, b = lookup[a_name], lookup[b_name]
        ok = (available[left[rows], a] & available[left[rows], b] &
              available[right[rows], a] & available[right[rows], b])
        local = rows[ok]
        if not len(local):
            continue
        p_left = 0.5 * (positions[left[local], a] + positions[left[local], b])
        p_right = 0.5 * (positions[right[local], a] + positions[right[local], b])
        denominator = times[right[local]] - times[left[local]]
        weight = np.divide(query_s[local] - times[left[local]], denominator,
                           out=np.zeros(len(local)), where=denominator > 0.0)
        result[local] = p_left + weight[:, None] * (p_right - p_left)
        valid[local] = True
        interpolated[local] = right[local] != left[local]
    in_bounds = (query_s >= times[0]) & (query_s <= times[-1])
    valid &= in_bounds
    result[~valid] = np.nan
    return result, valid, left.astype(np.int32), right.astype(np.int32), interpolated


def load_c1() -> tuple[C1Data, dict]:
    """Load C1 and prove exact raw-record -> canonical-T4 lineage."""

    m1 = _load_m1()
    nodes = tuple(str(value) for value in m1["node_ids"])
    segments = tuple(str(value) for value in m1["segment_names"])
    with np.load(SCHEDULE_PATH, allow_pickle=False) as schedule:
        node_index = schedule["c1_node_index"].astype(np.int16)
        source_index = schedule["c1_source_index"].astype(np.int32)
        epoch = schedule["c1_epoch"].astype(np.int64)
        sweep = schedule["c1_sweep"].astype(np.uint32)
        measurement = schedule["c1_measurement_s"].astype(float)
        availability = schedule["c1_available_s"].astype(float)
        strobe = schedule["c1_strobe_s"].astype(float)
        xyz = schedule["c1_xyz_m"].astype(float)
        used_mask = schedule["c1_used_mask"].astype(np.uint8)
    count = len(node_index)
    shape8 = (count, 8)
    raw_record = np.empty(count, np.uint64); raw_start = np.empty(count, np.uint64); raw_end = np.empty(count, np.uint64)
    packet = np.empty(count, np.uint32); raw_sweep = np.empty(count, np.uint32)
    anchor_id = np.empty(shape8, np.uint8); ranges = np.empty(shape8, float); quality = np.empty(shape8, np.uint8)
    tround = np.empty(shape8, np.uint16); cfo = np.empty(shape8, float); valid_mask = np.empty(count, np.uint8)
    solved_xyz = np.empty((count, 3), float); solved_used = np.empty(count, np.uint8)
    solved_cov = np.empty((count, 3), float); solved_residual = np.empty(shape8, float)
    with np.load(RAW_PATH, allow_pickle=False) as raw:
        for node_number, node in enumerate(nodes):
            rows = np.flatnonzero(node_index == node_number); source = source_index[rows]
            prefix = f"c1_{node}_"
            raw_record[rows] = raw[prefix + "raw_record_index"][source]
            raw_start[rows] = raw[prefix + "raw_start"][source]; raw_end[rows] = raw[prefix + "raw_end"][source]
            packet[rows] = raw[prefix + "packet_sequence"][source]; raw_sweep[rows] = raw[prefix + "sweep"][source]
            anchor_id[rows] = raw[prefix + "anchor_id"][source]
            ranges[rows] = raw[prefix + "range_mm"][source].astype(float) / 1000.0
            quality[rows] = raw[prefix + "quality"][source]
            tround[rows] = raw[prefix + "t_round_us"][source]
            cfo[rows] = raw[prefix + "cfo_ppm_q8"][source].astype(float) / 256.0
            valid_mask[rows] = raw[prefix + "valid_mask"][source]
            solved_xyz[rows] = raw[prefix + "solved_xyz_m"][source]
            solved_used[rows] = raw[prefix + "solved_used_mask"][source]
            solved_cov[rows] = raw[prefix + "solved_covariance_diag_m2"][source]
            solved_residual[rows] = raw[prefix + "solved_residuals_m"][source]
    exact_checks = {
        "sweep_exact": bool(np.array_equal(sweep, raw_sweep)),
        "t4_xyz_bit_exact": bool(np.array_equal(xyz.astype(np.float32), solved_xyz.astype(np.float32))),
        "t4_used_mask_exact": bool(np.array_equal(used_mask, solved_used)),
        "anchor_slots_exact_0_to_7": bool(np.all(anchor_id == np.arange(8, dtype=np.uint8))),
        "measurement_not_after_availability": bool(np.all(measurement <= availability + 1e-12)),
        "unique_node_source_pairs": len(set(zip(node_index.tolist(), source_index.tolist()))) == count,
    }
    if not all(exact_checks.values()):
        raise RuntimeError(f"C1 lineage closure failed: {exact_checks}")
    raw_valid = (((valid_mask[:, None] >> np.arange(8)) & 1).astype(bool) &
                 (ranges > 0.0) & (ranges < 65.535))
    epoch_audit = json.loads(EPOCH_AUDIT_PATH.read_text(encoding="utf-8"))
    capture_row = epoch_audit["captures"]["1"] if isinstance(epoch_audit.get("captures"), dict) else epoch_audit
    models = capture_row["mapping_models"]
    slopes = np.asarray([float(models[node]["slope_common_s_per_local_s"]) for node in nodes])
    raw_measurement = strobe[:, None] + slopes[node_index, None] * tround.astype(float) * 0.5e-6
    raw_availability = np.broadcast_to(availability[:, None], shape8).copy()
    relative, m1_valid, left, right, interpolated = m1_relative_points(m1, node_index, measurement)
    raw_relative_flat, _, _, _, _ = m1_relative_points(
        m1, np.repeat(node_index, 8), raw_measurement.reshape(-1))
    raw_relative = raw_relative_flat.reshape(count, 8, 3)
    layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    anchors = np.asarray([[row["x_mm"], row["y_mm"], row["z_mm"]] for row in layout["anchors"]], float) / 1000.0
    delays = np.asarray([row.get("d_anchor_mm", 0.0) for row in layout["anchors"]], float) / 1000.0
    data = C1Data(nodes, segments, node_index, source_index, epoch, sweep, measurement, availability, strobe,
                  xyz, used_mask, solved_cov, solved_residual, relative, raw_relative, left, right, interpolated,
                  raw_record, raw_start, raw_end, packet, anchor_id, ranges, raw_valid, raw_measurement,
                  raw_availability, quality, tround, cfo, anchors, delays, slopes, m1)
    constituent_counts = np.asarray([int(int(mask).bit_count()) for mask in used_mask])
    audit = {
        "schema": "biospur.root_r4.c1_lineage_load.v1",
        "capture": 1,
        "t4_events": count,
        "raw_valid_events": int(np.sum(raw_valid)),
        "exact_constituent_t4_events": count,
        "conservative_envelope_t4_events": 0,
        "unresolved_t4_events": 0,
        "checks": exact_checks,
        "constituent_range_count": {
            "minimum": int(np.min(constituent_counts)), "maximum": int(np.max(constituent_counts)),
            "median": float(np.median(constituent_counts)), "total_factor_memberships": int(np.sum(constituent_counts)),
        },
        "m1_geometry_available_events": int(np.sum(m1_valid)),
        "m1_geometry_unavailable_events": int(np.sum(~m1_valid)),
        "clock": {
            "raw_measurement": "mapped common time = strobe_s + node affine slope * t_round_us/2",
            "raw_geometry": "frozen M1 FK linearly interpolated independently at every per-anchor raw measurement time",
            "t4_measurement": "mapped common time = strobe_s + mean(node affine slope*t_round_us_used/2)",
            "availability": "mapped common time of completed-UART-frame lower bound (frame_us), shared by links in record",
            "source_clock": "per-node B306 TIMER2 1 MHz mapped to beacon/common capture-relative time",
        },
    }
    return data, audit


def primary_input_inventory() -> list[dict]:
    paths = (M1_PATH, SCHEDULE_PATH, CLOCK_PATH, EPOCH_AUDIT_PATH, RAW_PATH, PROVENANCE_PATH,
             RAW_COBS_PATH, FRAME_AUDIT_PATH, LAYOUT_PATH, CAPTURE_MANIFEST_PATH)
    return [{"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path), "read_only": True}
            for path in paths]
