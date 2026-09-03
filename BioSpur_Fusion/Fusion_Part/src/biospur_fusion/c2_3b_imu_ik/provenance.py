"""Fail-closed, orientation-only provenance and runtime loading for C2 3B."""

from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from .contracts import (
    ALL_EPISODES,
    EDGE_ROWS,
    HOLDOUT_EPISODES,
    PRIMARY_EPISODES,
    SEGMENTS,
    AxisPair,
    DisplayGeometry,
    EpisodeData,
    WORKSPACE,
)


class ProvenanceError(RuntimeError):
    """An approved input/runtime contract was not satisfied."""


PRIMARY_TRAJECTORY = Path(
    "logs/c2_pose_reset_qmt_avatar_v16_20260831_215300/POSE_RESET_QMT_TRAJECTORY.npz"
)
AXIS_REPORT = Path(
    "logs/c2_pose_reset_qmt_avatar_v16_20260831_215300/POSE_RESET_QMT_DIAGNOSTIC.json"
)
CALIBRATION = Path(
    "logs/c2_pose_reset_qmt_avatar_v16_20260831_215300/FROZEN_C2_AVATAR_REPLAY_CALIBRATION.npz"
)
HOLDOUT_TRAJECTORY = Path(
    "logs/c2_hxx_frozen_replay_20260831_220900/HXX_FROZEN_C2_REPLAY_TRAJECTORY.npz"
)
HOLDOUT_REPORT = Path(
    "logs/c2_hxx_frozen_replay_20260831_220900/HXX_FROZEN_C2_REPLAY_REPORT.json"
)
BASE_CONFIG = Path("config/c2_coupled_progressive_v1/config.json")
AMENDMENT = Path("config/c2_coupled_progressive_v1/AMENDMENT_001_REMOVE_TORSO_SCALAR.json")

SAFE_BINDINGS = MappingProxyType(
    {
        Path("logs/c2_imu_19plus2_formal_freeze_20260901_082039/FORMAL_FREEZE_SEAL.json"): "f41317208851eb3b0037b1463dd45aa3935d258161756f9549203603ef885534",
        Path("logs/c2_imu_19plus2_formal_freeze_20260901_082039/FORMAL_FREEZE_MANIFEST.json"): "e4edfa682daa6c3002212d8c3a8e0992e0c4cb562938434d4d2f75ad43f87acb",
        PRIMARY_TRAJECTORY: "0f3ce2f9765508829de66d6681d17b433af6b54167c83619d9ddaffa13fbccdd",
        AXIS_REPORT: "0e1d1dac79efebd8ddbcc928d5aa3ba56e52bfec4a040bb3301176c4b84b9aaa",
        CALIBRATION: "ddc25eef63dce56065478dc331d667f3ec85502193c6e11f3cee1e83abd2431d",
        HOLDOUT_TRAJECTORY: "da0855cb3b440cfbc565d60c4aedc0dbbe855fb3caff1e53ec7350c91929d639",
        HOLDOUT_REPORT: "196e6e50a19c13290652ddae64ed8cfa8b9bed00093f0415910a33b314ec0cdc",
        BASE_CONFIG: "d675c963a75a4e5b9dd103278401ac2bc93d24244dba4ffc1988a04a96d343e3",
        AMENDMENT: "d7a0554321c77b59a3dac8e91bdaab4dc68f2e3c06ba3faeba529ae1834a8170",
        Path("src/biospur_fusion/c2_coupled_progressive/renderer.py"): "aa62192f0e4afe9c129331650def3993581f59d3d371f44329c51028973ef66f",
        Path("src/biospur_fusion/c2_coupled_progressive/contracts.py"): "8823bb6ec93829388c4e3f978eed17dafdb2d8ce50a92d5940ff0fe3094d9361",
        Path("src/biospur_fusion/c2_coupled_progressive/output_coordinates.py"): "49834491a1adb21156928c81c4ab51ee685134c4cf6f65c5c3cfc969dde1eeb5",
        Path("src/biospur_fusion/c2_3a_kinematics/interface.py"): "e5ef3b9832d1b25cdd30a64e3f310ffdc783e071fa5781aaf02cb348c8add45f",
    }
)

