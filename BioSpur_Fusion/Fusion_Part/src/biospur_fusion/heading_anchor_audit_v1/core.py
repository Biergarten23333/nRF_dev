from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

THREAD_ENV = (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
)
CHAIN_CLASSES = {
    "DIRECTED_CHAIN_COMPLETE_BOUNDED",
    "DIRECTED_CHAIN_COMPLETE_UNBOUNDED",
    "AXIS_LINE_ONLY_PI_AMBIGUOUS",
    "VERTICAL_OR_POSITION_ONLY_NO_HEADING",
    "CONFLICTING_OR_REVISION_UNBOUND",
}


def force_single_thread_blas() -> None:
    for name in THREAD_ENV:
        os.environ[name] = "1"


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes(json.dumps(value, indent=2, sort_keys=True,
                               allow_nan=False).encode() + b"\n")
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_payload(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def wrap_pi(angle: float | np.ndarray) -> np.ndarray:
    return (np.asarray(angle, dtype=float) + np.pi) % (2 * np.pi) - np.pi


def wrap_line(angle: float | np.ndarray) -> np.ndarray:
    return (np.asarray(angle, dtype=float) + np.pi / 2) % np.pi - np.pi / 2


def directed_residual(observed: float, target: float) -> float:
    return float(wrap_pi(observed - target))


def line_residual(observed: float, target: float) -> float:
    return float(wrap_line(observed - target))


def gf2_rank(rows: Iterable[Sequence[int]], width: int) -> int:
    values = []
    for row in rows:
        if len(row) != width:
            raise ValueError("GF(2) row width mismatch")
        value = 0
        for index, bit in enumerate(row):
            if int(bit) not in (0, 1):
                raise ValueError("GF(2) rows must be binary")
            value |= int(bit) << index
        values.append(value)
    rank = 0
    for column in range(width):
        pivot = next((k for k in range(rank, len(values))
                      if (values[k] >> column) & 1), None)
        if pivot is None:
            continue
        values[rank], values[pivot] = values[pivot], values[rank]
        for k in range(len(values)):
            if k != rank and ((values[k] >> column) & 1):
                values[k] ^= values[rank]
        rank += 1
    return rank


def classify_pelvis_chain(authority: Mapping) -> str:
    """Fail-closed classification independent of any IMU numeric likelihood."""
    required = authority["required_links"]
    if any(row.get("revision_conflict", False) for row in required):
        result = "CONFLICTING_OR_REVISION_UNBOUND"
    elif any(not row.get("source_bound", False) for row in required):
        # Position-only pelvis placement and vertical short-edge statements are
        # weaker than a horizontal line: neither constrains yaw.
        result = "VERTICAL_OR_POSITION_ONLY_NO_HEADING"
    elif any(row.get("geometry") == "VERTICAL_OR_POSITION_ONLY" for row in required):
        result = "VERTICAL_OR_POSITION_ONLY_NO_HEADING"
    elif any(not row.get("directed_sign_bound", False) for row in required):
        result = "AXIS_LINE_ONLY_PI_AMBIGUOUS"
    elif not authority.get("uncertainty_bounded", False):
        result = "DIRECTED_CHAIN_COMPLETE_UNBOUNDED"
    elif float(authority["propagated_uncertainty_deg"]) > float(authority["gate_deg"]):
        result = "DIRECTED_CHAIN_COMPLETE_UNBOUNDED"
    else:
        result = "DIRECTED_CHAIN_COMPLETE_BOUNDED"
    if result not in CHAIN_CLASSES:
        raise AssertionError(result)
    return result


def information_rank(matrix: np.ndarray, tolerances: Sequence[float]) -> dict:
    matrix = np.asarray(matrix, dtype=float)
    matrix = 0.5 * (matrix + matrix.T)
    values = np.maximum(np.linalg.eigvalsh(matrix)[::-1], 0.0)
    maximum = max(float(values[0]) if len(values) else 0.0, 1e-300)
    ranks = {format(float(t), ".0e"): int(np.sum(values > maximum * float(t)))
             for t in tolerances}
    return {
        "dimension": int(matrix.shape[0]),
        "eigenvalues_descending": values.tolist(),
        "rank_by_relative_tolerance": ranks,
        "nullity_by_relative_tolerance": {
            key: int(matrix.shape[0] - rank) for key, rank in ranks.items()
        },
        "matrix_sha256": hashlib.sha256(
            np.asarray(matrix, dtype="<f8").tobytes(order="C")
        ).hexdigest(),
    }


def independent_rz(angle: float) -> np.ndarray:
    """Independent matrix oracle; no BioSpur quaternion helper is used."""
    c, s = math.cos(angle), math.sin(angle)
    return np.array(((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0)))
