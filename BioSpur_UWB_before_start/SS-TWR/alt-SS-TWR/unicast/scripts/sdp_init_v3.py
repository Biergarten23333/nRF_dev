#!/usr/bin/env python3
from __future__ import annotations

"""
SDP-like initialization for anchor layout.

If cvxpy is available, we solve the convex Gram-matrix relaxation (SCS solver).
If not, we fall back to classical MDS from a complete distance matrix.

This script provides a CLI to generate a seed layout JSON in the same
format used by other solvers in this repo.
"""

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


ANCHORS = tuple("ABCDEFGH")


def load_inter_anchor_distances_m(path: Path) -> dict[tuple[str, str], float]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    units = (raw.get("units") or "mm").lower()
    scale = 0.001 if units == "mm" else 1.0
    dist_raw = raw.get("distances") or {}
    out: dict[tuple[str, str], float] = {}
    for k, v in dist_raw.items():
        if not isinstance(k, str) or "-" not in k:
            continue
        a, b = k.split("-", 1)
        a = a.strip().upper()
        b = b.strip().upper()
        if a not in ANCHORS or b not in ANCHORS or a == b:
            continue
        try:
            d = float(v) * scale
        except Exception:
            continue
        if not math.isfinite(d) or d <= 0.0:
            continue
        out[(a, b)] = d
        out[(b, a)] = d
    return out


def classical_mds(distances: dict[tuple[str, str], float]) -> np.ndarray:
    """
    Classical MDS (Torgerson) from full distance matrix.
    Returns: (n,3) coordinates centered at origin.
    """
    n = len(ANCHORS)
    D = np.zeros((n, n), dtype=float)
    idx = {a: i for i, a in enumerate(ANCHORS)}
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS:
            if a == b:
                continue
            d = distances.get((a, b))
            if d is None:
                raise ValueError(f"Missing distance for {a}-{b} (MDS requires full matrix)")
            D[i, idx[b]] = float(d)
    D2 = D * D
    J = np.eye(n) - np.ones((n, n)) / n
    B = -0.5 * (J @ D2 @ J)
    w, V = np.linalg.eigh(B)
    order = np.argsort(w)[::-1]
    w = w[order]
    V = V[:, order]
    w3 = np.maximum(w[:3], 0.0)
    X = V[:, :3] * np.sqrt(w3)
    X -= np.mean(X, axis=0)
    return X


def _rotation_matrix_align(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    u = u / (np.linalg.norm(u) + 1e-12)
    v = v / (np.linalg.norm(v) + 1e-12)
    c = float(np.dot(u, v))
    if c > 1.0:
        c = 1.0
    if c < -1.0:
        c = -1.0
    if 1.0 - c < 1e-10:
        return np.eye(3)
    axis = np.cross(u, v)
    s = float(np.linalg.norm(axis))
    if s < 1e-12:
        # 180-deg rotation around any orthogonal axis
        axis = np.cross(u, np.array([1.0, 0.0, 0.0]))
        if np.linalg.norm(axis) < 1e-12:
            axis = np.cross(u, np.array([0.0, 1.0, 0.0]))
        axis = axis / (np.linalg.norm(axis) + 1e-12)
        K = np.array(
            [[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]],
            dtype=float,
        )
        return np.eye(3) + 2 * (K @ K)
    axis = axis / s
    K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]], dtype=float)
    R = np.eye(3) + s * K + (1 - c) * (K @ K)
    return R


def gauge_fix(X: np.ndarray) -> np.ndarray:
    """
    Fix translation + rotation + reflection gauge:
    - A at origin
    - B on +x axis
    - C with positive y
    """
    X = X - X[0]  # A -> origin
    b = X[1].copy()
    nb = float(np.linalg.norm(b))
    if nb > 1e-9:
        R = _rotation_matrix_align(b, np.array([1.0, 0.0, 0.0]))
        X = X @ R.T
    if X[2, 1] < 0:
        X[:, 1] *= -1.0
    return X


def try_sdp_init(distances: dict[tuple[str, str], float]) -> np.ndarray | None:
    """
    Attempt cvxpy-based Gram relaxation. Returns None if cvxpy missing/fails.
    """
    try:
        import cvxpy as cp  # type: ignore
    except Exception:
        return None

    n = len(ANCHORS)
    idx = {a: i for i, a in enumerate(ANCHORS)}
    G = cp.Variable((n, n), symmetric=True)
    constraints = [G >> 0]
    obj_terms = []
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            d = distances[(a, b)]
            ii, jj = idx[a], idx[b]
            dist_sq_expr = G[ii, ii] - 2 * G[ii, jj] + G[jj, jj]
            obj_terms.append(cp.square(dist_sq_expr - (d * d)))
    prob = cp.Problem(cp.Minimize(cp.sum(obj_terms)), constraints)
    try:
        prob.solve(solver=cp.SCS, verbose=False, eps=1e-6, max_iters=20000)
    except Exception:
        return None
    if prob.status not in ("optimal", "optimal_inaccurate"):
        return None
    Gv = np.asarray(G.value, dtype=float)
    w, V = np.linalg.eigh(Gv)
    order = np.argsort(w)[::-1]
    w = w[order]
    V = V[:, order]
    w3 = np.maximum(w[:3], 0.0)
    X = V[:, :3] * np.sqrt(w3)
    X -= np.mean(X, axis=0)
    return X


def write_layout_json(path: Path, X_m: np.ndarray, *, source: dict[str, Any]) -> None:
    anchors = []
    for i, label in enumerate(ANCHORS):
        anchors.append(
            {
                "label": label,
                "x_mm": float(X_m[i, 0] * 1000.0),
                "y_mm": float(X_m[i, 1] * 1000.0),
                "z_mm": float(X_m[i, 2] * 1000.0),
            }
        )
    payload = {
        "units": "mm",
        "anchors": anchors,
        "source": source,
        "notes": [
            "Seed layout produced by SDP init if available; otherwise classical MDS fallback.",
            "Gauge-fixed: A at origin, B on +x, C with +y.",
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate an SDP/MDS seed layout from an inter-anchor matrix JSON.")
    ap.add_argument("--input", required=True, help="inter_anchor_matrix_*.json")
    ap.add_argument("--output", required=True, help="output layout json")
    ap.add_argument("--force-mds", action="store_true", help="skip cvxpy SDP and force MDS fallback")
    args = ap.parse_args()

    inp = Path(args.input)
    out = Path(args.output)
    distances = load_inter_anchor_distances_m(inp)

    X = None
    method = "mds"
    if not args.force_mds:
        X = try_sdp_init(distances)
        if X is not None:
            method = "sdp"
    if X is None:
        X = classical_mds(distances)
        method = "mds"
    X = gauge_fix(X)

    write_layout_json(out, X, source={"input": str(inp.resolve()), "method": method})
    print(f"[ok] wrote {out} method={method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

