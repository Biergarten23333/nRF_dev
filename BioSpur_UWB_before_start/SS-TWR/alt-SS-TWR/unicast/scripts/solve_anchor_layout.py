#!/usr/bin/env python3
import argparse
import csv
import json
import math
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.optimize import least_squares


ANCHORS = ("A", "B", "C", "D", "E", "F", "G", "H")
LOWER_PLANE = ("A", "B", "C", "D")
UPPER_PLANE = ("E", "F", "G", "H")
VERTICAL_PAIRS = (("A", "E"), ("B", "F"), ("C", "G"), ("D", "H"))
VERTICAL_PAIR_SET = {(a, b) for a, b in VERTICAL_PAIRS} | {(b, a) for a, b in VERTICAL_PAIRS}


def load_input(path: Path) -> dict:
    return json.loads(path.read_text())


def load_distances(raw: dict) -> dict[tuple[str, str], float]:
    distances = {}
    for key, value in raw["distances"].items():
        a, b = key.split("-")
        distances[(a, b)] = value / 1000.0
        distances[(b, a)] = value / 1000.0
    return distances


def load_anchor_map_from_layout(path: Path) -> dict[str, np.ndarray]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    anchors_raw = raw["anchors"]
    if isinstance(anchors_raw, dict):
        units = raw.get("units", "m")
        scale = 0.001 if units == "mm" else 1.0
        return {name: np.array(values, dtype=float) * scale for name, values in anchors_raw.items()}

    units = raw.get("units", "m")
    scale = 0.001 if units == "mm" else 1.0
    return {
        entry["label"]: np.array(
            [entry["x_mm"], entry["y_mm"], entry["z_mm"]],
            dtype=float,
        )
        * scale
        for entry in anchors_raw
    }


def load_reference_constraints(session_dirs: list[str]) -> list[dict]:
    constraints = []
    for session_dir_str in session_dirs:
        session_dir = Path(session_dir_str)
        gt_path = session_dir / "ground_truth.json"
        ranges_path = session_dir / "ranges.csv"
        if not gt_path.exists():
            raise FileNotFoundError(f"Missing ground_truth.json in {session_dir}")
        if not ranges_path.exists():
            raise FileNotFoundError(f"Missing ranges.csv in {session_dir}")

        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        truth = gt["truth_mm"]
        by_anchor: dict[int, list[float]] = {}
        with ranges_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                anchor_id = int(row["anchor_id"])
                by_anchor.setdefault(anchor_id, []).append(float(row["filt_mm"]) / 1000.0)

        range_means = {
            anchor_id: sum(values) / len(values)
            for anchor_id, values in by_anchor.items()
            if values
        }
        constraints.append(
            {
                "session_dir": str(session_dir),
                "label": gt.get("label", session_dir.name),
                "truth_m": np.array(
                    [
                        float(truth["x_mm"]) / 1000.0,
                        float(truth["y_mm"]) / 1000.0,
                        float(truth["z_mm"]) / 1000.0,
                    ]
                ),
                "range_means_m": range_means,
            }
        )
    return constraints


def load_floating_reference_constraints(session_dirs: list[str]) -> list[dict]:
    constraints = []
    for session_dir_str in session_dirs:
        session_dir = Path(session_dir_str)
        ranges_path = session_dir / "ranges.csv"
        if not ranges_path.exists():
            raise FileNotFoundError(f"Missing ranges.csv in {session_dir}")

        by_anchor: dict[int, list[float]] = {}
        with ranges_path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                anchor_id = int(row["anchor_id"])
                by_anchor.setdefault(anchor_id, []).append(float(row["filt_mm"]) / 1000.0)

        range_means = {
            anchor_id: sum(values) / len(values)
            for anchor_id, values in by_anchor.items()
            if values
        }
        if not range_means:
            raise ValueError(
                f"Floating reference session {session_dir} has no range samples in ranges.csv"
            )

        summary_path = session_dir / "summary.json"
        initial_guess_m = np.array([1.8, 1.8, 0.7], dtype=float)
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                mean = summary.get("position_mean_mm")
                if mean:
                    initial_guess_m = np.array(
                        [
                            float(mean["x"]) / 1000.0,
                            float(mean["y"]) / 1000.0,
                            float(mean["z"]) / 1000.0,
                        ],
                        dtype=float,
                    )
            except Exception:
                pass

        constraints.append(
            {
                "session_dir": str(session_dir),
                "label": session_dir.name,
                "range_means_m": range_means,
                "initial_guess_m": initial_guess_m,
            }
        )
    return constraints


def build_initial_guess(
    distances: dict[tuple[str, str], float],
    floating_reference_constraints: list[dict] | None = None,
) -> np.ndarray:
    ab = distances[("A", "B")]
    ad = distances[("A", "D")]
    h = 1.4
    base = np.array(
        [
            ab,  # Bx
            ab, ad, 0.0,  # Cx, Cy, Cz
            0.0, ad,  # Dx, Dy
            0.0, 0.0, h,  # Ex, Ey, Ez
            ab, 0.0, h,  # Fx, Fy, Fz
            ab, ad, h,  # Gx, Gy, Gz
            0.0, ad, h,  # Hx, Hy, Hz
        ],
        dtype=float,
    )
    if not floating_reference_constraints:
        return base

    extra = [constraint["initial_guess_m"] for constraint in floating_reference_constraints]
    return np.concatenate([base, np.concatenate(extra)])


