from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

SCHEMA_VERSION = "biospur-phase3r23-common-heading-v1"
THREAD_ENV = (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
)


def force_single_thread_blas() -> None:
    for name in THREAD_ENV:
        os.environ[name] = "1"


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes(json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n")
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_payload(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def stable_seed(master_seed: int, task_id: str) -> int:
    payload = str(master_seed).encode() + b"\0" + task_id.encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)


def dynamic_offset(master_seed: int, action_id: str) -> int:
    # The protocol text specifies SHA256("20260819" || action_id) mod 3.
    payload = str(master_seed).encode() + action_id.encode()
    return int.from_bytes(hashlib.sha256(payload).digest(), "big") % 3


def normalize(v: np.ndarray, *, eps: float = 1e-12) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    if np.any(n < eps):
        raise ValueError("zero-length vector")
    return v / n


def quat_normalize(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    return normalize(q)


def quat_conjugate(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    out = q.copy()
    out[..., 1:] *= -1.0
    return out


def quat_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton product for scalar-first active quaternions."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    aw, ax, ay, az = np.moveaxis(a, -1, 0)
    bw, bx, by, bz = np.moveaxis(b, -1, 0)
    return np.stack((
        aw*bw-ax*bx-ay*by-az*bz,
        aw*bx+ax*bw+ay*bz-az*by,
        aw*by-ax*bz+ay*bw+az*bx,
        aw*bz+ax*by-ay*bx+az*bw,
    ), axis=-1)


def quat_rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rotate sensor-local vectors into the quaternion target frame.

    This implementation is independent of all BioSpur production quaternion
    helpers. q is scalar-first and represents the active source-to-target map.
    """
    q = quat_normalize(np.asarray(q, dtype=float))
    v = np.asarray(v, dtype=float)
    qv = q[..., 1:]
    qw = q[..., :1]
    return v + 2.0*np.cross(qv, np.cross(qv, v) + qw*v)


def rz(angle: float | np.ndarray) -> np.ndarray:
    angle = np.asarray(angle, dtype=float)
    c, s = np.cos(angle), np.sin(angle)
    out = np.zeros(angle.shape + (3, 3), dtype=float)
    out[..., 0, 0] = c
    out[..., 0, 1] = -s
    out[..., 1, 0] = s
    out[..., 1, 1] = c
    out[..., 2, 2] = 1.0
    return out


def wrap_pi(angle: float | np.ndarray) -> np.ndarray:
    return (np.asarray(angle, dtype=float) + np.pi) % (2*np.pi) - np.pi


def wrap_axis_line(angle: float | np.ndarray) -> np.ndarray:
    """Principal signed difference on RP1: range [-pi/2, pi/2)."""
    return (np.asarray(angle, dtype=float) + np.pi/2) % np.pi - np.pi/2


def horizontal_axis_angle(vectors: np.ndarray, *, minimum_horizontal: float = 0.15) -> tuple[float, float]:
    vectors = normalize(np.asarray(vectors, dtype=float))
    horizontal = vectors[:, :2]
    norms = np.linalg.norm(horizontal, axis=1)
    keep = norms >= minimum_horizontal
    if int(np.sum(keep)) < 3:
        raise ValueError("axis has insufficient horizontal projection")
    # Axis lines use doubled angles; this is antipodal invariant.
    angles = np.arctan2(horizontal[keep, 1], horizontal[keep, 0])
    moment = np.mean(np.exp(2j*angles))
    return float(0.5*np.angle(moment)), float(abs(moment))


def rp2_mean(axes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Antipodal line mean and tangent covariance from dyadic moments."""
    axes = normalize(np.asarray(axes, dtype=float))
    moment = np.einsum("ni,nj->ij", axes, axes)/len(axes)
    values, vectors = np.linalg.eigh(moment)
    mean = vectors[:, -1]
    pivot = int(np.argmax(np.abs(mean)))
    if mean[pivot] < 0:
        mean = -mean
    projector = np.eye(3)-np.outer(mean, mean)
    residuals = (axes@mean)[:, None]*axes
    residuals = residuals-(residuals@mean)[:, None]*mean
    covariance = projector @ np.cov(residuals.T, ddof=1) @ projector if len(axes) > 1 else projector*np.nan
    return mean, covariance


def gyro_world_axis(q_e_i: np.ndarray, gyro_i: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Return an RP2 motion-axis proposal in E from a complete block."""
    q_e_i = np.asarray(q_e_i, dtype=float)
    gyro_i = np.asarray(gyro_i, dtype=float)
    speed = np.linalg.norm(gyro_i, axis=1)
    if len(speed) < 20:
        raise ValueError("insufficient block samples")
    cutoff = max(float(np.quantile(speed, 0.55)), math.radians(8.0))
    keep = speed >= cutoff
    if int(np.sum(keep)) < 20:
        raise ValueError("insufficient dynamic samples")
    world = quat_rotate(q_e_i[keep], gyro_i[keep])
    weights = np.linalg.norm(world, axis=1)
    unit = normalize(world)
    moment = np.einsum("n,ni,nj->ij", weights, unit, unit)/np.sum(weights)
    values, vectors = np.linalg.eigh(moment)
    axis = vectors[:, -1]
    pivot = int(np.argmax(np.abs(axis)))
    if axis[pivot] < 0:
        axis = -axis
    angle, concentration = horizontal_axis_angle(np.tile(axis, (4, 1)))
    eigengap = float((values[-1]-values[-2])/max(values[-1], 1e-12))
    return axis, angle, min(concentration, eigengap)


def circular_axis_mean(values: Sequence[float]) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    z = np.mean(np.exp(2j*values))
    return float(0.5*np.angle(z)), float(abs(z))


def robust_axis_scale(values: Sequence[float], floor_rad: float) -> float:
    centre, _ = circular_axis_mean(values)
    residual = np.abs(wrap_axis_line(np.asarray(values)-centre))
    mad = float(np.median(np.abs(residual-np.median(residual))))
    return max(float(floor_rad), 1.4826*mad)


def information_rank(matrix: np.ndarray, tolerances: Iterable[float]) -> dict[str, object]:
    matrix = 0.5*(np.asarray(matrix, dtype=float)+np.asarray(matrix, dtype=float).T)
    values, vectors = np.linalg.eigh(matrix)
    values = np.maximum(values, 0.0)[::-1]
    vectors = vectors[:, ::-1]
    maximum = max(float(values[0]) if len(values) else 0.0, 1e-300)
    ranks = {format(float(t), ".0e"): int(np.sum(values > maximum*float(t))) for t in tolerances}
    null_vectors = vectors[:, values <= maximum*min(float(x) for x in tolerances)].T
    return {
        "dimension": int(matrix.shape[0]),
        "eigenvalues_descending": values.tolist(),
        "rank_by_relative_tolerance": ranks,
        "nullity_by_relative_tolerance": {key: int(matrix.shape[0]-value) for key, value in ranks.items()},
        "null_vectors_at_1e_8": null_vectors.tolist(),
        "matrix_sha256": hashlib.sha256(np.asarray(matrix, dtype="<f8").tobytes(order="C")).hexdigest(),
    }


def schur_profile(information: np.ndarray, keep: int) -> np.ndarray:
    """Schur/profile trailing nuisance variables using a Moore-Penrose inverse."""
    information = np.asarray(information, dtype=float)
    aa = information[:keep, :keep]
    ab = information[:keep, keep:]
    bb = information[keep:, keep:]
    if bb.size == 0:
        return aa.copy()
    return 0.5*((aa-ab@np.linalg.pinv(bb, rcond=1e-12)@ab.T)+(aa-ab@np.linalg.pinv(bb, rcond=1e-12)@ab.T).T)


def uid_string(node: str, boot: int, timer2_us: int, sequence: int, source_offset: int) -> str:
    return f"{node}:{int(boot)}:{int(timer2_us)}:{int(sequence)}:{int(source_offset)}"


def hash_ordered_strings(values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        encoded = value.encode()
        digest.update(len(encoded).to_bytes(4, "little"))
        digest.update(encoded)
    return digest.hexdigest()


def validate_exact_mapping(actual: Mapping[str, str], expected: Mapping[str, str]) -> None:
    if dict(actual) != dict(expected):
        raise RuntimeError(f"operator mapping mismatch: actual={dict(actual)!r}")
