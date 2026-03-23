#!/usr/bin/env python3
import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares


ANCHORS = ("A", "B", "C", "D", "E", "F", "G", "H")
LOWER_PLANE = ("A", "B", "C", "D")
UPPER_PLANE = ("E", "F", "G", "H")
VERTICAL_PAIRS = (("A", "E"), ("B", "F"), ("C", "G"), ("D", "H"))


def load_input(path: Path) -> dict:
    return json.loads(path.read_text())


def load_distances(raw: dict) -> dict[tuple[str, str], float]:
    distances = {}
    for key, value in raw["distances"].items():
        a, b = key.split("-")
        distances[(a, b)] = value / 1000.0
        distances[(b, a)] = value / 1000.0
    return distances


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
) -> np.ndarray:
    coords, floating_reference_points = unpack_params(params, floating_reference_constraints)
    res = []

    # Primary ranging residuals.
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            target = distances[(a, b)]
            actual = np.linalg.norm(coords[a] - coords[b])
            res.append((actual - target) / distance_sigma_m)

    # Soft lower-plane prior: A/B/D define the reference plane, C is allowed to
    # deviate from it slightly instead of being forced exactly coplanar.
    res.append(coords["C"][2] / lower_plane_sigma_m)

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

    # Encourage paired anchors to stay roughly vertically aligned in XY.
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

    return np.array(res)


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
        "--height-sigma-mm",
        type=float,
        default=150.0,
        help="Assumed 1-sigma uncertainty for the plane-height prior.",
    )
    parser.add_argument(
        "--vertical-sigma-mm",
        type=float,
        default=500.0,
        help="Assumed 1-sigma XY offset allowed for vertical anchor pairs.",
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
    args = parser.parse_args()

    raw = load_input(Path(args.input))
    distances = load_distances(raw)
    reference_constraints = load_reference_constraints(args.reference_session)
    floating_reference_constraints = load_floating_reference_constraints(
        args.floating_reference_session
    )
    x0 = (
        build_initial_guess_from_layout(Path(args.initial_layout), floating_reference_constraints)
        if args.initial_layout
        else build_initial_guess(distances, floating_reference_constraints)
    )

    result = least_squares(
        residuals,
        x0,
        args=(
            distances,
            reference_constraints,
            floating_reference_constraints,
            args.height_prior_m,
            args.distance_sigma_mm / 1000.0,
            args.height_sigma_mm / 1000.0,
            args.vertical_sigma_mm / 1000.0,
            args.lower_plane_sigma_mm / 1000.0,
            args.upper_plane_sigma_mm / 1000.0,
            args.upper_level_sigma_mm / 1000.0,
            args.pair_height_sigma_mm / 1000.0,
            args.reference_sigma_mm / 1000.0,
            (
                None
                if args.floating_reference_z_prior_mm is None
                else args.floating_reference_z_prior_mm / 1000.0
            ),
            args.floating_reference_z_sigma_mm / 1000.0,
        ),
        max_nfev=8000,
        loss="soft_l1",
        f_scale=1.0,
        verbose=0,
    )
    coords, floating_reference_points = unpack_params(result.x, floating_reference_constraints)

    # Keep the upper plane positive in Z for readability.
    if coords["E"][2] < 0:
        for name in ANCHORS:
            coords[name] = coords[name] * np.array([1.0, 1.0, -1.0])

    rms = rms_mm(coords, distances)
    pair_report = vertical_pair_report(coords)

    serializable = {
        "units": "m",
        "solver": {
            "type": "constrained_least_squares_v4_soft_planes_levelled",
            "distance_sigma_mm": args.distance_sigma_mm,
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
            "termination_status": int(result.status),
            "message": result.message,
        },
        "rms_error_mm": rms,
        "anchors": {name: coords[name].round(6).tolist() for name in ANCHORS},
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