def build_initial_guess_from_layout(
    path: Path,
    floating_reference_constraints: list[dict] | None = None,
) -> np.ndarray:
    raw = json.loads(path.read_text(encoding="utf-8"))
    anchors_raw = raw["anchors"]
    if isinstance(anchors_raw, dict):
        anchors = {name: np.array(value, dtype=float) for name, value in anchors_raw.items()}
        units = raw.get("units", "m")
        scale = 0.001 if units == "mm" else 1.0
        for name in anchors:
            anchors[name] = anchors[name] * scale
    else:
        units = raw.get("units", "m")
        scale = 0.001 if units == "mm" else 1.0
        anchors = {
            entry["label"]: np.array(
                [entry["x_mm"], entry["y_mm"], entry["z_mm"]], dtype=float
            )
            * scale
            for entry in anchors_raw
        }

    base = np.array(
        [
            anchors["B"][0],
            anchors["C"][0],
            anchors["C"][1],
            anchors["C"][2],
            anchors["D"][0],
            anchors["D"][1],
            anchors["E"][0],
            anchors["E"][1],
            anchors["E"][2],
            anchors["F"][0],
            anchors["F"][1],
            anchors["F"][2],
            anchors["G"][0],
            anchors["G"][1],
            anchors["G"][2],
            anchors["H"][0],
            anchors["H"][1],
            anchors["H"][2],
        ],
        dtype=float,
    )
    if not floating_reference_constraints:
        return base

    extra = [constraint["initial_guess_m"] for constraint in floating_reference_constraints]
    return np.concatenate([base, np.concatenate(extra)])


def unpack_params(
    params: np.ndarray,
    floating_reference_constraints: list[dict] | None = None,
) -> tuple[dict[str, np.ndarray], list[np.ndarray]]:
    idx = 0
    coords = {
        "A": np.array([0.0, 0.0, 0.0]),
        "B": np.array([params[idx], 0.0, 0.0]),
    }
    idx += 1

    coords["C"] = np.array([params[idx], params[idx + 1], params[idx + 2]])
    idx += 3

    coords["D"] = np.array([params[idx], params[idx + 1], 0.0])
    idx += 2

    ex, ey, ez = params[idx], params[idx + 1], params[idx + 2]
    idx += 3
    fx, fy, fz = params[idx], params[idx + 1], params[idx + 2]
    idx += 3
    gx, gy, gz = params[idx], params[idx + 1], params[idx + 2]
    idx += 3
    hx, hy, hz = params[idx], params[idx + 1], params[idx + 2]
    idx += 3

    coords["E"] = np.array([ex, ey, ez])
    coords["F"] = np.array([fx, fy, fz])
    coords["G"] = np.array([gx, gy, gz])
    coords["H"] = np.array([hx, hy, hz])

    reference_points = []
    for _ in floating_reference_constraints or []:
        reference_points.append(np.array([params[idx], params[idx + 1], params[idx + 2]]))
        idx += 3

    return coords, reference_points


def plane_distance_residuals(points: list[np.ndarray], sigma_m: float) -> list[float]:
    point_array = np.vstack(points)
    centroid = np.mean(point_array, axis=0)
    centered = point_array - centroid
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    normal = vh[-1]
    distances = centered @ normal
    return (distances / sigma_m).tolist()


def mean_centered_residuals(values: list[float], sigma_m: float) -> list[float]:
    if sigma_m <= 0.0:
        return []
    arr = np.asarray(values, dtype=float)
    mean = float(np.mean(arr))
    return ((arr - mean) / sigma_m).tolist()


