"""Source-owned exact-tick native-200 publication producer for calibration."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_calibration.antenna_los import (
    NODE_OUTWARD_MINUS_Z_IN_SEGMENT,
    outward_normal_world,
    rotation_from_wxyz,
)
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS, corrected_proxy_points
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import Native200ClockMappingOwner
from biospur_fusion.ingest.events import EventStatus, RecordType, TypedEvent
from biospur_fusion.root_r3.models import ImuSample

from .continuous_native200_bridge import AuthoritativeNative200PosePublication
from .contracts import EPISODES, NODE_TO_SEGMENT, ROOT

ACTION_KEYS = MappingProxyType({
    action: f"{index:02d}" for index, action in enumerate(EPISODES)
})
PELVIS_NODE = "BSFC2CC"
CONTACT_NOT_APPLIED_OWNER = hashlib.sha256(b"CONTACT_NOT_APPLIED_IN_00_GAP_02_TRANSPORT_GATE").hexdigest()
FRONTEND = ROOT / "logs/c2_basis_progressive_20260829T102836Z/CONTINUATION_SPRINT/C2_NONHINGE_TRAINING_REPLAY_001/FRONTEND_RECONSTRUCTION_INPUTS.npz"
FRONTEND_MANIFEST = FRONTEND.with_suffix(".json")
TRAJECTORY = ROOT / "logs/c2_native200_orientation_constrained_biomechanics_v4_20260904/ARTICULATED_CALIBRATION_TRAJECTORY.npz"
CLOCK_TABLE = ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json"
EXPECTED = MappingProxyType({
    FRONTEND: "58f88f9fb59d64a20c9c3c1f29db2309eb2a38e6fb3bd62d982969b51bf54cd7",
    FRONTEND_MANIFEST: "db1ed458cbc80ad07cd1a885d5ac42498ab6057ea560d6535524ae244b5e7a22",
    TRAJECTORY: "94f9afb088c7f05a7dbcae0c7d6d2c18be76a6ca32e1d9b96861a8deb7962937",
    CLOCK_TABLE: "b3c18d2d0ece3826498d2adc3cd41f3e4412794557f8525adc2f73bfa4ae3a66",
})


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps({"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True).encode()
    return hashlib.sha256(header + array.tobytes()).hexdigest()


def accepted_pose_frame(action_data, frame: int, alignment: np.ndarray, geometry):
    """Pure Action04-compatible segment/FK/body rule."""
    aligned = np.asarray(alignment, float).reshape(3, 3)
    rotations = {
        segment: aligned @ rotation_from_wxyz(action_data[segment]["quat_world_segment_wxyz"][frame])
        for segment in SEGMENTS
    }
    points = corrected_proxy_points(rotations, {segment: np.zeros(3) for segment in SEGMENTS}, geometry)
    normals = {
        node: outward_normal_world(
            node, action_data[NODE_TO_SEGMENT[node]]["quat_world_segment_wxyz"][frame], aligned,
        )
        for node in NODE_TO_SEGMENT
    }
    return rotations, points, normals


def accepted_pose_frames(
    action_data, frames: tuple[int, ...], alignment: np.ndarray, geometry,
):
    """Pure structural batch equivalent of independent accepted pose frames."""
    if type(frames) is not tuple or not 1 <= len(frames) <= 16:
        raise ValueError("pose batch requires 1..16 frames")
    aligned = np.asarray(alignment, float).reshape(3, 3)
    quaternions = np.stack([
        [action_data[segment]["quat_world_segment_wxyz"][frame] for segment in SEGMENTS]
        for frame in frames
    ])
    matrices = Rotation.from_quat(
        quaternions[..., [1, 2, 3, 0]].reshape(-1, 4)
    ).as_matrix().reshape(len(frames), len(SEGMENTS), 3, 3)
    rotations = aligned[None, None, :, :] @ matrices
    segment_index = {segment: index for index, segment in enumerate(SEGMENTS)}

    def rotated(segment: str, vector) -> np.ndarray:
        return rotations[:, segment_index[segment]] @ np.asarray(vector, float)

    length = geometry.segment_length_m
    root = np.zeros((len(frames), 3))
    shoulder_mid = rotated("torso", [0.0, 0.0, geometry.torso_height_m])
    shoulder_left = shoulder_mid + rotated(
        "torso", [-0.5 * geometry.shoulder_span_m, 0.0, 0.0]
    )
    shoulder_right = shoulder_mid + rotated(
        "torso", [0.5 * geometry.shoulder_span_m, 0.0, 0.0]
    )
    hip_left = rotated("pelvis", [-0.5 * geometry.hip_span_m, 0.0, 0.0])
    hip_right = rotated("pelvis", [0.5 * geometry.hip_span_m, 0.0, 0.0])
    elbow_left = shoulder_left + rotated(
        "upper_arm_left", [0.0, 0.0, -length["upper_arm_left"]]
    )
    wrist_left = elbow_left + rotated(
        "forearm_left", [0.0, 0.0, -length["forearm_left"]]
    )
    elbow_right = shoulder_right + rotated(
        "upper_arm_right", [0.0, 0.0, -length["upper_arm_right"]]
    )
    wrist_right = elbow_right + rotated(
        "forearm_right", [0.0, 0.0, -length["forearm_right"]]
    )
    knee_left = hip_left + rotated(
        "thigh_left", [0.0, 0.0, -length["thigh_left"]]
    )
    ankle_left = knee_left + rotated(
        "shank_left", [0.0, 0.0, -length["shank_left"]]
    )
    knee_right = hip_right + rotated(
        "thigh_right", [0.0, 0.0, -length["thigh_right"]]
    )
    ankle_right = knee_right + rotated(
        "shank_right", [0.0, 0.0, -length["shank_right"]]
    )
    points = dict(zip(
        (
            "pelvis_center", "shoulder_mid", "shoulder_left", "shoulder_right",
            "hip_left", "hip_right", "elbow_left", "wrist_left",
            "elbow_right", "wrist_right", "knee_left", "ankle_left",
            "knee_right", "ankle_right",
        ),
        (
            root, shoulder_mid, shoulder_left, shoulder_right, hip_left,
            hip_right, elbow_left, wrist_left, elbow_right, wrist_right,
            knee_left, ankle_left, knee_right, ankle_right,
        ),
    ))
    normals = {}
    for node, segment in NODE_TO_SEGMENT.items():
        value = rotations[:, segment_index[segment]] @ NODE_OUTWARD_MINUS_Z_IN_SEGMENT[node]
        normals[node] = value / np.linalg.norm(value, axis=1)[:, None]
    return tuple(
        (
            {segment: rotations[row, index] for index, segment in enumerate(SEGMENTS)},
            {name: value[row] for name, value in points.items()},
            {node: value[row] for node, value in normals.items()},
        )
        for row in range(len(frames))
    )


def _immutable_array(value: np.ndarray) -> np.ndarray:
    array = np.ascontiguousarray(np.asarray(value))
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)


def _immutable_mapping(values: Mapping[str, np.ndarray]) -> Mapping[str, np.ndarray]:
    return MappingProxyType({name: _immutable_array(value) for name, value in values.items()})


@dataclass(frozen=True)
class _PreparedPoseRow:
    owner: object
    record: TypedEvent
    raw_identity: tuple[int, int, int, int, str]
    source_owner: object
    action_id: str
    frame: int
    span: int
    expected_revision: int
    rotations: Mapping[str, np.ndarray]
    points: Mapping[str, np.ndarray]
    normals: Mapping[str, np.ndarray]

    def __deepcopy__(self, memo):
        return self


@dataclass(frozen=True)
class PublicationActionSource:
    action_id: str
    key: str
    time_us: np.ndarray
    boot: np.ndarray
    span: np.ndarray
    calibrated_acc_mps2: np.ndarray
    sensor_quat_wxyz: np.ndarray
    trajectory: Mapping[str, Mapping[str, np.ndarray]]
    array_hashes: Mapping[str, str]

    def __post_init__(self):
        if ACTION_KEYS.get(self.action_id) != self.key:
            raise ValueError("action/physical trajectory key mismatch")
        arrays = {
            "time_us": np.asarray(self.time_us), "derived_boot_epoch": np.asarray(self.boot),
            "contiguous_span_id": np.asarray(self.span), "acc_mps2": np.asarray(self.calibrated_acc_mps2),
            "quat_world_sensor_wxyz": np.asarray(self.sensor_quat_wxyz),
        }
        n = len(arrays["time_us"])
        if n < 2 or arrays["time_us"].shape != (n,) or arrays["derived_boot_epoch"].shape != (n,) or arrays["contiguous_span_id"].shape != (n,) or arrays["acc_mps2"].shape != (n, 3) or arrays["quat_world_sensor_wxyz"].shape != (n, 4) or np.any(np.diff(arrays["time_us"]) <= 0):
            raise ValueError("invalid frontend source inventory")
        if set(self.array_hashes) != set(arrays) or any(array_sha256(value) != self.array_hashes[name] for name, value in arrays.items()):
            raise ValueError("frontend array binding mismatch")
        for field, name in (("time_us", "time_us"), ("boot", "derived_boot_epoch"), ("span", "contiguous_span_id"), ("calibrated_acc_mps2", "acc_mps2"), ("sensor_quat_wxyz", "quat_world_sensor_wxyz")):
            value = np.array(arrays[name], copy=True); value.setflags(write=False); object.__setattr__(self, field, value)
        if set(self.trajectory) != set(SEGMENTS):
            raise ValueError("trajectory segment inventory mismatch")
        frozen = {}
        frontend_time_s = (arrays["time_us"] - arrays["time_us"][0]).astype(float) * 1e-6
        reference_root_time = None
        for segment in SEGMENTS:
            q = np.asarray(self.trajectory[segment]["quat_world_segment_wxyz"], float)
            m = np.asarray(self.trajectory[segment]["mask"], bool)
            root_time = np.asarray(self.trajectory[segment]["time_root_s"], float)
            if q.shape != (n, 4) or m.shape != (n,) or root_time.shape != (n,):
                raise ValueError("trajectory frame inventory mismatch")
            if not np.all(np.isfinite(root_time)) or np.any(np.diff(root_time) <= 0.0):
                raise ValueError("invalid trajectory root time")
            if np.max(np.abs((root_time - root_time[0]) - frontend_time_s)) > 1e-9:
                raise ValueError("frontend/trajectory time ownership mismatch")
            if reference_root_time is not None and not np.array_equal(root_time, reference_root_time):
                raise ValueError("segment trajectory times differ")
            reference_root_time = root_time
            q = q.copy(); m = m.copy(); root_time = root_time.copy()
            q.setflags(write=False); m.setflags(write=False); root_time.setflags(write=False)
            frozen[segment] = MappingProxyType({
                "quat_world_segment_wxyz": q, "mask": m, "time_root_s": root_time,
            })
        object.__setattr__(self, "trajectory", MappingProxyType(frozen))
        object.__setattr__(self, "array_hashes", MappingProxyType(dict(self.array_hashes)))


class Native200PublicationProducer:
    def __init__(self, *, sources, clocks, alignment, geometry, geometry_owner_digest,
                 frontend_sha256, frontend_manifest_sha256, trajectory_sha256,
                 clock_table_sha256, clock_source_sha256, publication_owner_sha256,
                 body_rule_owners, frontend_manifest_schema,
                 frontend_reconstruction_role):
        if (
            not sources
            or set(sources) != set(clocks)
            or not set(sources).issubset(ACTION_KEYS)
        ):
            raise ValueError("producer requires a matching acquired-calibration source inventory")
        self._sources = MappingProxyType(dict(sources)); self._clocks = MappingProxyType(dict(clocks))
        self._alignment = np.asarray(alignment, float).reshape(3, 3).copy(); self._alignment.setflags(write=False)
        if not np.allclose(self._alignment.T @ self._alignment, np.eye(3), atol=1e-10) or np.linalg.det(self._alignment) < 1 - 1e-10:
            raise ValueError("invalid frozen world alignment")
        self._geometry = geometry
        if not frontend_manifest_schema or not frontend_reconstruction_role:
            raise ValueError("missing frontend manifest ownership")
        if not body_rule_owners or any(not isinstance(name, str) for name in body_rule_owners):
            raise ValueError("missing named FK/normal/body owner map")
        owners = (geometry_owner_digest, frontend_sha256, frontend_manifest_sha256,
                  trajectory_sha256, clock_table_sha256, clock_source_sha256,
                  publication_owner_sha256, *body_rule_owners.values())
        if any(len(v) != 64 or any(c not in "0123456789abcdef" for c in v) for v in owners):
            raise ValueError("invalid producer owner digest")
        body_rule_owners = MappingProxyType(dict(sorted(body_rule_owners.items())))
        body_rule_digest = hashlib.sha256(json.dumps(
            dict(body_rule_owners), sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        self.frontend_sha256 = frontend_sha256; self.publication_owner_sha256 = publication_owner_sha256
        self.clock_source_sha256 = clock_source_sha256; self.body_rule_digest = body_rule_digest
        self.base_pose_owner_digest = hashlib.sha256(json.dumps({
            "trajectory": trajectory_sha256, "clock_table": clock_table_sha256,
            "frontend": frontend_sha256, "frontend_manifest": frontend_manifest_sha256,
            "frontend_manifest_schema": frontend_manifest_schema,
            "frontend_reconstruction_role": frontend_reconstruction_role,
            "actions": dict(ACTION_KEYS), "alignment": self._alignment.tolist(),
            "geometry_owner": geometry_owner_digest, "body_rule": body_rule_digest,
            "body_rule_owners": dict(body_rule_owners),
            "clock_source": clock_source_sha256,
            "contact_owner": CONTACT_NOT_APPLIED_OWNER,
            "measurement": "CALIBRATED_FRONTEND_ACC;RAW_ACC_IDENTITY_SEPARATE;EXACT_TICK",
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        tick_index = {}
        for action, source in self._sources.items():
            for frame, (boot, timer) in enumerate(zip(source.boot, source.time_us)):
                key = (int(boot), int(timer))
                if key in tick_index:
                    raise ValueError("native200 hardware tick is owned by multiple actions")
                tick_index[key] = (action, frame)
        self._tick_index = MappingProxyType(tick_index)
        self._revision = 0; self._last_ns = None; self._rank = -1
        self.__pose_row_owner = object()

    @classmethod
    def from_sealed_archives(cls):
        for path, expected in EXPECTED.items():
            if file_sha256(path) != expected: raise RuntimeError(f"sealed publication owner changed: {path}")
        manifest = json.loads(FRONTEND_MANIFEST.read_text()); clock_doc = json.loads(CLOCK_TABLE.read_text())
        clock_source = ROOT / "src/biospur_fusion/c2_uwb_root_world/beacon_clock.py"
        if file_sha256(clock_source) != clock_doc["source_sha256"]: raise RuntimeError("clock source binding changed")
        cm = clock_doc["models"][PELVIS_NODE]
        clock = Native200ClockMappingOwner(PELVIS_NODE, "B306_TIMER2", int(cm["boot_epoch"]), float(cm["a_ns_per_us"]), float(cm["b_ns"]), EXPECTED[CLOCK_TABLE])
        sources = {}
        with np.load(FRONTEND, allow_pickle=False) as f, np.load(TRAJECTORY, allow_pickle=False) as t:
            for action, key in ACTION_KEYS.items():
                base = f"orientation/{key}/{PELVIS_NODE}"
                paths = {name: f"{base}/{name}" for name in ("time_us", "derived_boot_epoch", "contiguous_span_id", "acc_mps2", "quat_world_sensor_wxyz")}
                values = {name: np.array(f[path], copy=True) for name, path in paths.items()}
                hashes = {name: manifest["array_bindings"][path]["sha256"] for name, path in paths.items()}
                traj = {segment: {
                    "time_root_s": np.array(t[f"trajectory/{key}/{segment}/time_root_s"], copy=True),
                    "quat_world_segment_wxyz": np.array(t[f"trajectory/{key}/{segment}/quat_world_segment_wxyz"], copy=True),
                    "mask": np.array(t[f"trajectory/{key}/{segment}/mask"], bool, copy=True),
                } for segment in SEGMENTS}
                sources[action] = PublicationActionSource(action, key, values["time_us"], values["derived_boot_epoch"], values["contiguous_span_id"], values["acc_mps2"], values["quat_world_sensor_wxyz"], traj, hashes)
        from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
        from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import frozen_world_alignment
        kin = load_frozen_c2_3a(); alignment, _ = frozen_world_alignment(kin)
        body_paths = [ROOT / "src/biospur_fusion/c2_uwb_calibration" / name for name in ("articulated_range.py", "antenna_los.py", "frozen_body_proxy.py")]
        body_owners = {str(p.relative_to(ROOT)): file_sha256(p) for p in body_paths}
        return cls(sources=sources, clocks={a: clock for a in sources}, alignment=alignment, geometry=kin.geometry,
                   geometry_owner_digest=kin.verification.manifest_sha256, frontend_sha256=EXPECTED[FRONTEND],
                   frontend_manifest_sha256=EXPECTED[FRONTEND_MANIFEST], trajectory_sha256=EXPECTED[TRAJECTORY],
                   clock_table_sha256=EXPECTED[CLOCK_TABLE], clock_source_sha256=clock_doc["source_sha256"],
                   publication_owner_sha256=file_sha256(Path(__file__)), body_rule_owners=body_owners,
                   frontend_manifest_schema=manifest["schema"],
                   frontend_reconstruction_role=manifest["frontend_reconstruction_role"])

    @property
    def revision(self): return self._revision

    def action_for_pelvis_event(self, record: TypedEvent) -> str:
        """Resolve source ownership from boot+TIMER2, never from an action label."""
        if (
            type(record) is not TypedEvent
            or record.record_type is not RecordType.IMU
            or record.node_id != PELVIS_NODE
        ):
            raise ValueError("tick lookup requires one decoded pelvis IMU")
        owner = self._tick_index.get((int(record.boot_epoch), int(record.node_timer_us)))
        if owner is None:
            raise ValueError("pelvis IMU tick has no acquired-pose owner")
        return owner[0]

    def publication_for_pelvis_event(
        self, record: TypedEvent, *, availability_global_ns: int,
    ):
        """Publish by source clock identity without consulting action metadata."""
        return self.publication_for_selected_pelvis_event(
            record,
            action_id=self.action_for_pelvis_event(record),
            availability_global_ns=availability_global_ns,
        )

    def _prepare_pelvis_record_rows(
        self, records: tuple[TypedEvent, ...],
    ) -> tuple[_PreparedPoseRow, ...] | None:
        """Prepare geometry only when one raw record is structurally homogeneous."""
        if type(records) is not tuple or not 1 <= len(records) <= 16:
            return None
        if not all(hasattr(self, name) for name in (
            "_tick_index", "_sources", "_alignment", "_geometry",
            "_revision", "_Native200PublicationProducer__pose_row_owner",
        )):
            return None
        resolved = []
        for record in records:
            if (
                type(record) is not TypedEvent
                or record.record_type is not RecordType.IMU
                or record.status is not EventStatus.DECODED
                or record.node_id != PELVIS_NODE
                or record.raw is None
            ):
                return None
            owner = self._tick_index.get((int(record.boot_epoch), int(record.node_timer_us)))
            if owner is None:
                return None
            action_id, frame = owner
            source = self._sources[action_id]
            if (
                frame < 1
                or source.span[frame] != source.span[frame - 1]
                or int(source.time_us[frame] - source.time_us[frame - 1]) != 5000
                or not all(bool(source.trajectory[s]["mask"][frame]) for s in SEGMENTS)
            ):
                return None
            resolved.append((record, action_id, int(frame), source))
        action_id = resolved[0][1]
        source = resolved[0][3]
        frames = tuple(row[2] for row in resolved)
        if (
            any(row[1] != action_id or row[3] is not source for row in resolved)
            or any(right != left + 1 for left, right in zip(frames, frames[1:]))
            or any(int(source.span[frame]) != int(source.span[frames[0]]) for frame in frames)
        ):
            return None
        try:
            geometry_rows = accepted_pose_frames(
                source.trajectory, frames, self._alignment, self._geometry,
            )
        except Exception:
            return None
        output = []
        for index, ((record, _, frame, _), geometry_row) in enumerate(
            zip(resolved, geometry_rows)
        ):
            raw = record.raw
            output.append(_PreparedPoseRow(
                self.__pose_row_owner,
                record,
                (
                    raw.record_index, raw.sample_index, raw.start_offset,
                    raw.end_offset, raw.encoded_sha256,
                ),
                source,
                action_id,
                frame,
                int(source.span[frame]),
                self._revision + index,
                _immutable_mapping(geometry_row[0]),
                _immutable_mapping(geometry_row[1]),
                _immutable_mapping(geometry_row[2]),
            ))
        return tuple(output)

    def _publication_for_pelvis_event_with_row(
        self, record: TypedEvent, *, availability_global_ns: int,
        prepared_row: _PreparedPoseRow,
    ):
        return self.publication_for_selected_pelvis_event(
            record,
            action_id=self.action_for_pelvis_event(record),
            availability_global_ns=availability_global_ns,
            _prepared_pose_row=prepared_row,
        )

    def publication_for_selected_pelvis_event(
        self, record: TypedEvent, *, action_id: str, availability_global_ns: int,
        _prepared_pose_row: _PreparedPoseRow | None = None,
    ):
        if type(record) is not TypedEvent or record.record_type is not RecordType.IMU or record.status is not EventStatus.DECODED or record.node_id != PELVIS_NODE or record.raw is None:
            raise ValueError("producer requires decoded pelvis IMU raw identity")
        source = self._sources.get(action_id); clock = self._clocks.get(action_id)
        if source is None: raise ValueError("event action is not in the acquired calibration source")
        timer = int(record.node_timer_us)
        owner = self._tick_index.get((int(record.boot_epoch), timer))
        if owner is None or owner[0] != action_id:
            raise ValueError("event lacks exact uniquely owned frontend TIMER2 tick")
        frame = int(owner[1])
        if frame < 1 or source.span[frame] != source.span[frame-1] or int(source.time_us[frame]-source.time_us[frame-1]) != 5000: raise ValueError("event lacks consecutive same-span source frame")
        if int(record.boot_epoch) != int(source.boot[frame]) or int(record.boot_epoch) != clock.boot_epoch: raise ValueError("event boot/span differs from source owner")
        global_ns = clock.global_ns(timer)
        if record.global_time_ns is not None and int(record.global_time_ns) != global_ns: raise ValueError("event global time differs from clock owner")
        base = record.payload.get("base_timer2_us"); delta = record.payload.get("delta_us"); raw_acc = record.payload.get("acc_raw")
        if type(base) is not int or type(delta) is not int or base + delta != timer: raise ValueError("event TIMER2 base/delta mismatch")
        raw_acc = tuple(int(v) for v in raw_acc) if isinstance(raw_acc, (list, tuple)) and len(raw_acc) == 3 else ()
        if len(raw_acc) != 3 or any(v < -32768 or v > 32767 for v in raw_acc): raise ValueError("event raw acceleration identity invalid")
        if not all(bool(source.trajectory[s]["mask"][frame]) for s in SEGMENTS): raise ValueError("exact trajectory frame is masked")
        rank = tuple(ACTION_KEYS).index(action_id)
        if self._last_ns is not None and (global_ns <= self._last_ns or rank < self._rank): raise ValueError("publication chronology reversed")
        if _prepared_pose_row is None:
            rotations, points, normals = accepted_pose_frame(
                source.trajectory, frame, self._alignment, self._geometry,
            )
        else:
            raw = record.raw
            raw_identity = (
                raw.record_index, raw.sample_index, raw.start_offset,
                raw.end_offset, raw.encoded_sha256,
            )
            if (
                type(_prepared_pose_row) is not _PreparedPoseRow
                or _prepared_pose_row.owner is not self.__pose_row_owner
                or _prepared_pose_row.record is not record
                or _prepared_pose_row.raw_identity != raw_identity
                or _prepared_pose_row.source_owner is not source
                or _prepared_pose_row.action_id != action_id
                or _prepared_pose_row.frame != frame
                or _prepared_pose_row.span != int(source.span[frame])
                or _prepared_pose_row.expected_revision != self._revision
            ):
                raise RuntimeError("foreign, replayed, or stale prepared pose row")
            rotations = _prepared_pose_row.rotations
            points = _prepared_pose_row.points
            normals = _prepared_pose_row.normals
        _, previous_points, _ = accepted_pose_frame(source.trajectory, frame-1, self._alignment, self._geometry)
        previous_ns = clock.global_ns(int(source.time_us[frame-1])); dt = (global_ns-previous_ns)*1e-9
        offsets = {node: points[name] for node, name in NODE_TO_PROXY_POINT.items()}
        velocities = {node: (offsets[node]-previous_points[NODE_TO_PROXY_POINT[node]])/dt for node in offsets}
        sensor_rotation = self._alignment @ rotation_from_wxyz(source.sensor_quat_wxyz[frame])
        imu = ImuSample(global_ns*1e-9, int(availability_global_ns)*1e-9, source.calibrated_acc_mps2[frame], sensor_rotation, int(record.sequence))
        raw = record.raw; event_id = f"v47:{raw.record_index}:{raw.sample_index}:{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}"
        result = AuthoritativeNative200PosePublication(event_id, PELVIS_NODE, clock.boot_epoch, base, timer, global_ns, int(availability_global_ns), "B306_TIMER2", clock.digest, clock.clock_owner_sha256, self.clock_source_sha256, self._revision, frame, action_id, raw, raw_acc, imu, rotations, offsets, velocities, normals, points, {}, self.frontend_sha256, self.publication_owner_sha256, self.base_pose_owner_digest, self.body_rule_digest, CONTACT_NOT_APPLIED_OWNER, "CALIBRATED_FRONTEND_MEASUREMENT;RAW_EVENT_IDENTITY_SEPARATE;CONTACT_NOT_APPLIED")
        self._revision += 1; self._last_ns = global_ns; self._rank = rank
        return result
