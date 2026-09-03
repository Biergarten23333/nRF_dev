"""Label-blind D0B-R1 centerline replay.

The worker consumes a canonical calibration artifact and Q2/common-time
observations.  It deliberately has no action-window, truth, or PCA input.
Only centerline-observable products are emitted: segment longitudinal axes,
fixed-template graphical nodes, and capture-defined (not anatomical-zero)
functional coordinates.
"""
from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .d0b_r1_generator import SEGMENTS
from .d0b_r1_model import FUNCTIONAL_JOINTS, JOINTS, decode_product, yaw


NODE_NAMES = (
    "pelvis", "central", "shoulder_L", "shoulder_R", "elbow_L",
    "elbow_R", "wrist_L", "wrist_R", "hip_L", "hip_R", "knee_L",
    "knee_R", "ankle_L", "ankle_R",
)


def _unit_rows(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, float)
    return value / np.maximum(np.linalg.norm(value, axis=-1, keepdims=True), 1e-12)


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def calibration_payload(product_x: np.ndarray, contract_sha256: str) -> dict[str, Any]:
    values = np.asarray(product_x, float)
    if values.shape != (47,) or not np.isfinite(values).all():
        raise ValueError("R1 calibration product must be 47 finite coordinates")
    return {
        "schema": "biospur-d0b-r1-centerline-calibration-v1",
        "contract_sha256": str(contract_sha256),
        "product_coordinates": values.tolist(),
        "joint_zero": "CAPTURE_DEFINED_REPORTING_CONVENTION_NOT_ESTIMATED",
        "global_yaw": "PELVIS_DISPLAY_GAUGE_FIXED_TO_ZERO",
    }


def write_calibration(path: str | Path, product_x: np.ndarray, contract_sha256: str) -> str:
    path = Path(path)
    payload = calibration_payload(product_x, contract_sha256)
    encoded = _canonical_bytes(payload)
    path.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(digest + "\n", encoding="ascii")
    return digest


def load_calibration(path: str | Path) -> tuple[dict[str, Any], str]:
    path = Path(path)
    encoded = path.read_bytes()
    digest = hashlib.sha256(encoded).hexdigest()
    expected = path.with_suffix(path.suffix + ".sha256").read_text(encoding="ascii").strip()
    if digest != expected:
        raise ValueError("canonical calibration SHA-256 mismatch")
    payload = json.loads(encoded)
    if encoded != _canonical_bytes(payload):
        raise ValueError("calibration artifact is not canonical JSON")
    return payload, digest


