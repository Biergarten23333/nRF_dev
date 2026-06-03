#!/usr/bin/env python3
from __future__ import annotations

"""
V3_full / V3_box / V3_free solver (practical implementation in this repo):

- Input: inter-anchor distances (mm) in the common "inter_anchor_matrix*.json" format
  (for V3 fusion, generate with scripts/fuse_bidirectional_matrix_v3.py).
- Optional: floating reference sessions (Tag115 CM extracted ranges.csv directories)
  to anchor the absolute scale/height.
- V3_full adds:
  - SDP/MDS seed (scripts/sdp_init_v3.py)
  - Antenna-delay (per-anchor additive bias) joint estimation
  - Tukey bisquare IRLS on inter-anchor residuals

Geometry modes in this file:
  - box: preserve approximate upper/lower paired-column structure
         (E-A, F-B, G-C, H-D height similarity; optional XY alignment).
  - free: only preserve a lower height band (A-D) and an upper height band
          (E-H), without enforcing paired columns.

Notes on "antenna delay" modeling:
  We model a per-anchor additive bias b_i in meters:
    d_meas_ij ~= ||x_i - x_j|| + b_i + b_j
  Under the V3 doc convention: correction is c/2*(tau_i + tau_j).
  So b_i corresponds to c/2 * tau_i => tau_i = 2*b_i/c.

This implementation does NOT implement the full V3 doc's time-varying Ref115
localization loop (because our Tag115 capture is currently aggregated). Instead,
each floating reference session contributes one unknown reference point p_k with
mean ranges per anchor, matching the existing repo solver format.
"""

import argparse
import csv
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

try:
    from scipy.optimize import least_squares  # type: ignore
except Exception:  # pragma: no cover
    least_squares = None


ANCHORS = tuple("ABCDEFGH")
C_LIGHT = 299_702_547.0  # m/s (same constant as docs)