EXPECTED_INTERPRETER_SHA256 = "1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118"
EXPECTED_PYTHONPATH = (
    f"{WORKSPACE}/src:/home/zekaixiao/.local/lib/python3.12/site-packages"
)
EXPECTED_DISTRIBUTIONS = MappingProxyType(
    {
        "numpy": ("2.4.4", "f3a38cbef36a7b20346924ff77c35c1d61081626f7599870d8f7372ebf33801c"),
        "scipy": ("1.17.1", "cf88c01b64998c08fee7fc684ba0dcfa292a2df89efc009288ae3f2ffd549ce2"),
        "matplotlib": ("3.10.9", "df4a8a517405c6d1c2e74ff274f5043f9e44883c0f49592d71f20308aad2c6ef"),
        "contourpy": ("1.3.3", "ba168c0e5b1ffc80a56f7d1617a3e2bec1d9f17d21a3afdef01aaa17c64018c0"),
        "cycler": ("0.12.1", "0dd0246dcaff5aaa3e9bbb1d8fb35134b6d04659180f94e77491162f0e1f2ab3"),
        "fonttools": ("4.62.1", "5f887adeea1bcdc8886274d3c8c8aea1c3e3de866feb2f7de00393ed68d88f97"),
        "kiwisolver": ("1.5.0", "5d00f69a41ce6bb947fd6727b4ff69a4e30a2b52dd5666b1993ef7d78cdba634"),
        "packaging": ("26.2", "2db86fa1abcd7cddc9205020b3c7ecf114d279d367fe2782978b23e141837ecc"),
        "typing_extensions": ("4.15.0", "02f70a4ed6f81c3298a0024ca9dcc6807360938d388360ce3b768243f719cdce"),
    }
)
EXPECTED_MODULES = MappingProxyType(
    {
        "PIL": ("10.2.0", "/usr/lib/python3/dist-packages/PIL/__init__.py", "c499bf96823dc5c7dc9b5fec6bd97c9971fb9757fde7bdd09e1716fe7817f72f"),
        "pyparsing": ("3.1.1", "/usr/lib/python3/dist-packages/pyparsing/__init__.py", "01b8e571e157b953f24b3eed7418f253254c5b25bed2744d3fd01e798dc04aee"),
        "dateutil": ("2.8.2", "/usr/lib/python3/dist-packages/dateutil/__init__.py", "957125012ab0606c2a96b49649e5f5f49c05e417bdb4d79c0daf5e6e4fb48269"),
    }
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_identity_binding(stream_source_by_slot: Mapping[str, str]) -> bool:
    """Require every immutable segment slot to carry its own source identity."""

    return tuple(stream_source_by_slot) == SEGMENTS and all(
        stream_source_by_slot[segment] == segment for segment in SEGMENTS
    )


def verify_safe_bindings(workspace: Path = WORKSPACE) -> dict[str, str]:
    """Verify only the approved orientation/geometry projection."""

    workspace = workspace.resolve()
    if workspace != WORKSPACE.resolve():
        raise ProvenanceError(f"canonical workspace required: {workspace}")
    verified: dict[str, str] = {}
    for relative, expected in SAFE_BINDINGS.items():
        actual = sha256_file(workspace / relative)
        if actual != expected:
            raise ProvenanceError(f"safe input binding changed: {relative}")
        verified[str(relative)] = actual
    return verified


def _record_path(distribution: importlib.metadata.Distribution) -> Path:
    matches = [item for item in (distribution.files or ()) if str(item).endswith(".dist-info/RECORD")]
    if len(matches) != 1:
        raise ProvenanceError(f"one RECORD required for {distribution.metadata['Name']}")
    return Path(distribution.locate_file(matches[0])).resolve()


def _verify_record_files(distribution: importlib.metadata.Distribution) -> int:
    count = 0
    for item in distribution.files or ():
        if item.hash is None:
            continue
        if item.hash.mode != "sha256":
            raise ProvenanceError(f"unsupported RECORD hash: {item}")
        path = Path(distribution.locate_file(item)).resolve()
        expected = base64.urlsafe_b64decode(item.hash.value + "==").hex()
        if sha256_file(path) != expected:
            raise ProvenanceError(f"distribution file changed: {path}")
        count += 1
    if count == 0:
        raise ProvenanceError(f"no hashed files in RECORD: {distribution.metadata['Name']}")
    return count


def verify_runtime(*, full_records: bool = True) -> dict[str, Any]:
    if Path(sys.executable).resolve() != Path("/usr/bin/python3").resolve():
        raise ProvenanceError(f"wrong interpreter: {sys.executable}")
    if sys.version_info[:3] != (3, 12, 3):
        raise ProvenanceError(f"wrong interpreter version: {sys.version_info[:3]}")
    if sha256_file(Path(sys.executable)) != EXPECTED_INTERPRETER_SHA256:
        raise ProvenanceError("interpreter hash changed")
    if not sys.flags.no_user_site:
        raise ProvenanceError("Python user-site discovery is not disabled")
    if os.environ.get("PYTHONPATH") != EXPECTED_PYTHONPATH:
        raise ProvenanceError("PYTHONPATH does not match the approved owner")
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        if os.environ.get(name) != "1":
            raise ProvenanceError(f"{name}=1 required")

    verified: dict[str, Any] = {"interpreter": EXPECTED_INTERPRETER_SHA256, "distributions": {}}
    for name, (version, expected_record) in EXPECTED_DISTRIBUTIONS.items():
        distribution = importlib.metadata.distribution(name)
        if distribution.version != version:
            raise ProvenanceError(f"distribution version changed: {name}")
        record = _record_path(distribution)
        if sha256_file(record) != expected_record:
            raise ProvenanceError(f"RECORD changed: {name}")
        count = _verify_record_files(distribution) if full_records else 0
        verified["distributions"][name] = {
            "version": version,
            "record": str(record),
            "record_sha256": expected_record,
            "hashed_files_verified": count,
        }
    for module_name, (version, expected_path, expected_hash) in EXPECTED_MODULES.items():
        module = importlib.import_module(module_name)
        path = Path(module.__file__).resolve()
        actual_version = getattr(module, "__version__", None)
        if actual_version != version or str(path) != expected_path or sha256_file(path) != expected_hash:
            raise ProvenanceError(f"rendering module changed: {module_name}")
        verified["distributions"][module_name] = {
            "version": version,
            "module": str(path),
            "module_sha256": expected_hash,
        }
    return verified


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProvenanceError(f"JSON object required: {path}")
    return value


def load_axes(workspace: Path = WORKSPACE) -> Mapping[str, AxisPair]:
    report = _json(workspace / AXIS_REPORT)
    rows = report.get("qmt_olsson_hinge_axes")
    if not isinstance(rows, dict) or set(rows) != {"elbow_left", "elbow_right", "knee_left", "knee_right"}:
        raise ProvenanceError("sealed four-axis registry changed")
    edge_by_name = {name: (parent, child) for name, parent, child in EDGE_ROWS}
    result: dict[str, AxisPair] = {}
    for name in ("elbow_left", "elbow_right", "knee_left", "knee_right"):
        row = rows[name]
        if row.get("primitive") != "qmt.jointAxisEstHingeOlsson_unmodified":
            raise ProvenanceError(f"axis primitive changed: {name}")
        parent_axis = np.asarray(row.get("parent_axis_reset_segment"), dtype=np.float64)
        child_axis = np.asarray(row.get("child_axis_reset_segment"), dtype=np.float64)
        if (
            parent_axis.shape != (3,)
            or child_axis.shape != (3,)
            or not np.all(np.isfinite(parent_axis))
            or not np.all(np.isfinite(child_axis))
            or not np.isclose(np.linalg.norm(parent_axis), 1.0, rtol=0.0, atol=1e-12)
            or not np.isclose(np.linalg.norm(child_axis), 1.0, rtol=0.0, atol=1e-12)
        ):
            raise ProvenanceError(f"axis values changed: {name}")
        parent_axis.setflags(write=False)
        child_axis.setflags(write=False)
        parent, child = edge_by_name[name]
        result[name] = AxisPair(name, parent, child, parent_axis, child_axis)
    return MappingProxyType(result)


def _load_archive(path: Path, episode_keys: tuple[str, ...]) -> tuple[dict[str, EpisodeData], np.ndarray]:
    required = {
        f"trajectory/{episode}/{segment}/{field}"
        for episode in episode_keys
        for segment in SEGMENTS
        for field in ("time_root_s", "quat_world_segment_wxyz", "mask")
    }
    required.update(
        {
            "output_coordinates/matrix_world_output_from_internal",
            "output_coordinates/plane_normal_world_internal",
        }
    )
    result: dict[str, EpisodeData] = {}
    with np.load(path, allow_pickle=False) as archive:
        if set(archive.files) != required:
            raise ProvenanceError(f"orientation archive field set changed: {path}")
        output = np.asarray(
            archive["output_coordinates/matrix_world_output_from_internal"],
            dtype=np.float64,
        )
        for episode in episode_keys:
            time_arrays = [
                np.asarray(archive[f"trajectory/{episode}/{segment}/time_root_s"], dtype=np.float64)
                for segment in SEGMENTS
            ]
            if any(not np.array_equal(time_arrays[0], other) for other in time_arrays[1:]):
                raise ProvenanceError(f"segment time arrays differ: {episode}")
            time_s = time_arrays[0].copy()
            if time_s.ndim != 1 or not np.all(np.isfinite(time_s)) or np.any(np.diff(time_s) <= 0.0):
                raise ProvenanceError(f"invalid episode time: {episode}")
            quaternions = np.stack(
                [
                    np.asarray(
                        archive[f"trajectory/{episode}/{segment}/quat_world_segment_wxyz"],
                        dtype=np.float64,
                    )
                    for segment in SEGMENTS
                ],
                axis=1,
            )
            masks = np.stack(
                [
                    np.asarray(archive[f"trajectory/{episode}/{segment}/mask"], dtype=bool)
                    for segment in SEGMENTS
                ],
                axis=1,
            )
            if quaternions.shape != (len(time_s), len(SEGMENTS), 4) or masks.shape != (len(time_s), len(SEGMENTS)):
                raise ProvenanceError(f"invalid episode shapes: {episode}")
            if not np.all(np.isfinite(quaternions)) or not np.allclose(
                np.linalg.norm(quaternions, axis=2), 1.0, rtol=0.0, atol=1e-12
            ):
                raise ProvenanceError(f"invalid episode quaternions: {episode}")
            xyzw = quaternions[..., [1, 2, 3, 0]].reshape(-1, 4)
            matrices = Rotation.from_quat(xyzw).as_matrix().reshape(len(time_s), len(SEGMENTS), 3, 3)
            full = np.logical_and.reduce(masks, axis=1)
            for array in (time_s, matrices, masks, full):
                array.setflags(write=False)
            result[episode] = EpisodeData(episode, time_s, matrices, masks, full)
    if (
        output.shape != (3, 3)
        or not np.allclose(output.T @ output, np.eye(3), rtol=0.0, atol=1e-12)
        or not np.isclose(np.linalg.det(output), -1.0, rtol=0.0, atol=1e-12)
    ):
        raise ProvenanceError("display reflection changed")
    output.setflags(write=False)
    return result, output


def load_episodes(workspace: Path = WORKSPACE) -> tuple[Mapping[str, EpisodeData], np.ndarray]:
    primary, output_a = _load_archive(workspace / PRIMARY_TRAJECTORY, PRIMARY_EPISODES)
    holdouts, output_h = _load_archive(workspace / HOLDOUT_TRAJECTORY, HOLDOUT_EPISODES)
    if not np.array_equal(output_a, output_h):
        raise ProvenanceError("primary/holdout display convention differs")
    primary.update(holdouts)
    if tuple(primary) != ALL_EPISODES:
        raise ProvenanceError("19+2 episode registry changed")
    return MappingProxyType(primary), output_a


def load_display_geometries(workspace: Path = WORKSPACE) -> tuple[DisplayGeometry, ...]:
    base = _json(workspace / BASE_CONFIG)
    amendment = _json(workspace / AMENDMENT)
    changes = {
        row.get("path"): row.get("value")
        for row in amendment.get("effective_changes", ())
        if isinstance(row, dict)
    }
    torso = changes.get("proxy_geometry.torso_display_geometry")
    if not isinstance(torso, dict) or torso.get("models_are_anatomical_estimates") is not False:
        raise ProvenanceError("display torso proxy contract changed")
    proxy = base.get("proxy_geometry")
    if not isinstance(proxy, dict):
        raise ProvenanceError("display proxy geometry missing")
    lengths = {
        segment: float(proxy[f"{segment}_m"]["nominal"])
        for segment in (
            "upper_arm_left",
            "forearm_left",
            "upper_arm_right",
            "forearm_right",
            "thigh_left",
            "shank_left",
            "thigh_right",
            "shank_right",
        )
    }
    torso_values = tuple(float(value) for value in torso.get("models_m", ()))
    if torso_values != (0.36, 0.425, 0.49):
        raise ProvenanceError("three torso display proxies changed")
    shoulder = float(proxy["acromion_proxy_span_m"]["nominal"])
    models = tuple(
        DisplayGeometry(name, height, hip, shoulder, MappingProxyType(dict(lengths)))
        for name, height, hip in zip(
            ("short_narrow", "middle_proxy", "tall_wide"),
            torso_values,
            (0.18, 0.23, 0.28),
        )
    )
    return models