def residuals(
    params: np.ndarray,
    distances: dict[tuple[str, str], float],
    reference_constraints: list[dict],
    floating_reference_constraints: list[dict],
    plane_height_prior_m: float,
    distance_sigma_m: float,
    height_sigma_m: float,
    vertical_sigma_m: float,
    lower_plane_sigma_m: float,
    upper_plane_sigma_m: float,
    upper_level_sigma_m: float,
    pair_height_sigma_m: float,
    reference_sigma_m: float,
    floating_reference_z_prior_m: float | None,
    floating_reference_z_sigma_m: float,
    distance_sigma_same_plane_m: float,
    distance_sigma_cross_plane_m: float,
    distance_sigma_vertical_pair_m: float,
    initial_layout_prior: dict[str, np.ndarray] | None,
    prior_lower_xy_sigma_m: float,
    prior_lower_z_sigma_m: float,
    prior_upper_xy_sigma_m: float,
    prior_upper_z_sigma_m: float,
    lower_parallelogram_sigma_m: float,
    upper_parallelogram_sigma_m: float,
    cuboid_translation_xy_sigma_m: float,
    cuboid_translation_z_sigma_m: float,
    rect_diagonal_sigma_m: float,
    space_diagonal_sigma_m: float,
    lower_ortho_sigma: float,
    upper_ortho_sigma: float,
) -> np.ndarray:
    coords, floating_reference_points = unpack_params(params, floating_reference_constraints)
    res = []

    # Primary ranging residuals.
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            target = distances[(a, b)]
            actual = np.linalg.norm(coords[a] - coords[b])
            sigma = distance_sigma_m
            if (a, b) in VERTICAL_PAIR_SET:
                sigma = distance_sigma_vertical_pair_m
            elif (a in LOWER_PLANE and b in LOWER_PLANE) or (a in UPPER_PLANE and b in UPPER_PLANE):
                sigma = distance_sigma_same_plane_m
            else:
                sigma = distance_sigma_cross_plane_m
            res.append((actual - target) / sigma)

    # Soft lower-plane prior: A/B/D define the reference plane, C is allowed to
    # deviate from it slightly instead of being forced exactly coplanar.
    res.append(coords["C"][2] / lower_plane_sigma_m)

    # Optional: enforce approximate parallelogram closure on lower and upper quads.
    #
    # For an (almost) rectangle-like placement, we expect:
    #   A + C ≈ B + D
    #   E + G ≈ F + H
    #
    # This is *not* redundant with distances when ranging is noisy/NLOS: it encodes
    # a structural prior about the layout topology.
    if lower_parallelogram_sigma_m > 0.0:
        closure = (coords["A"] + coords["C"]) - (coords["B"] + coords["D"])
        res.append(closure[0] / lower_parallelogram_sigma_m)
        res.append(closure[1] / lower_parallelogram_sigma_m)
        res.append(closure[2] / lower_parallelogram_sigma_m)
    if upper_parallelogram_sigma_m > 0.0:
        closure = (coords["E"] + coords["G"]) - (coords["F"] + coords["H"])
        res.append(closure[0] / upper_parallelogram_sigma_m)
        res.append(closure[1] / upper_parallelogram_sigma_m)
        res.append(closure[2] / upper_parallelogram_sigma_m)

    # Soft upper-plane prior: the four upper anchors should be close to a common
    # plane, but not exactly share the same Z.
    upper_points = [coords[name] for name in UPPER_PLANE]
    if upper_plane_sigma_m > 0.0:
        res.extend(plane_distance_residuals(upper_points, upper_plane_sigma_m))

    # Real installations are not perfectly coplanar, but in this setup the
    # upper anchors are expected to be close in height relative to the lower
    # reference plane rather than diverging by hundreds of millimetres.
    upper_z = [coords[name][2] for name in UPPER_PLANE]
    res.extend(mean_centered_residuals(upper_z, upper_level_sigma_m))

    # Soft average height prior for the upper cluster.
    upper_mean_height = float(np.mean([coords[name][2] for name in UPPER_PLANE]))
    res.append((upper_mean_height - plane_height_prior_m) / height_sigma_m)

    # Optional: cuboid-like vertical translation consistency.
    #
    # Even if AE/BF/CG/DH are not perfectly aligned in XY, in a "stacked rectangle"
    # installation we expect the translation vectors to be similar:
    #   t_A = E - A, t_B = F - B, t_C = G - C, t_D = H - D
    #
    # This helps stabilize Z/tilt under NLOS without forcing exact XY equality.
    if cuboid_translation_xy_sigma_m > 0.0 or cuboid_translation_z_sigma_m > 0.0:
        t = [
            coords["E"] - coords["A"],
            coords["F"] - coords["B"],
            coords["G"] - coords["C"],
            coords["H"] - coords["D"],
        ]
        mean_t = np.mean(np.vstack(t), axis=0)
        for ti in t:
            d = ti - mean_t
            if cuboid_translation_xy_sigma_m > 0.0:
                res.append(d[0] / cuboid_translation_xy_sigma_m)
                res.append(d[1] / cuboid_translation_xy_sigma_m)
            if cuboid_translation_z_sigma_m > 0.0:
                res.append(d[2] / cuboid_translation_z_sigma_m)

    # Optional: rectangle diagonal equality in each quad (AC ≈ BD, EG ≈ FH).
    if rect_diagonal_sigma_m > 0.0:
        d_ac = float(np.linalg.norm(coords["A"] - coords["C"]))
        d_bd = float(np.linalg.norm(coords["B"] - coords["D"]))
        d_eg = float(np.linalg.norm(coords["E"] - coords["G"]))
        d_fh = float(np.linalg.norm(coords["F"] - coords["H"]))
        res.append((d_ac - d_bd) / rect_diagonal_sigma_m)
        res.append((d_eg - d_fh) / rect_diagonal_sigma_m)

    # Optional: space diagonal equality for the implied parallelepiped.
    # In an ideal cuboid-like placement, these four body diagonals should be equal:
    #   A-G, B-H, C-E, D-F
    if space_diagonal_sigma_m > 0.0:
        d_ag = float(np.linalg.norm(coords["A"] - coords["G"]))
        d_bh = float(np.linalg.norm(coords["B"] - coords["H"]))
        d_ce = float(np.linalg.norm(coords["C"] - coords["E"]))
        d_df = float(np.linalg.norm(coords["D"] - coords["F"]))
        mean_d = (d_ag + d_bh + d_ce + d_df) / 4.0
        res.append((d_ag - mean_d) / space_diagonal_sigma_m)
        res.append((d_bh - mean_d) / space_diagonal_sigma_m)
        res.append((d_ce - mean_d) / space_diagonal_sigma_m)
        res.append((d_df - mean_d) / space_diagonal_sigma_m)

    # Optional: orthogonality priors for the rectangle edges (AB ⟂ AD, EF ⟂ EH).
    #
    # We use cos(theta) as a unitless residual (0 means perfectly orthogonal).
    def cos_angle(u: np.ndarray, v: np.ndarray) -> float:
        nu = float(np.linalg.norm(u))
        nv = float(np.linalg.norm(v))
        if nu <= 1e-9 or nv <= 1e-9:
            return 0.0
        return float(np.dot(u, v) / (nu * nv))

    if lower_ortho_sigma > 0.0:
        u = coords["B"] - coords["A"]
        v = coords["D"] - coords["A"]
        res.append(cos_angle(u, v) / lower_ortho_sigma)
    if upper_ortho_sigma > 0.0:
        u = coords["F"] - coords["E"]
        v = coords["H"] - coords["E"]
        res.append(cos_angle(u, v) / upper_ortho_sigma)

    # Optional: Encourage paired anchors to stay roughly vertically aligned in XY.
    #
    # Real installations often have non-zero XY projection offsets between the
    # lower/upper anchors, so this should default to *disabled* unless the user
    # explicitly enables it. Use --vertical-sigma-mm > 0 to enable.
    if vertical_sigma_m > 0.0:
        for lower, upper in VERTICAL_PAIRS:
            dx = coords[upper][0] - coords[lower][0]
            dy = coords[upper][1] - coords[lower][1]
            res.append(dx / vertical_sigma_m)
            res.append(dy / vertical_sigma_m)

    # The four vertical pairs should have similar height separation even when
    # they are not mathematically identical.
    pair_heights = [(coords[upper][2] - coords[lower][2]) for lower, upper in VERTICAL_PAIRS]
    res.extend(mean_centered_residuals(pair_heights, pair_height_sigma_m))

    # Encourage the lower plane to keep a right-handed orientation.
    # This avoids accidental mirror/flip solutions around the A-B axis.
    dy = coords["D"][1]
    res.append(min(0.0, dy) / 0.05)

    # Optional fixed-reference constraints: use mean measured ranges from a
    # known static Tag position to pull the anchor layout toward reality.
    for constraint in reference_constraints:
        truth_point = constraint["truth_m"]
        for anchor_id, measured_range_m in constraint["range_means_m"].items():
            anchor_label = ANCHORS[anchor_id]
            predicted = np.linalg.norm(coords[anchor_label] - truth_point)
            res.append((predicted - measured_range_m) / reference_sigma_m)

    for constraint, ref_point in zip(floating_reference_constraints, floating_reference_points):
        for anchor_id, measured_range_m in constraint["range_means_m"].items():
            anchor_label = ANCHORS[anchor_id]
            predicted = np.linalg.norm(coords[anchor_label] - ref_point)
            res.append((predicted - measured_range_m) / reference_sigma_m)
        if floating_reference_z_prior_m is not None and floating_reference_z_sigma_m > 0.0:
            res.append((ref_point[2] - floating_reference_z_prior_m) / floating_reference_z_sigma_m)

    # Strong prior to keep topology close to the current validated layout.
    # This is especially important when only lower anchors move slightly.
    if initial_layout_prior:
        for name in ANCHORS:
            prior = initial_layout_prior[name]
            current = coords[name]
            if name in LOWER_PLANE:
                if prior_lower_xy_sigma_m > 0.0:
                    res.append((current[0] - prior[0]) / prior_lower_xy_sigma_m)
                    res.append((current[1] - prior[1]) / prior_lower_xy_sigma_m)
                if prior_lower_z_sigma_m > 0.0:
                    res.append((current[2] - prior[2]) / prior_lower_z_sigma_m)
            else:
                if prior_upper_xy_sigma_m > 0.0:
                    res.append((current[0] - prior[0]) / prior_upper_xy_sigma_m)
                    res.append((current[1] - prior[1]) / prior_upper_xy_sigma_m)
                if prior_upper_z_sigma_m > 0.0:
                    res.append((current[2] - prior[2]) / prior_upper_z_sigma_m)

    return np.array(res)