def load_inter_anchor_matrix(path: Path) -> tuple[dict[tuple[str, str], float], dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    units = (raw.get("units") or "mm").lower()
    scale = 0.001 if units == "mm" else 1.0
    dist_raw = raw.get("distances") or {}
    distances: dict[tuple[str, str], float] = {}
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
        if not math.isfinite(d) or d <= 0:
            continue
        distances[(a, b)] = d
        distances[(b, a)] = d
    return distances, raw


def load_pair_weights(path: Path | None) -> dict[tuple[str, str], float]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    weights_raw = raw.get("weights", raw)
    out: dict[tuple[str, str], float] = {}
    for key, value in weights_raw.items():
        if not isinstance(key, str) or "-" not in key:
            continue
        a, b = key.split("-", 1)
        a = a.strip().upper()
        b = b.strip().upper()
        if a not in ANCHORS or b not in ANCHORS or a == b:
            continue
        try:
            w = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(w):
            continue
        w = max(1.0e-6, min(1.0, w))
        out[(a, b)] = w
        out[(b, a)] = w
    return out


def load_layout_coords_m(path: Path) -> dict[str, np.ndarray]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    anchors_raw = raw.get("anchors")
    units = (raw.get("units") or "mm").lower()
    scale = 0.001 if units == "mm" else 1.0
    out: dict[str, np.ndarray] = {}
    if isinstance(anchors_raw, dict):
        for k, v in anchors_raw.items():
            if k in ANCHORS:
                out[k] = np.array(v, dtype=float) * scale
        return out
    if isinstance(anchors_raw, list):
        for ent in anchors_raw:
            label = ent.get("label")
            if label not in ANCHORS:
                continue
            if "x_mm" in ent:
                out[label] = np.array([ent["x_mm"], ent["y_mm"], ent["z_mm"]], dtype=float) * 0.001
            else:
                out[label] = np.array([ent["x"], ent["y"], ent["z"]], dtype=float)
        return out
    raise ValueError(f"Unsupported layout: {path}")


def load_floating_reference_sessions(session_dirs: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in session_dirs:
        d = Path(s)
        p = d / "ranges.csv"
        if not p.exists():
            raise FileNotFoundError(f"Missing ranges.csv in {d}")
        by_anchor: dict[int, list[float]] = {}
        with p.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    anchor_id = int(row["anchor_id"])
                    mm = float(row["filt_mm"])
                    ok = int(row.get("ok", "1"))
                except Exception:
                    continue
                if anchor_id < 0 or anchor_id >= 8:
                    continue
                if ok != 1:
                    continue
                if not math.isfinite(mm) or mm <= 0:
                    continue
                by_anchor.setdefault(anchor_id, []).append(mm / 1000.0)
        means = {}
        for aid, vals in by_anchor.items():
            if not vals:
                continue
            arr = np.asarray(vals, dtype=float)
            med = float(np.median(arr))
            mad = float(np.median(np.abs(arr - med)))
            if mad > 0.0:
                # Keep a conservative inlier band around the median so a few
                # malformed CM rows cannot corrupt the floating-reference mean.
                band = 6.0 * 1.4826 * mad
                arr = arr[np.abs(arr - med) <= band]
            if arr.size == 0:
                arr = np.asarray([med], dtype=float)
            means[aid] = float(np.mean(arr))
        if not means:
            raise ValueError(f"Floating reference session {d} has no valid samples")

        # Initial guess: try summary.json from extract step, else heuristic.
        guess = np.array([1.8, 1.8, 0.7], dtype=float)
        summary = d / "summary.json"
        if summary.exists():
            try:
                r = json.loads(summary.read_text(encoding="utf-8"))
                mean = r.get("position_mean_mm")
                if mean:
                    guess = np.array([float(mean["x"]), float(mean["y"]), float(mean["z"])], dtype=float) / 1000.0
            except Exception:
                pass
        out.append(
            {
                "session_dir": str(d),
                "label": d.name,
                "range_means_m": means,
                "initial_guess_m": guess,
            }
        )
    return out


def tukey_weights(residuals: np.ndarray, c: float) -> np.ndarray:
    if c <= 0.0:
        return np.ones_like(residuals)
    r = residuals / c
    w = np.zeros_like(r)
    m = np.abs(r) < 1.0
    w[m] = (1.0 - r[m] ** 2) ** 2
    return w


def estimate_sigma_mad(residuals: np.ndarray) -> float:
    if residuals.size == 0:
        return 1.0
    med = float(np.median(residuals))
    mad = float(np.median(np.abs(residuals - med)))
    return max(1e-6, 1.4826 * mad)


def mean_centered_residuals(values: list[float], sigma: float) -> list[float]:
    """Return residuals of (v - mean(v)) / sigma for a list of scalars."""
    if sigma <= 0.0 or not values:
        return []
    m = float(np.mean(np.asarray(values, dtype=float)))
    return [(float(v) - m) / sigma for v in values]


@dataclass
class State:
    anchors: dict[str, np.ndarray]  # meters
    biases_m: dict[str, float]  # b_i in meters (per anchor)
    refs: list[np.ndarray]  # meters
    ref_biases_m: list[float]  # per floating ref session, meters


def pack_vars(st: State) -> np.ndarray:
    """
    Optimization variables: geometry only (anchors + floating ref points).

    Biases are solved in a separate closed-form step to keep the alternating
    loop stable (and to avoid inconsistency with regularization strength).
    """
    vec: list[float] = []
    # Gauge-fixed parameterization:
    # - A = (0,0,0)
    # - B = (Bx,0,0)
    # - D = (Dx,Dy,0)
    # This removes translation + 3-DOF rotation ambiguity, improving conditioning.
    B = st.anchors["B"]
    C = st.anchors["C"]
    D = st.anchors["D"]
    E = st.anchors["E"]
    F = st.anchors["F"]
    G = st.anchors["G"]
    H = st.anchors["H"]

    vec.append(float(B[0]))  # Bx
    vec.extend([float(C[0]), float(C[1]), float(C[2])])  # Cx,Cy,Cz
    vec.extend([float(D[0]), float(D[1])])  # Dx,Dy (Dz=0)
    for P in (E, F, G, H):
        vec.extend([float(P[0]), float(P[1]), float(P[2])])

    for p in st.refs:
        vec.extend([float(p[0]), float(p[1]), float(p[2])])
    return np.asarray(vec, dtype=float)


def unpack_vars(x: np.ndarray, n_refs: int, st_bias: dict[str, float], st_ref_bias: list[float]) -> State:
    idx = 0
    anchors: dict[str, np.ndarray] = {"A": np.array([0.0, 0.0, 0.0], dtype=float)}
    bx = float(x[idx])
    idx += 1
    anchors["B"] = np.array([bx, 0.0, 0.0], dtype=float)
    anchors["C"] = np.array([float(x[idx]), float(x[idx + 1]), float(x[idx + 2])], dtype=float)
    idx += 3
    anchors["D"] = np.array([float(x[idx]), float(x[idx + 1]), 0.0], dtype=float)
    idx += 2
    # E,F,G,H full xyz
    for a in ("E", "F", "G", "H"):
        anchors[a] = np.array([float(x[idx]), float(x[idx + 1]), float(x[idx + 2])], dtype=float)
        idx += 3
    refs: list[np.ndarray] = []
    for _ in range(n_refs):
        refs.append(np.array([x[idx], x[idx + 1], x[idx + 2]], dtype=float))
        idx += 3
    return State(anchors=anchors, biases_m=st_bias, refs=refs, ref_biases_m=st_ref_bias)


def build_residuals(
    x: np.ndarray,
    *,
    n_refs: int,
    biases_m: dict[str, float],
    ref_biases_m: list[float],
    distances: dict[tuple[str, str], float],
    ref_constraints: list[dict[str, Any]],
    sigma_dist_m: float,
    sigma_ref_m: float,
    z_prior_m: float | None,
    z_prior_sigma_m: float,
    w_edges: dict[tuple[str, str], float] | None,
    # Soft constraints (all optional; pass sigma<=0 to disable).
    geometry_mode: str,
    height_prior_m: float,
    height_sigma_m: float,
    lower_plane_sigma_m: float,
    upper_level_sigma_m: float,
    pair_height_sigma_m: float,
    vertical_xy_sigma_m: float,
    band_separation_prior_m: float | None,
    band_separation_sigma_m: float,
) -> np.ndarray:
    st = unpack_vars(x, n_refs, biases_m, ref_biases_m)
    res = []

    # Inter-anchor edges (unique i<j).
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            d = distances[(a, b)]
            pred = float(np.linalg.norm(st.anchors[a] - st.anchors[b]) + st.biases_m[a] + st.biases_m[b])
            r = (pred - d) / max(1e-9, sigma_dist_m)
            if w_edges is not None:
                r *= math.sqrt(max(0.0, float(w_edges.get((a, b), 1.0))))
            res.append(r)

    # Floating reference mean ranges.
    for k, c in enumerate(ref_constraints):
        p = st.refs[k]
        b_ref = ref_biases_m[k] if k < len(ref_biases_m) else 0.0
        for anchor_id, d in c["range_means_m"].items():
            a = ANCHORS[int(anchor_id)]
            pred = float(np.linalg.norm(st.anchors[a] - p) + st.biases_m[a] + b_ref)
            res.append((pred - float(d)) / max(1e-9, sigma_ref_m))
        if z_prior_m is not None and z_prior_sigma_m > 0.0:
            res.append((float(p[2]) - float(z_prior_m)) / float(z_prior_sigma_m))

    # -----------------------
    # Soft structural priors:
    # -----------------------
    # A/B/D define the gauge plane (z=0). C.z measures lower-cluster deviation
    # from that plane; in "free" mode this is interpreted as allowed lower-band
    # spread rather than strict coplanarity.
    if lower_plane_sigma_m > 0.0:
        res.append(float(st.anchors["C"][2]) / float(lower_plane_sigma_m))

    # Upper-band coherence: E/F/G/H z should remain a height-cluster even in
    # free mode; "box" just interprets this more structurally.
    upper_z = [float(st.anchors[a][2]) for a in ("E", "F", "G", "H")]
    res.extend(mean_centered_residuals(upper_z, upper_level_sigma_m))

    lower_z = [float(st.anchors[a][2]) for a in ("A", "B", "C", "D")]
    upper_mean = float(np.mean(np.asarray(upper_z)))
    lower_mean = float(np.mean(np.asarray(lower_z)))

    if geometry_mode == "box":
        # Box mode: the upper cluster has a soft absolute height prior, and
        # paired columns should have similar vertical translation.
        if height_sigma_m > 0.0:
            res.append((upper_mean - float(height_prior_m)) / float(height_sigma_m))

        pair_heights = [
            float(st.anchors["E"][2] - st.anchors["A"][2]),
            float(st.anchors["F"][2] - st.anchors["B"][2]),
            float(st.anchors["G"][2] - st.anchors["C"][2]),
            float(st.anchors["H"][2] - st.anchors["D"][2]),
        ]
        res.extend(mean_centered_residuals(pair_heights, pair_height_sigma_m))

        if vertical_xy_sigma_m > 0.0:
            pairs = [("A", "E"), ("B", "F"), ("C", "G"), ("D", "H")]
            for lo, up in pairs:
                dx = float(st.anchors[up][0] - st.anchors[lo][0])
                dy = float(st.anchors[up][1] - st.anchors[lo][1])
                res.append(dx / float(vertical_xy_sigma_m))
                res.append(dy / float(vertical_xy_sigma_m))
    elif geometry_mode == "free":
        # Free mode: no fixed column pairing. Keep only lower-band / upper-band
        # separation and upper-band compactness.
        if band_separation_prior_m is not None and band_separation_sigma_m > 0.0:
            res.append(((upper_mean - lower_mean) - float(band_separation_prior_m)) / float(band_separation_sigma_m))
        elif height_sigma_m > 0.0:
            # Fallback for legacy callers: treat height_prior_m as absolute upper
            # mean height when no explicit inter-band prior is supplied.
            res.append((upper_mean - float(height_prior_m)) / float(height_sigma_m))
    else:  # pragma: no cover
        raise ValueError(f"unsupported geometry_mode={geometry_mode}")

    return np.asarray(res, dtype=float)


def solve_biases_closed_form(
    anchors: dict[str, np.ndarray],
    refs: list[np.ndarray],
    distances: dict[tuple[str, str], float],
    ref_constraints: list[dict[str, Any]],
    *,
    mu: float,
) -> tuple[dict[str, float], list[float]]:
    """
    Solve biases b_i (anchors) and b_ref_k for floating refs in least squares:
      d_ij - ||xi-xj|| ~= b_i + b_j
      d_refi - ||p_k-xi|| ~= b_i + b_ref_k

    Gauge: b_A fixed to 0 by removing its column.
    Regularization: mu * ||b||^2.
    """
    # Unknowns: b_B..b_H (7) + b_ref_k (n_refs)
    n_refs = len(refs)
    n_unknown = 7 + n_refs
    cols = {a: (i - 1) for i, a in enumerate(ANCHORS) if a != "A"}  # B..H -> 0..6
    ref_cols = {k: 7 + k for k in range(n_refs)}

    rows = []
    rhs = []

    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            d = distances[(a, b)]
            geom = float(np.linalg.norm(anchors[a] - anchors[b]))
            y = float(d - geom)
            row = np.zeros((n_unknown,), dtype=float)
            if a != "A":
                row[cols[a]] = 1.0
            if b != "A":
                row[cols[b]] = 1.0
            rows.append(row)
            rhs.append(y)

    for k, c in enumerate(ref_constraints):
        p = refs[k]
        for anchor_id, d in c["range_means_m"].items():
            a = ANCHORS[int(anchor_id)]
            geom = float(np.linalg.norm(anchors[a] - p))
            y = float(d - geom)
            row = np.zeros((n_unknown,), dtype=float)
            if a != "A":
                row[cols[a]] = 1.0
            row[ref_cols[k]] = 1.0
            rows.append(row)
            rhs.append(y)

    A = np.vstack(rows) if rows else np.zeros((0, n_unknown), dtype=float)
    b = np.asarray(rhs, dtype=float) if rhs else np.zeros((0,), dtype=float)

    # Tikhonov regularization.
    if mu > 0.0:
        A = np.vstack([A, math.sqrt(mu) * np.eye(n_unknown)])
        b = np.concatenate([b, np.zeros((n_unknown,), dtype=float)])

    x, *_ = np.linalg.lstsq(A, b, rcond=None)

    biases_m = {"A": 0.0}
    for a in ANCHORS[1:]:
        biases_m[a] = float(x[cols[a]])
    ref_biases = [float(x[ref_cols[k]]) for k in range(n_refs)]
    return biases_m, ref_biases


def rms_mm_of_edges(anchors: dict[str, np.ndarray], biases_m: dict[str, float], distances: dict[tuple[str, str], float]) -> float:
    errs = []
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            d = distances[(a, b)]
            pred = float(np.linalg.norm(anchors[a] - anchors[b]) + biases_m[a] + biases_m[b])
            errs.append(pred - d)
    arr = np.asarray(errs, dtype=float)
    return float(np.sqrt(np.mean(arr * arr)) * 1000.0)

def edge_fit_stats_mm(
    anchors: dict[str, np.ndarray],
    biases_m: dict[str, float],
    distances: dict[tuple[str, str], float],
    w_edges: dict[tuple[str, str], float] | None,
    *,
    inlier_w_thresh: float = 0.2,
) -> dict[str, Any]:
    errs_mm: list[float] = []
    inlier_mm: list[float] = []
    outliers: list[dict[str, Any]] = []
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            d = distances[(a, b)]
            pred = float(np.linalg.norm(anchors[a] - anchors[b]) + biases_m[a] + biases_m[b])
            e_mm = (pred - d) * 1000.0
            errs_mm.append(e_mm)
            w = 1.0 if w_edges is None else float(w_edges.get((a, b), 1.0))
            if w >= inlier_w_thresh:
                inlier_mm.append(e_mm)
            else:
                outliers.append({"pair": f"{a}-{b}", "w": w, "err_mm": e_mm, "abs_err_mm": abs(e_mm)})
    def rms(vals: list[float]) -> float:
        arr = np.asarray(vals, dtype=float)
        return float(np.sqrt(np.mean(arr * arr))) if len(arr) else 0.0
    outliers.sort(key=lambda d: d["abs_err_mm"], reverse=True)
    return {
        "rms_all_mm": rms(errs_mm),
        "rms_inlier_mm": rms(inlier_mm),
        "inlier_count": len(inlier_mm),
        "outlier_count": len(outliers),
        "inlier_w_thresh": float(inlier_w_thresh),
        "top_outliers": outliers[:8],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Solve anchor layout with V3_full (seed + antenna bias + Tukey IRLS).")
    ap.add_argument("--input", required=True, help="inter_anchor_matrix_*.json (mm)")
    ap.add_argument("--output", required=True, help="output layout json")
    ap.add_argument("--seed-layout", default=None, help="optional seed layout json (mm/m)")
    ap.add_argument(
        "--geometry-mode",
        choices=("box", "free"),
        default="box",
        help="box: approximate paired upper/lower columns; free: only lower-band / upper-band separation.",
    )
    ap.add_argument("--floating-reference-session", action="append", default=[], help="Tag115 CM ranges session dir; may repeat")
    ap.add_argument("--floating-reference-z-prior-mm", type=float, default=None)
    ap.add_argument("--floating-reference-z-sigma-mm", type=float, default=80.0)
    ap.add_argument("--sigma-dist-mm", type=float, default=80.0, help="base sigma for inter-anchor distances")
    ap.add_argument("--sigma-ref-mm", type=float, default=70.0, help="sigma for floating reference mean ranges")
    # Soft constraints: keep defaults aligned with the repo's constrained solver.
    ap.add_argument("--height-prior-m", type=float, default=1.4, help="Soft prior mean height for upper plane (E/F/G/H).")
    ap.add_argument("--height-sigma-mm", type=float, default=300.0, help="1-sigma for the upper mean height prior.")
    ap.add_argument("--lower-plane-sigma-mm", type=float, default=80.0, help="1-sigma for C.z deviation from z=0.")
    ap.add_argument("--upper-level-sigma-mm", type=float, default=35.0, help="1-sigma for upper z spread around its mean.")
    ap.add_argument("--pair-height-sigma-mm", type=float, default=45.0, help="1-sigma for vertical-pair height spread around its mean.")
    ap.add_argument(
        "--vertical-xy-sigma-mm",
        type=float,
        default=0.0,
        help="Optional 1-sigma XY offset to softly encourage vertical-pair XY alignment. Set 0 to disable (recommended).",
    )
    ap.add_argument(
        "--band-separation-prior-mm",
        type=float,
        default=None,
        help="Free mode: expected mean height gap between upper band (E-H) and lower band (A-D).",
    )
    ap.add_argument(
        "--band-separation-sigma-mm",
        type=float,
        default=250.0,
        help="Free mode: 1-sigma for upper/lower band mean-height separation.",
    )
    ap.add_argument("--max-iters", type=int, default=15)
    ap.add_argument("--tukey-c-mult", type=float, default=4.685, help="Tukey c = mult * sigma(MAD)")
    ap.add_argument(
        "--tukey-c-min-mm",
        type=float,
        default=120.0,
        help="Lower bound for Tukey c in mm (prevents weight collapse to 0 on many edges).",
    )
    ap.add_argument(
        "--tukey-w-min",
        type=float,
        default=0.05,
        help="Clamp Tukey edge weights to at least this value (prevents edges being fully ignored).",
    )
    ap.add_argument(
        "--cir-pair-weights",
        default=None,
        help="Optional CIR-derived pair-weight JSON. Combined multiplicatively with Tukey edge weights.",
    )
    ap.add_argument(
        "--bias-sigma-mm",
        type=float,
        default=80.0,
        help="Bias (antenna-delay equivalent) 1-sigma prior in mm. Converted to Tikhonov mu=1/sigma^2.",
    )
    ap.add_argument(
        "--bias-mu",
        type=float,
        default=None,
        help="Override Tikhonov mu for biases (1/m^2). If set, --bias-sigma-mm is ignored.",
    )
    ap.add_argument("--freeze-bias", action="store_true", help="do not update biases (debug)")
    ap.add_argument("--verbose", type=int, default=1, help="0 quiet, 1 per-iter, 2 debug")
    args = ap.parse_args()

    if least_squares is None:
        raise SystemExit("[error] scipy is required (scipy.optimize.least_squares)")

    inp = Path(args.input)
    out = Path(args.output)
    distances, raw_in = load_inter_anchor_matrix(inp)
    cir_pair_weights_path = Path(args.cir_pair_weights) if args.cir_pair_weights else None
    cir_weights = load_pair_weights(cir_pair_weights_path)

    ref_constraints = load_floating_reference_sessions(args.floating_reference_session)
    n_refs = len(ref_constraints)

    # Seed layout.
    if args.seed_layout:
        anchors0 = load_layout_coords_m(Path(args.seed_layout))
    else:
        # Generate seed via sdp_init_v3.py into a temp file.
        tmp_seed = out.with_suffix(".seed.json")
        import subprocess

        subprocess.run(
            ["python3", "scripts/sdp_init_v3.py", "--input", str(inp), "--output", str(tmp_seed)],
            check=True,
        )
        anchors0 = load_layout_coords_m(tmp_seed)

    # Initialize refs from session guess.
    refs0 = [c["initial_guess_m"].copy() for c in ref_constraints]
    st = State(
        anchors={a: anchors0[a].copy() for a in ANCHORS},
        biases_m={a: 0.0 for a in ANCHORS},
        refs=refs0,
        ref_biases_m=[0.0 for _ in range(n_refs)],
    )

    # Initial bias solve (helps remove systematic offsets early).
    if not args.freeze_bias:
        if args.bias_mu is not None:
            mu = float(args.bias_mu)
        else:
            sig_b_m = max(1e-6, float(args.bias_sigma_mm) / 1000.0)
            mu = 1.0 / (sig_b_m * sig_b_m)
        st.biases_m, st.ref_biases_m = solve_biases_closed_form(
            st.anchors, st.refs, distances, ref_constraints, mu=mu
        )

    sigma_dist_m = float(args.sigma_dist_mm) / 1000.0
    sigma_ref_m = float(args.sigma_ref_mm) / 1000.0
    z_prior_m = None if args.floating_reference_z_prior_mm is None else float(args.floating_reference_z_prior_mm) / 1000.0
    z_prior_sigma_m = float(args.floating_reference_z_sigma_mm) / 1000.0
    height_sigma_m = float(args.height_sigma_mm) / 1000.0
    lower_plane_sigma_m = float(args.lower_plane_sigma_mm) / 1000.0
    upper_level_sigma_m = float(args.upper_level_sigma_mm) / 1000.0
    pair_height_sigma_m = float(args.pair_height_sigma_mm) / 1000.0
    vertical_xy_sigma_m = float(args.vertical_xy_sigma_mm) / 1000.0
    band_separation_prior_m = None if args.band_separation_prior_mm is None else float(args.band_separation_prior_mm) / 1000.0
    band_separation_sigma_m = float(args.band_separation_sigma_mm) / 1000.0

    w_edges: dict[tuple[str, str], float] | None = dict(cir_weights) if cir_weights else None
    last_rms_mm = None
    history: list[dict[str, Any]] = []

    # Fix Tukey c from the first-iteration residual scale, with a lower bound.
    c_fixed_m: float | None = None

    t0 = time.time()
    for it in range(1, int(args.max_iters) + 1):
        x0 = pack_vars(st)

        def fun(x: np.ndarray) -> np.ndarray:
            return build_residuals(
                x,
                n_refs=n_refs,
                biases_m=st.biases_m,
                ref_biases_m=st.ref_biases_m,
                distances=distances,
                ref_constraints=ref_constraints,
                sigma_dist_m=sigma_dist_m,
                sigma_ref_m=sigma_ref_m,
                z_prior_m=z_prior_m,
                z_prior_sigma_m=z_prior_sigma_m,
                w_edges=w_edges,
                geometry_mode=str(args.geometry_mode),
                height_prior_m=float(args.height_prior_m),
                height_sigma_m=height_sigma_m,
                lower_plane_sigma_m=lower_plane_sigma_m,
                upper_level_sigma_m=upper_level_sigma_m,
                pair_height_sigma_m=pair_height_sigma_m,
                vertical_xy_sigma_m=vertical_xy_sigma_m,
                band_separation_prior_m=band_separation_prior_m,
                band_separation_sigma_m=band_separation_sigma_m,
            )

        # Use plain least squares; robustification is done via IRLS weights on edges.
        sol = least_squares(fun, x0, max_nfev=800, verbose=0)
        st = unpack_vars(sol.x, n_refs, st.biases_m, st.ref_biases_m)

        # Closed-form bias update.
        if not args.freeze_bias:
            if args.bias_mu is not None:
                mu = float(args.bias_mu)
            else:
                sig_b_m = max(1e-6, float(args.bias_sigma_mm) / 1000.0)
                mu = 1.0 / (sig_b_m * sig_b_m)
            st.biases_m, st.ref_biases_m = solve_biases_closed_form(
                st.anchors, st.refs, distances, ref_constraints, mu=mu
            )

        # Update Tukey weights from *un-normalized* edge residuals in meters.
        edge_res = []
        keys = []
        for i, a in enumerate(ANCHORS):
            for b in ANCHORS[i + 1 :]:
                d = distances[(a, b)]
                pred = float(np.linalg.norm(st.anchors[a] - st.anchors[b]) + st.biases_m[a] + st.biases_m[b])
                edge_res.append(pred - d)
                keys.append((a, b))
        edge_res_arr = np.asarray(edge_res, dtype=float)
        sig = estimate_sigma_mad(edge_res_arr)
        c_min_m = float(args.tukey_c_min_mm) / 1000.0
        if c_fixed_m is None:
            c_fixed_m = max(c_min_m, float(args.tukey_c_mult) * sig)
        c = max(c_min_m, c_fixed_m)
        w_arr = tukey_weights(edge_res_arr, c)
        w_min = float(args.tukey_w_min)
        if w_min > 0.0:
            w_arr = np.maximum(w_arr, w_min)
        w_edges = {
            k: float(w) * float(cir_weights.get(k, 1.0))
            for k, w in zip(keys, w_arr)
        }

        rms_mm = rms_mm_of_edges(st.anchors, st.biases_m, distances)
        elapsed = time.time() - t0
        step = {
            "iter": it,
            "rms_edges_mm": rms_mm,
            "sigma_mad_mm": float(sig * 1000.0),
            "tukey_c_mm": float(c * 1000.0),
            "tukey_c_fixed_mm": float(c_fixed_m * 1000.0) if c_fixed_m is not None else None,
            "tukey_w_min": float(args.tukey_w_min),
            "elapsed_s": elapsed,
            "cost": float(sol.cost),
            "status": int(sol.status),
            "nfev": int(sol.nfev),
        }
        history.append(step)
        if args.verbose >= 1:
            print(
                f"iter={it} rms_edges_mm={rms_mm:.3f} sigma_mad_mm={sig*1000.0:.3f} "
                f"c_mm={c*1000.0:.1f} cost={sol.cost:.3f} nfev={sol.nfev}"
            )

        if last_rms_mm is not None and it >= 5:
            # Conservative stop: after a few iterations, stop when RMS is not
            # materially improving anymore (avoid stopping on early small oscillations).
            if (last_rms_mm - rms_mm) < 0.25:
                break
        last_rms_mm = rms_mm

    # Convert biases -> tau_ns using tau = 2*b/c.
    delays_ns = {a: float(2.0 * st.biases_m[a] / C_LIGHT * 1e9) for a in ANCHORS}
    ref_delays_ns = [float(2.0 * b / C_LIGHT * 1e9) for b in st.ref_biases_m]

    # Output format aligned with repo conventions.
    anchors_out = []
    for a in ANCHORS:
        p = st.anchors[a]
        anchors_out.append(
            {"label": a, "x_mm": float(p[0] * 1000.0), "y_mm": float(p[1] * 1000.0), "z_mm": float(p[2] * 1000.0)}
        )

    quality = {
        "rms_edges_mm": float(rms_mm_of_edges(st.anchors, st.biases_m, distances)),
        "tukey_edge_weights": {
            f"{a}-{b}": float(w_edges[(a, b)]) if w_edges else 1.0
            for i, a in enumerate(ANCHORS)
            for b in ANCHORS[i + 1 :]
        },
        "cir_pair_weights": {
            f"{a}-{b}": float(cir_weights[(a, b)])
            for i, a in enumerate(ANCHORS)
            for b in ANCHORS[i + 1 :]
            if (a, b) in cir_weights
        },
        "edge_fit": edge_fit_stats_mm(st.anchors, st.biases_m, distances, w_edges, inlier_w_thresh=0.2),
        "history": history,
    }

    payload: dict[str, Any] = {
        "units": "mm",
        "anchors": anchors_out,
        "antenna_delays_ns": delays_ns,
        "floating_reference": [
            {
                "label": ref_constraints[k]["label"],
                "session_dir": ref_constraints[k]["session_dir"],
                "ref_point_mm": {
                    "x": float(st.refs[k][0] * 1000.0),
                    "y": float(st.refs[k][1] * 1000.0),
                    "z": float(st.refs[k][2] * 1000.0),
                },
                "ref_delay_ns": float(ref_delays_ns[k]),
            }
            for k in range(n_refs)
        ],
        "quality": quality,
        "source": {
            "input": str(inp.resolve()),
            "seed_layout": str(Path(args.seed_layout).resolve()) if args.seed_layout else "auto:sdp_init_v3.py",
            "cir_pair_weights": str(cir_pair_weights_path.resolve()) if cir_pair_weights_path else None,
            "cir_pair_weight_count": len(cir_weights) // 2,
            "floating_reference_sessions": args.floating_reference_session,
            "sigma_dist_mm": float(args.sigma_dist_mm),
            "sigma_ref_mm": float(args.sigma_ref_mm),
            "geometry_mode": str(args.geometry_mode),
            "floating_reference_z_prior_mm": args.floating_reference_z_prior_mm,
            "floating_reference_z_sigma_mm": float(args.floating_reference_z_sigma_mm),
            "height_prior_m": float(args.height_prior_m),
            "height_sigma_mm": float(args.height_sigma_mm),
            "lower_plane_sigma_mm": float(args.lower_plane_sigma_mm),
            "upper_level_sigma_mm": float(args.upper_level_sigma_mm),
            "pair_height_sigma_mm": float(args.pair_height_sigma_mm),
            "vertical_xy_sigma_mm": float(args.vertical_xy_sigma_mm),
            "band_separation_prior_mm": args.band_separation_prior_mm,
            "band_separation_sigma_mm": float(args.band_separation_sigma_mm),
            "bias_sigma_mm": float(args.bias_sigma_mm),
            "bias_mu": float(args.bias_mu) if args.bias_mu is not None else None,
        },
        "notes": [
            "V3_full practical: Tukey-IRLS on inter-anchor residuals + per-anchor antenna bias (b_i) estimation.",
            f"Geometry mode: {args.geometry_mode}.",
            "Bias gauge fixed by b_A=0; delays reported as tau_ns = 2*b/c.",
            "Floating reference uses mean ranges per anchor (aggregated Tag115 CM).",
        ],
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    hist_path = out.with_name(out.stem + "_v3full_history.json")
    hist_path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    if args.verbose >= 1:
        print(f"[ok] wrote {out}")
        print(f"[ok] wrote {hist_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
