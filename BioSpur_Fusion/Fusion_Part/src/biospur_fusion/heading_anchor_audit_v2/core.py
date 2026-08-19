from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np


COORDINATE_ORDER = (
    "torso", "upper_arm_left", "forearm_left", "upper_arm_right",
    "forearm_right", "thigh_left", "shank_left", "thigh_right",
    "shank_right",
)


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode()


def payload_sha(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes(json.dumps(value, indent=2, sort_keys=True,
                               allow_nan=False).encode() + b"\n")
    os.replace(tmp, path)


def wrap_2pi(angle: float | np.ndarray) -> np.ndarray:
    return (np.asarray(angle, dtype=float) + np.pi) % (2 * np.pi) - np.pi


def wrap_mod_pi(angle: float | np.ndarray) -> np.ndarray:
    return (np.asarray(angle, dtype=float) + np.pi / 2) % np.pi - np.pi / 2


def circular_mean(angle: np.ndarray) -> float:
    angle = np.asarray(angle, dtype=float)
    return float(math.atan2(float(np.mean(np.sin(angle))),
                            float(np.mean(np.cos(angle)))))


def circular_resultant(angle: np.ndarray) -> float:
    angle = np.asarray(angle, dtype=float)
    return float(np.hypot(np.mean(np.cos(angle)), np.mean(np.sin(angle))))


def circular_dispersion_deg(angle: np.ndarray) -> float:
    r = max(circular_resultant(angle), 1e-300)
    return math.degrees(math.sqrt(max(0.0, -2.0 * math.log(r))))


def rz(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array(((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0)))


def quat_to_matrix_wxyz(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    if q.shape[-1] != 4:
        raise ValueError("quaternion must be scalar-first wxyz")
    norm = np.linalg.norm(q, axis=-1, keepdims=True)
    if np.any(norm <= 0) or not np.all(np.isfinite(norm)):
        raise ValueError("invalid quaternion")
    w, x, y, z = np.moveaxis(q / norm, -1, 0)
    result = np.empty(q.shape[:-1] + (3, 3), dtype=float)
    result[..., 0, 0] = 1 - 2 * (y*y + z*z)
    result[..., 0, 1] = 2 * (x*y - z*w)
    result[..., 0, 2] = 2 * (x*z + y*w)
    result[..., 1, 0] = 2 * (x*y + z*w)
    result[..., 1, 1] = 1 - 2 * (x*x + z*z)
    result[..., 1, 2] = 2 * (y*z - x*w)
    result[..., 2, 0] = 2 * (x*z - y*w)
    result[..., 2, 1] = 2 * (y*z + x*w)
    result[..., 2, 2] = 1 - 2 * (x*x + y*y)
    return result


def rotate_active_wxyz(q: np.ndarray, vector: Sequence[float]) -> np.ndarray:
    return np.einsum("...ij,j->...i", quat_to_matrix_wxyz(q),
                     np.asarray(vector, dtype=float))


def reference_axis(q: np.ndarray, *, axis_sign: int = -1,
                   convention: str = "active_wxyz") -> np.ndarray:
    values = np.asarray(q, dtype=float)
    vector = np.array((0.0, 0.0, float(axis_sign)))
    if convention == "active_wxyz":
        return rotate_active_wxyz(values, vector)
    if convention in {"passive", "inverse", "transpose"}:
        matrices = quat_to_matrix_wxyz(values)
        return np.einsum("...ji,j->...i", matrices, vector)
    if convention == "xyzw_as_wxyz":
        mutated = values[..., (3, 0, 1, 2)]
        return rotate_active_wxyz(mutated, vector)
    raise ValueError(f"unknown convention {convention}")


def directed_residual(h_i: float, axis_yaw: float, psi_gp: float,
                      target_yaw_p: float, *, wrap: str = "2pi") -> float:
    raw = h_i + axis_yaw - psi_gp - target_yaw_p
    if wrap == "2pi":
        return float(wrap_2pi(raw))
    if wrap == "mod_pi":
        return float(wrap_mod_pi(raw))
    raise ValueError(wrap)


def pelvis_protocol_gauge(axis_yaw: np.ndarray, target_yaw_p: float = 0.0,
                          *, sign: int = 1) -> float:
    """Production frame contract: psi_GP = +yaw(a_pelvis)-yaw(d_pelvis)."""
    return float(wrap_2pi(sign * circular_mean(np.asarray(axis_yaw)) - target_yaw_p))


def point_distances(delta: float) -> tuple[float, float, float]:
    primary = abs(float(wrap_2pi(delta)))
    antipodal = abs(float(wrap_2pi(delta - np.pi)))
    return primary, antipodal, antipodal - primary


def _ccw_in_arc(angle: float, start: float, stop: float,
                tolerance: float = 1e-14) -> bool:
    span = float((stop - start) % (2*np.pi))
    position = float((angle - start) % (2*np.pi))
    return position <= span + tolerance


def circular_sector_distance(angle: float, start: float, stop: float) -> float:
    """Distance to the closed counter-clockwise circular arc start -> stop."""
    angle, start, stop = map(lambda x: float(wrap_2pi(x)), (angle, start, stop))
    if _ccw_in_arc(angle, start, stop):
        return 0.0
    return min(abs(float(wrap_2pi(angle-start))),
               abs(float(wrap_2pi(angle-stop))))


def sector_distances(angle: float, start: float, stop: float) -> tuple[float, float, float]:
    primary = circular_sector_distance(angle, start, stop)
    antipodal = circular_sector_distance(angle, start+np.pi, stop+np.pi)
    return primary, antipodal, antipodal-primary


def gf2_rank(rows: Iterable[Sequence[int]], width: int) -> int:
    packed: list[int] = []
    for row in rows:
        if len(row) != width:
            raise ValueError("GF(2) width mismatch")
        value = 0
        for index, bit in enumerate(row):
            if int(bit) not in (0, 1):
                raise ValueError("nonbinary GF(2) row")
            value |= int(bit) << index
        packed.append(value)
    rank = 0
    for column in range(width):
        pivot = next((i for i in range(rank, len(packed))
                      if (packed[i] >> column) & 1), None)
        if pivot is None:
            continue
        packed[rank], packed[pivot] = packed[pivot], packed[rank]
        for i in range(len(packed)):
            if i != rank and ((packed[i] >> column) & 1):
                packed[i] ^= packed[rank]
        rank += 1
    return rank


def factor_coordinate(edge: Mapping) -> list[str]:
    endpoints = list(edge["endpoints"])
    return [x for x in endpoints if x != "psi_GP"]


def production_reduced_factor_residual(edge: Mapping,
                                       headings: Mapping[str, float],
                                       psi_gp: float,
                                       measurement: float = 0.0) -> float:
    """R2.3 production factor geometry, evaluated through its modulo-pi path."""
    endpoints = list(edge["endpoints"])
    kind = edge["factor_type"]
    if kind == "PROTOCOL_AXIS_LINE":
        segment = next(x for x in endpoints if x != "psi_GP")
        raw = headings[segment] - psi_gp - measurement
    elif kind == "HINGE_RP2_RELATION":
        if len(endpoints) != 2:
            raise ValueError("hinge endpoint count")
        raw = headings[endpoints[0]] - headings[endpoints[1]] - measurement
    else:
        raise ValueError(f"unsupported production factor {kind}")
    return float(wrap_mod_pi(raw))


def evaluate_reduced_graph(edges: Sequence[Mapping], headings: Mapping[str, float],
                           psi_gp: float = 0.0) -> np.ndarray:
    return np.asarray([production_reduced_factor_residual(edge, headings, psi_gp)
                       for edge in edges], dtype=float)


def directed_structural_rows(order: Sequence[str], include: Iterable[str]) -> list[list[int]]:
    """Unweighted Jacobian rows in [h_1..h_9, psi_GP] coordinates."""
    include = list(include)
    rows: list[list[int]] = []
    for segment in include:
        row = [0] * (len(order)+1)
        if segment != "pelvis":
            row[list(order).index(segment)] = 1
        row[-1] = -1
        rows.append(row)
    return rows


def matrix_rank(matrix: np.ndarray, tolerance: float = 1e-12) -> int:
    return int(np.linalg.matrix_rank(np.asarray(matrix, dtype=float), tol=tolerance))


def canonical_result_payload(result: Mapping) -> dict:
    """Remove execution metadata from a formal replay before hashing."""
    excluded = {"execution", "start_utc", "end_utc", "wall_seconds",
                "invocation", "stdout_log", "stderr_log"}
    return {k: v for k, v in result.items() if k not in excluded}