def edge_class(a: str, b: str) -> str:
    if (a, b) in VERTICAL_PAIR_SET:
        return "vertical_pair"
    same_lower = a in LOWER_PLANE and b in LOWER_PLANE
    same_upper = a in UPPER_PLANE and b in UPPER_PLANE
    if same_lower or same_upper:
        return "same_plane"
    return "cross_plane"


def distance_residual_stats_by_class(
    coords: dict[str, np.ndarray],
    distances: dict[tuple[str, str], float],
) -> dict[str, dict[str, float]]:
    by_class: dict[str, list[float]] = {"same_plane": [], "cross_plane": [], "vertical_pair": []}
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            target = distances[(a, b)]
            actual = float(np.linalg.norm(coords[a] - coords[b]))
            by_class[edge_class(a, b)].append(actual - target)

    out: dict[str, dict[str, float]] = {}
    for k, vals in by_class.items():
        arr = np.asarray(vals, dtype=float)
        rms_m = float(np.sqrt(np.mean(arr * arr))) if len(arr) else 0.0
        mad_m = float(np.median(np.abs(arr - np.median(arr)))) if len(arr) else 0.0
        out[k] = {
            "count": len(vals),
            "rms_m": rms_m,
            "mad_m": mad_m,
        }
    return out


def solve_once(
    x0: np.ndarray,
    *,
    distances: dict[tuple[str, str], float],
    reference_constraints: list[dict],
    floating_reference_constraints: list[dict],
    height_prior_m: float,
    distance_sigma_mm: float,
    height_sigma_mm: float,
    vertical_sigma_mm: float,
    lower_plane_sigma_mm: float,
    upper_plane_sigma_mm: float,
    upper_level_sigma_mm: float,
    pair_height_sigma_mm: float,
    reference_sigma_mm: float,
    floating_reference_z_prior_mm: Optional[float],
    floating_reference_z_sigma_mm: float,
    distance_sigma_same_plane_mm: float,
    distance_sigma_cross_plane_mm: float,
    distance_sigma_vertical_pair_mm: float,
    initial_layout_prior: dict[str, np.ndarray] | None,
    prior_lower_xy_sigma_mm: float,
    prior_lower_z_sigma_mm: float,
    prior_upper_xy_sigma_mm: float,
    prior_upper_z_sigma_mm: float,
    lower_parallelogram_sigma_mm: float,
    upper_parallelogram_sigma_mm: float,
    cuboid_translation_xy_sigma_mm: float,
    cuboid_translation_z_sigma_mm: float,
    rect_diagonal_sigma_mm: float,
    space_diagonal_sigma_mm: float,
    lower_ortho_sigma: float,
    upper_ortho_sigma: float,
    loss: str,
    max_nfev: int,
):
    return least_squares(
        residuals,
        x0,
        args=(
            distances,
            reference_constraints,
            floating_reference_constraints,
            height_prior_m,
            distance_sigma_mm / 1000.0,
            height_sigma_mm / 1000.0,
            vertical_sigma_mm / 1000.0,
            lower_plane_sigma_mm / 1000.0,
            upper_plane_sigma_mm / 1000.0,
            upper_level_sigma_mm / 1000.0,
            pair_height_sigma_mm / 1000.0,
            reference_sigma_mm / 1000.0,
            (
                None
                if floating_reference_z_prior_mm is None
                else floating_reference_z_prior_mm / 1000.0
            ),
            floating_reference_z_sigma_mm / 1000.0,
            distance_sigma_same_plane_mm / 1000.0,
            distance_sigma_cross_plane_mm / 1000.0,
            distance_sigma_vertical_pair_mm / 1000.0,
            initial_layout_prior,
            prior_lower_xy_sigma_mm / 1000.0,
            prior_lower_z_sigma_mm / 1000.0,
            prior_upper_xy_sigma_mm / 1000.0,
            prior_upper_z_sigma_mm / 1000.0,
            lower_parallelogram_sigma_mm / 1000.0,
            upper_parallelogram_sigma_mm / 1000.0,
            cuboid_translation_xy_sigma_mm / 1000.0,
            cuboid_translation_z_sigma_mm / 1000.0,
            rect_diagonal_sigma_mm / 1000.0,
            space_diagonal_sigma_mm / 1000.0,
            lower_ortho_sigma,
            upper_ortho_sigma,
        ),
        max_nfev=max_nfev,
        loss=loss,
        f_scale=1.0,
        verbose=0,
    )