def _corrected_kinematics(
    product: Mapping[str, Any], rotation: np.ndarray, gyro: np.ndarray,
    valid: np.ndarray, node_order: tuple[str, ...], node_to_segment: Mapping[str, str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    node_index = {node: index for index, node in enumerate(node_order)}
    segment_node = {segment: node for node, segment in node_to_segment.items()}
    count = len(rotation)
    directions = np.full((count, len(SEGMENTS), 3), np.nan)
    omega = np.full_like(directions, np.nan)
    segment_valid = np.zeros((count, len(SEGMENTS)), bool)
    for index, segment in enumerate(SEGMENTS):
        source = node_index[segment_node[segment]]
        corrected = np.einsum("ij,njk->nik", yaw(product["headings"][segment]), rotation[:, source])
        directions[:, index] = np.einsum("nij,j->ni", corrected, product["axes"][segment])
        omega[:, index] = np.einsum("nij,nj->ni", corrected, gyro[:, source])
        segment_valid[:, index] = valid[:, source] & np.isfinite(directions[:, index]).all(axis=1) & np.isfinite(omega[:, index]).all(axis=1)
    directions = _unit_rows(directions)
    return directions, omega, segment_valid


def _nodes_from_directions(directions: np.ndarray, valid: np.ndarray, lengths: Mapping[str, float]) -> tuple[np.ndarray, np.ndarray]:
    count = len(directions)
    output = np.full((count, len(NODE_NAMES), 3), np.nan)
    output_valid = np.zeros((count, len(NODE_NAMES)), bool)
    si = {name: index for index, name in enumerate(SEGMENTS)}
    ni = {name: index for index, name in enumerate(NODE_NAMES)}
    for row in range(count):
        if not valid[row, si["pelvis"]] or not valid[row, si["torso"]]:
            continue
        node = {"pelvis": np.zeros(3)}
        torso = directions[row, si["torso"]]
        node["central"] = float(lengths["torso"]) * torso
        # Deterministic display gauge. Near the exceptional parallel case the
        # second reference avoids a frame flip; neither choice claims heading.
        reference = np.array([0.0, 1.0, 0.0])
        lateral = np.cross(reference, torso)
        if np.linalg.norm(lateral) < 1e-6:
            lateral = np.cross(np.array([1.0, 0.0, 0.0]), torso)
        lateral = lateral / np.linalg.norm(lateral)
        node["shoulder_L"] = node["central"] + 0.5 * float(lengths["shoulder_width"]) * lateral
        node["shoulder_R"] = node["central"] - 0.5 * float(lengths["shoulder_width"]) * lateral
        node["hip_L"] = node["pelvis"] + 0.5 * float(lengths["hip_width"]) * lateral
        node["hip_R"] = node["pelvis"] - 0.5 * float(lengths["hip_width"]) * lateral
        row_ok = True
        for side in ("L", "R"):
            required = [f"upper_arm_{side}", f"forearm_{side}", f"thigh_{side}", f"shank_{side}"]
            if not all(valid[row, si[item]] for item in required):
                row_ok = False
                break
            node[f"elbow_{side}"] = node[f"shoulder_{side}"] + float(lengths[f"upper_arm_{side}"]) * directions[row, si[f"upper_arm_{side}"]]
            node[f"wrist_{side}"] = node[f"elbow_{side}"] + float(lengths[f"forearm_{side}"]) * directions[row, si[f"forearm_{side}"]]
            node[f"knee_{side}"] = node[f"hip_{side}"] + float(lengths[f"thigh_{side}"]) * directions[row, si[f"thigh_{side}"]]
            node[f"ankle_{side}"] = node[f"knee_{side}"] + float(lengths[f"shank_{side}"]) * directions[row, si[f"shank_{side}"]]
        if row_ok:
            for name in NODE_NAMES:
                output[row, ni[name]] = node[name]
                output_valid[row, ni[name]] = True
    return output, output_valid


def _integrate_coordinates(
    time_ns: np.ndarray, omega: np.ndarray, valid: np.ndarray,
    product: Mapping[str, Any], maximum_gap_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    count = len(time_ns)
    joint = np.zeros((count, len(FUNCTIONAL_JOINTS)))
    joint_valid = np.zeros_like(joint, dtype=bool)
    si = {name: index for index, name in enumerate(SEGMENTS)}
    for column, name in enumerate(FUNCTIONAL_JOINTS):
        parent, child = JOINTS[name]
        axis = np.asarray(product["functional"][name], float)
        for row in range(1, count):
            dt = (int(time_ns[row]) - int(time_ns[row - 1])) / 1e9
            ok = 0.0 < dt <= maximum_gap_s and valid[row - 1, si[parent]] and valid[row - 1, si[child]] and valid[row, si[parent]] and valid[row, si[child]]
            joint[row, column] = joint[row - 1, column]
            if ok:
                rel0 = omega[row - 1, si[child]] - omega[row - 1, si[parent]]
                rel1 = omega[row, si[child]] - omega[row, si[parent]]
                joint[row, column] += 0.5 * dt * float((rel0 + rel1) @ axis)
                joint_valid[row, column] = True
    trunk = np.zeros((count, 2))
    trunk_valid = np.zeros_like(trunk, dtype=bool)
    normal = np.asarray(product["trunk_normal"], float)
    seed = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.85 else np.array([0.0, 1.0, 0.0])
    first = seed - normal * float(seed @ normal); first /= np.linalg.norm(first)
    second = np.cross(normal, first); second /= np.linalg.norm(second)
    for row in range(1, count):
        dt = (int(time_ns[row]) - int(time_ns[row - 1])) / 1e9
        ok = 0.0 < dt <= maximum_gap_s and valid[row - 1, si["pelvis"]] and valid[row - 1, si["torso"]] and valid[row, si["pelvis"]] and valid[row, si["torso"]]
        trunk[row] = trunk[row - 1]
        if ok:
            rel0 = omega[row - 1, si["torso"]] - omega[row - 1, si["pelvis"]]
            rel1 = omega[row, si["torso"]] - omega[row, si["pelvis"]]
            average = 0.5 * (rel0 + rel1)
            trunk[row] += dt * np.array([average @ first, average @ second])
            trunk_valid[row] = True
    return joint, joint_valid, trunk, trunk_valid


def replay(
    payload: Mapping[str, Any], *, time_ns: np.ndarray, rotation: np.ndarray,
    gyro_rad_s: np.ndarray, valid: np.ndarray, node_order: tuple[str, ...],
    node_to_segment: Mapping[str, str], lengths: Mapping[str, float], maximum_gap_s: float,
) -> dict[str, np.ndarray]:
    """Run the physical forward model without labels or synthetic truth."""
    forbidden = {"action", "actions", "windows", "truth", "pca"} & set(payload)
    if forbidden:
        raise ValueError(f"label/truth input forbidden in replay: {sorted(forbidden)}")
    product = decode_product(np.asarray(payload["product_coordinates"], float))
    directions, omega, segment_valid = _corrected_kinematics(product, rotation, gyro_rad_s, valid, node_order, node_to_segment)
    nodes, node_valid = _nodes_from_directions(directions, segment_valid, lengths)
    joints, joint_valid, trunk, trunk_valid = _integrate_coordinates(time_ns, omega, segment_valid, product, maximum_gap_s)
    return {
        "time_ns": np.asarray(time_ns, np.int64),
        "segment_directions": directions,
        "segment_valid": segment_valid,
        "graphical_nodes": nodes,
        "node_valid": node_valid,
        "joint_coordinates": joints,
        "joint_coordinate_valid": joint_valid,
        "trunk_coordinates": trunk,
        "trunk_coordinate_valid": trunk_valid,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Fresh-process D0B-R1 label-blind replay worker")
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lengths", type=Path, required=True)
    parser.add_argument("--maximum-gap-s", type=float, required=True)
    args = parser.parse_args()
    payload, digest = load_calibration(args.calibration)
    with np.load(args.observations, allow_pickle=False) as data:
        node_order = tuple(str(item) for item in data["node_order"].tolist())
        segments = tuple(str(item) for item in data["segments"].tolist())
        if len(node_order) != len(segments):
            raise ValueError("observation node/segment mapping length mismatch")
        result = replay(
            payload,
            time_ns=data["time_ns"],
            rotation=data["rotation"],
            gyro_rad_s=data["gyro_rad_s"],
            valid=data["valid"].astype(bool),
            node_order=node_order,
            node_to_segment=dict(zip(node_order, segments)),
            lengths=json.loads(args.lengths.read_text()),
            maximum_gap_s=args.maximum_gap_s,
        )
    np.savez_compressed(args.output, calibration_sha256=np.asarray(digest), **result)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