def anchor_shift_report_mm(
    coords: dict[str, np.ndarray],
    initial_layout_prior: dict[str, np.ndarray] | None,
) -> dict[str, float]:
    if not initial_layout_prior:
        return {}
    out = {}
    for name in ANCHORS:
        delta = coords[name] - initial_layout_prior[name]
        out[name] = float(np.linalg.norm(delta) * 1000.0)
    return out


def solution_score(
    coords: dict[str, np.ndarray],
    distances: dict[tuple[str, str], float],
    initial_layout_prior: dict[str, np.ndarray] | None,
) -> dict[str, float]:
    data_rms = rms_mm(coords, distances)
    upper_z = np.array([coords[name][2] for name in UPPER_PLANE], dtype=float)
    lower_z = np.array([coords[name][2] for name in LOWER_PLANE], dtype=float)
    upper_spread_mm = float((np.max(upper_z) - np.min(upper_z)) * 1000.0)
    separation_mm = float((np.mean(upper_z) - np.mean(lower_z)) * 1000.0)

    shifts = anchor_shift_report_mm(coords, initial_layout_prior)
    mean_shift_mm = float(np.mean(list(shifts.values()))) if shifts else 0.0

    sep_penalty = max(0.0, 1200.0 - separation_mm) * 1.5
    spread_penalty = max(0.0, upper_spread_mm - 350.0) * 0.5
    shift_penalty = max(0.0, mean_shift_mm - 1200.0) * 0.05
    score = data_rms + sep_penalty + spread_penalty + shift_penalty
    return {
        "score": float(score),
        "data_rms_mm": float(data_rms),
        "upper_spread_mm": float(upper_spread_mm),
        "upper_lower_separation_mm": float(separation_mm),
        "mean_anchor_shift_mm_vs_initial": float(mean_shift_mm),
    }


def estimate_anchor_uncertainty_mm(
    result,
) -> dict[str, dict[str, float]]:
    jac = getattr(result, "jac", None)
    if jac is None:
        return {}
    jt_j = jac.T @ jac
    try:
        cov = np.linalg.inv(jt_j)
    except np.linalg.LinAlgError:
        cov = np.linalg.pinv(jt_j)

    dof = max(1, jac.shape[0] - jac.shape[1])
    sigma2 = float(2.0 * result.cost / dof)
    cov_scaled = cov * sigma2

    # Parameter layout:
    # Bx (1), CxCyCz (3), DxDy (2), EFGH xyz (12), then floating refs xyz...
    pstd = np.sqrt(np.maximum(np.diag(cov_scaled), 0.0))
    anchor_std = {
        "A": {"sx_mm": 0.0, "sy_mm": 0.0, "sz_mm": 0.0},
        "B": {"sx_mm": float(pstd[0] * 1000.0), "sy_mm": 0.0, "sz_mm": 0.0},
        "C": {
            "sx_mm": float(pstd[1] * 1000.0),
            "sy_mm": float(pstd[2] * 1000.0),
            "sz_mm": float(pstd[3] * 1000.0),
        },
        "D": {
            "sx_mm": float(pstd[4] * 1000.0),
            "sy_mm": float(pstd[5] * 1000.0),
            "sz_mm": 0.0,
        },
    }
    names = ["E", "F", "G", "H"]
    base = 6
    for i, name in enumerate(names):
        off = base + i * 3
        anchor_std[name] = {
            "sx_mm": float(pstd[off + 0] * 1000.0),
            "sy_mm": float(pstd[off + 1] * 1000.0),
            "sz_mm": float(pstd[off + 2] * 1000.0),
        }
    cond = float(np.linalg.cond(jt_j))
    anchor_std["_meta"] = {"normal_matrix_cond": cond}
    return anchor_std


def rms_mm(coords: dict[str, np.ndarray], distances: dict[tuple[str, str], float]) -> float:
    errs = []
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            target = distances[(a, b)]
            actual = np.linalg.norm(coords[a] - coords[b])
            errs.append((actual - target) * 1000.0)
    return math.sqrt(sum(e * e for e in errs) / len(errs))


def vertical_pair_report(coords: dict[str, np.ndarray]) -> dict[str, dict[str, float]]:
    report = {}
    for lower, upper in VERTICAL_PAIRS:
        delta = coords[upper] - coords[lower]
        report[f"{lower}-{upper}"] = {
            "dx_m": float(delta[0]),
            "dy_m": float(delta[1]),
            "dz_m": float(delta[2]),
            "xy_offset_m": float(np.linalg.norm(delta[:2])),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Solve a stable relative 3D anchor layout from inter-anchor distances."
    )
    parser.add_argument(
        "--input",
        default="data/inter_anchor_matrix_ah.json",
        help="Path to the inter-anchor distance JSON file.",
    )
    parser.add_argument(
        "--output",
        default="data/anchor_layout_ah_solution.json",
        help="Path to write the solved coordinates JSON.",
    )
    parser.add_argument(
        "--height-prior-m",
        type=float,
        default=1.4,
        help="Soft prior for the vertical separation between lower and upper planes.",
    )
    parser.add_argument(
        "--distance-sigma-mm",
        type=float,
        default=70.0,
        help="Assumed 1-sigma range error used to scale ranging residuals.",
    )
    parser.add_argument(
        "--distance-sigma-same-plane-mm",
        type=float,
        default=120.0,
        help="1-sigma ranging error for lower-lower and upper-upper edges.",
    )
    parser.add_argument(
        "--distance-sigma-cross-plane-mm",
        type=float,
        default=180.0,
        help="1-sigma ranging error for cross-plane edges.",
    )
    parser.add_argument(
        "--distance-sigma-vertical-pair-mm",
        type=float,
        default=120.0,
        help="1-sigma ranging error for paired vertical edges (A-E/B-F/C-G/D-H).",
    )
    parser.add_argument(
        "--height-sigma-mm",
        type=float,
        default=150.0,
        help="Assumed 1-sigma uncertainty for the plane-height prior.",
    )
    parser.add_argument(
        "--vertical-sigma-mm",
        type=float,
        default=0.0,
        help="Optional 1-sigma XY offset (mm) used to *softly* encourage vertical-pair XY alignment. "
        "Set to 0 to disable (recommended when upper/lower XY projections do not overlap).",
    )
    parser.add_argument(
        "--lower-plane-sigma-mm",
        type=float,
        default=120.0,
        help="Allowed deviation of C from the lower reference plane.",
    )
    parser.add_argument(
        "--upper-plane-sigma-mm",
        type=float,
        default=120.0,
        help="Allowed signed distance of upper anchors from a shared best-fit plane.",
    )
    parser.add_argument(
        "--upper-level-sigma-mm",
        type=float,
        default=35.0,
        help="Allowed 1-sigma deviation of each upper anchor Z from the upper-cluster mean Z.",
    )
    parser.add_argument(
        "--pair-height-sigma-mm",
        type=float,
        default=45.0,
        help="Allowed 1-sigma deviation of each vertical-pair height from the mean pair height.",
    )
    parser.add_argument(
        "--reference-session",
        action="append",
        default=[],
        help="Ground-truth session directory containing ground_truth.json and ranges.csv. "
             "May be passed multiple times.",
    )
    parser.add_argument(
        "--reference-sigma-mm",
        type=float,
        default=60.0,
        help="Assumed 1-sigma error for fixed reference-tag mean ranges.",
    )
    parser.add_argument(
        "--floating-reference-session",
        action="append",
        default=[],
        help="Static reference-tag session directory containing ranges.csv but not requiring "
             "a known absolute XYZ. May be passed multiple times.",
    )
    parser.add_argument(
        "--floating-reference-z-prior-mm",
        type=float,
        default=None,
        help="Soft prior for the unknown floating reference Z height in mm.",
    )
    parser.add_argument(
        "--floating-reference-z-sigma-mm",
        type=float,
        default=80.0,
        help="Allowed 1-sigma deviation for the floating reference Z prior.",
    )
    parser.add_argument(
        "--initial-layout",
        default=None,
        help="Optional JSON layout file used as the initial guess.",
    )
    parser.add_argument(
        "--prior-lower-xy-sigma-mm",
        type=float,
        default=1200.0,
        help="Layout-prior 1-sigma for lower anchor XY drift from initial layout.",
    )
    parser.add_argument(
        "--prior-lower-z-sigma-mm",
        type=float,
        default=500.0,
        help="Layout-prior 1-sigma for lower anchor Z drift from initial layout.",
    )
    parser.add_argument(
        "--prior-upper-xy-sigma-mm",
        type=float,
        default=800.0,
        help="Layout-prior 1-sigma for upper anchor XY drift from initial layout.",
    )
    parser.add_argument(
        "--prior-upper-z-sigma-mm",
        type=float,
        default=350.0,
        help="Layout-prior 1-sigma for upper anchor Z drift from initial layout.",
    )
    parser.add_argument(
        "--lower-parallelogram-sigma-mm",
        type=float,
        default=400.0,
        help="Optional 1-sigma (mm) for lower quad closure prior: A + C ≈ B + D. 0 disables.",
    )
    parser.add_argument(
        "--upper-parallelogram-sigma-mm",
        type=float,
        default=400.0,
        help="Optional 1-sigma (mm) for upper quad closure prior: E + G ≈ F + H. 0 disables.",
    )
    parser.add_argument(
        "--cuboid-translation-xy-sigma-mm",
        type=float,
        default=400.0,
        help="Optional 1-sigma (mm) to keep vertical translation vectors (E-A,F-B,G-C,H-D) consistent in XY. 0 disables.",
    )
    parser.add_argument(
        "--cuboid-translation-z-sigma-mm",
        type=float,
        default=200.0,
        help="Optional 1-sigma (mm) to keep vertical translation vectors (E-A,F-B,G-C,H-D) consistent in Z. 0 disables.",
    )
    parser.add_argument(
        "--rect-diagonal-sigma-mm",
        type=float,
        default=400.0,
        help="Optional 1-sigma (mm) for rectangle diagonal equality: AC ≈ BD and EG ≈ FH. 0 disables.",
    )
    parser.add_argument(
        "--space-diagonal-sigma-mm",
        type=float,
        default=600.0,
        help="Optional 1-sigma (mm) for space diagonal equality: A-G, B-H, C-E, D-F. 0 disables.",
    )
    parser.add_argument(
        "--lower-ortho-sigma",
        type=float,
        default=0.0,
        help="Unitless 1-sigma for cos(angle) prior of AB ⟂ AD. 0 disables.",
    )
    parser.add_argument(
        "--upper-ortho-sigma",
        type=float,
        default=0.0,
        help="Unitless 1-sigma for cos(angle) prior of EF ⟂ EH. 0 disables.",
    )
    parser.add_argument(
        "--multi-start",
        type=int,
        default=8,
        help="Number of randomized starts; best-scoring solution is selected.",
    )
    parser.add_argument(
        "--start-jitter-mm",
        type=float,
        default=450.0,
        help="Gaussian jitter (mm) applied to each random start around x0.",
    )
    parser.add_argument(
        "--adaptive-edge-reweight-rounds",
        type=int,
        default=2,
        help="Adaptive reweight rounds for edge classes based on residual statistics.",
    )
    args = parser.parse_args()

    raw = load_input(Path(args.input))
    distances = load_distances(raw)
    reference_constraints = load_reference_constraints(args.reference_session)
    floating_reference_constraints = load_floating_reference_constraints(
        args.floating_reference_session
    )
    initial_layout_prior = (
        load_anchor_map_from_layout(Path(args.initial_layout))
        if args.initial_layout
        else None
    )
    x0 = (
        build_initial_guess_from_layout(Path(args.initial_layout), floating_reference_constraints)
        if args.initial_layout
        else build_initial_guess(distances, floating_reference_constraints)
    )

    rng = np.random.default_rng(42)
    best = None
    best_meta = None
    best_result = None
    best_ref_points = None

    n_starts = max(1, int(args.multi_start))
    for start_idx in range(n_starts):
        if start_idx == 0:
            start_x = x0.copy()
        else:
            jitter_m = args.start_jitter_mm / 1000.0
            start_x = x0 + rng.normal(0.0, jitter_m, size=x0.shape)

        # Stage 1: coarse fit with weak priors and linear loss.
        coarse = solve_once(
            start_x,
            distances=distances,
            reference_constraints=reference_constraints,
            floating_reference_constraints=floating_reference_constraints,
            height_prior_m=args.height_prior_m,
            distance_sigma_mm=args.distance_sigma_mm * 1.8,
            height_sigma_mm=args.height_sigma_mm * 1.8,
            vertical_sigma_mm=args.vertical_sigma_mm * 1.8,
            lower_plane_sigma_mm=args.lower_plane_sigma_mm * 1.6,
            upper_plane_sigma_mm=args.upper_plane_sigma_mm * 1.6,
            upper_level_sigma_mm=args.upper_level_sigma_mm * 1.6,
            pair_height_sigma_mm=args.pair_height_sigma_mm * 1.6,
            reference_sigma_mm=args.reference_sigma_mm * 1.5,
            floating_reference_z_prior_mm=args.floating_reference_z_prior_mm,
            floating_reference_z_sigma_mm=args.floating_reference_z_sigma_mm * 1.6,
            distance_sigma_same_plane_mm=args.distance_sigma_same_plane_mm * 1.6,
            distance_sigma_cross_plane_mm=args.distance_sigma_cross_plane_mm * 1.6,
            distance_sigma_vertical_pair_mm=args.distance_sigma_vertical_pair_mm * 1.6,
            initial_layout_prior=initial_layout_prior,
            prior_lower_xy_sigma_mm=args.prior_lower_xy_sigma_mm * 2.5,
            prior_lower_z_sigma_mm=args.prior_lower_z_sigma_mm * 2.5,
            prior_upper_xy_sigma_mm=args.prior_upper_xy_sigma_mm * 2.5,
            prior_upper_z_sigma_mm=args.prior_upper_z_sigma_mm * 2.5,
            lower_parallelogram_sigma_mm=args.lower_parallelogram_sigma_mm * 1.6,
            upper_parallelogram_sigma_mm=args.upper_parallelogram_sigma_mm * 1.6,
            cuboid_translation_xy_sigma_mm=args.cuboid_translation_xy_sigma_mm * 1.6,
            cuboid_translation_z_sigma_mm=args.cuboid_translation_z_sigma_mm * 1.6,
            rect_diagonal_sigma_mm=args.rect_diagonal_sigma_mm * 1.6,
            space_diagonal_sigma_mm=args.space_diagonal_sigma_mm * 1.6,
            lower_ortho_sigma=args.lower_ortho_sigma * 1.6,
            upper_ortho_sigma=args.upper_ortho_sigma * 1.6,
            loss="linear",
            max_nfev=2500,
        )

        # Stage 2+: robust refinement with adaptive edge class reweighting.
        class_sigma_same = args.distance_sigma_same_plane_mm
        class_sigma_cross = args.distance_sigma_cross_plane_mm
        class_sigma_pair = args.distance_sigma_vertical_pair_mm
        current = coarse
        for _ in range(max(0, int(args.adaptive_edge_reweight_rounds)) + 1):
            current = solve_once(
                current.x,
                distances=distances,
                reference_constraints=reference_constraints,
                floating_reference_constraints=floating_reference_constraints,
                height_prior_m=args.height_prior_m,
                distance_sigma_mm=args.distance_sigma_mm,
                height_sigma_mm=args.height_sigma_mm,
                vertical_sigma_mm=args.vertical_sigma_mm,
                lower_plane_sigma_mm=args.lower_plane_sigma_mm,
                upper_plane_sigma_mm=args.upper_plane_sigma_mm,
                upper_level_sigma_mm=args.upper_level_sigma_mm,
                pair_height_sigma_mm=args.pair_height_sigma_mm,
                reference_sigma_mm=args.reference_sigma_mm,
                floating_reference_z_prior_mm=args.floating_reference_z_prior_mm,
                floating_reference_z_sigma_mm=args.floating_reference_z_sigma_mm,
                distance_sigma_same_plane_mm=class_sigma_same,
                distance_sigma_cross_plane_mm=class_sigma_cross,
                distance_sigma_vertical_pair_mm=class_sigma_pair,
                initial_layout_prior=initial_layout_prior,
                prior_lower_xy_sigma_mm=args.prior_lower_xy_sigma_mm,
                prior_lower_z_sigma_mm=args.prior_lower_z_sigma_mm,
                prior_upper_xy_sigma_mm=args.prior_upper_xy_sigma_mm,
                prior_upper_z_sigma_mm=args.prior_upper_z_sigma_mm,
                lower_parallelogram_sigma_mm=args.lower_parallelogram_sigma_mm,
                upper_parallelogram_sigma_mm=args.upper_parallelogram_sigma_mm,
                cuboid_translation_xy_sigma_mm=args.cuboid_translation_xy_sigma_mm,
                cuboid_translation_z_sigma_mm=args.cuboid_translation_z_sigma_mm,
                rect_diagonal_sigma_mm=args.rect_diagonal_sigma_mm,
                space_diagonal_sigma_mm=args.space_diagonal_sigma_mm,
                lower_ortho_sigma=args.lower_ortho_sigma,
                upper_ortho_sigma=args.upper_ortho_sigma,
                loss="soft_l1",
                max_nfev=5000,
            )
            coords_tmp, _ = unpack_params(current.x, floating_reference_constraints)
            class_stats = distance_residual_stats_by_class(coords_tmp, distances)
            # Adaptive update: noisy class gets looser sigma; stable class remains tighter.
            class_sigma_same = max(
                args.distance_sigma_same_plane_mm,
                0.7 * class_sigma_same + 0.3 * max(args.distance_sigma_same_plane_mm, class_stats["same_plane"]["rms_m"] * 2000.0),
            )
            class_sigma_cross = max(
                args.distance_sigma_cross_plane_mm,
                0.7 * class_sigma_cross + 0.3 * max(args.distance_sigma_cross_plane_mm, class_stats["cross_plane"]["rms_m"] * 2000.0),
            )
            class_sigma_pair = max(
                args.distance_sigma_vertical_pair_mm,
                0.7 * class_sigma_pair + 0.3 * max(args.distance_sigma_vertical_pair_mm, class_stats["vertical_pair"]["rms_m"] * 2000.0),
            )

        coords_tmp, refs_tmp = unpack_params(current.x, floating_reference_constraints)
        meta = solution_score(coords_tmp, distances, initial_layout_prior)
        if best is None or meta["score"] < best_meta["score"]:
            best = coords_tmp
            best_meta = meta
            best_result = current
            best_ref_points = refs_tmp

    coords = best
    floating_reference_points = best_ref_points
    result = best_result

    # Keep the upper plane positive in Z for readability.
    if coords["E"][2] < 0:
        for name in ANCHORS:
            coords[name] = coords[name] * np.array([1.0, 1.0, -1.0])

    rms = rms_mm(coords, distances)
    pair_report = vertical_pair_report(coords)
    shift_mm = anchor_shift_report_mm(coords, initial_layout_prior)
    class_stats = distance_residual_stats_by_class(coords, distances)
    uncertainty = estimate_anchor_uncertainty_mm(result)

    serializable = {
        "units": "m",
        "solver": {
            "type": "constrained_least_squares_v4_soft_planes_levelled",
            "distance_sigma_mm": args.distance_sigma_mm,
            "distance_sigma_same_plane_mm": args.distance_sigma_same_plane_mm,
            "distance_sigma_cross_plane_mm": args.distance_sigma_cross_plane_mm,
            "distance_sigma_vertical_pair_mm": args.distance_sigma_vertical_pair_mm,
            "height_prior_m": args.height_prior_m,
            "height_sigma_mm": args.height_sigma_mm,
            "vertical_sigma_mm": args.vertical_sigma_mm,
            "lower_plane_sigma_mm": args.lower_plane_sigma_mm,
            "upper_plane_sigma_mm": args.upper_plane_sigma_mm,
            "upper_level_sigma_mm": args.upper_level_sigma_mm,
            "pair_height_sigma_mm": args.pair_height_sigma_mm,
            "reference_sigma_mm": args.reference_sigma_mm,
            "reference_session_count": len(reference_constraints),
            "floating_reference_session_count": len(floating_reference_constraints),
            "floating_reference_z_prior_mm": args.floating_reference_z_prior_mm,
            "floating_reference_z_sigma_mm": args.floating_reference_z_sigma_mm,
            "prior_lower_xy_sigma_mm": args.prior_lower_xy_sigma_mm,
            "prior_lower_z_sigma_mm": args.prior_lower_z_sigma_mm,
            "prior_upper_xy_sigma_mm": args.prior_upper_xy_sigma_mm,
            "prior_upper_z_sigma_mm": args.prior_upper_z_sigma_mm,
            "lower_parallelogram_sigma_mm": args.lower_parallelogram_sigma_mm,
            "upper_parallelogram_sigma_mm": args.upper_parallelogram_sigma_mm,
            "cuboid_translation_xy_sigma_mm": args.cuboid_translation_xy_sigma_mm,
            "cuboid_translation_z_sigma_mm": args.cuboid_translation_z_sigma_mm,
            "rect_diagonal_sigma_mm": args.rect_diagonal_sigma_mm,
            "space_diagonal_sigma_mm": args.space_diagonal_sigma_mm,
            "lower_ortho_sigma": args.lower_ortho_sigma,
            "upper_ortho_sigma": args.upper_ortho_sigma,
            "multi_start": args.multi_start,
            "start_jitter_mm": args.start_jitter_mm,
            "adaptive_edge_reweight_rounds": args.adaptive_edge_reweight_rounds,
            "termination_status": int(result.status),
            "message": result.message,
        },
        "rms_error_mm": rms,
        "score": best_meta,
        "anchors": {name: coords[name].round(6).tolist() for name in ANCHORS},
        "anchor_shift_mm_vs_initial": shift_mm,
        "distance_residual_by_class": class_stats,
        "uncertainty_mm": uncertainty,
        "vertical_pairs": pair_report,
        "reference_constraints": [
            {
                "label": c["label"],
                "session_dir": c["session_dir"],
                "truth_m": c["truth_m"].round(6).tolist(),
                "anchor_count": len(c["range_means_m"]),
            }
            for c in reference_constraints
        ],
        "floating_reference_constraints": [
            {
                "label": c["label"],
                "session_dir": c["session_dir"],
                "anchor_count": len(c["range_means_m"]),
                "solved_reference_m": ref.round(6).tolist(),
            }
            for c, ref in zip(floating_reference_constraints, floating_reference_points)
        ],
    }
    Path(args.output).write_text(json.dumps(serializable, indent=2) + "\n")

    print(f"RMS error: {rms:.2f} mm")
    upper_z = [coords[name][2] for name in UPPER_PLANE]
    print(f"Upper-plane mean height: {sum(upper_z) / len(upper_z):.3f} m")
    print(f"Upper-plane z spread: min={min(upper_z):.3f} m max={max(upper_z):.3f} m")
    print(f"Lower-plane C z offset: {coords['C'][2]:.3f} m")
    for name in ANCHORS:
        x, y, z = coords[name]
        print(f"{name}: x={x:.3f} m y={y:.3f} m z={z:.3f} m")
    print("Vertical pair offsets:")
    for pair, values in pair_report.items():
        print(
            f"  {pair}: dx={values['dx_m']:.3f} m "
            f"dy={values['dy_m']:.3f} m dz={values['dz_m']:.3f} m "
            f"xy={values['xy_offset_m']:.3f} m"
        )
    for constraint, ref in zip(floating_reference_constraints, floating_reference_points):
        print(
            f"Floating reference {constraint['label']}: "
            f"x={ref[0]:.3f} m y={ref[1]:.3f} m z={ref[2]:.3f} m"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
