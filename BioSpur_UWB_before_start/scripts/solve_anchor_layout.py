#!/usr/bin/env python3
import argparse
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


def build_initial_guess(distances: dict[tuple[str, str], float]) -> np.ndarray:
    ab = distances[("A", "B")]
    ad = distances[("A", "D")]
    h = 1.4
    return np.array(
        [
            ab,  # Bx
            ab, ad,  # Cx, Cy
            0.0, ad,  # Dx, Dy
            0.0, 0.0,  # Ex, Ey
            ab, 0.0,  # Fx, Fy
            ab, ad,  # Gx, Gy
            0.0, ad,  # Hx, Hy
            h,  # shared upper-plane height
        ],
        dtype=float,
    )


def unpack_params(params: np.ndarray) -> dict[str, np.ndarray]:
    idx = 0
    coords = {
        "A": np.array([0.0, 0.0, 0.0]),
        "B": np.array([params[idx], 0.0, 0.0]),
    }
    idx += 1

    coords["C"] = np.array([params[idx], params[idx + 1], 0.0])
    idx += 2

    coords["D"] = np.array([params[idx], params[idx + 1], 0.0])
    idx += 2

    ex, ey = params[idx], params[idx + 1]
    idx += 2
    fx, fy = params[idx], params[idx + 1]
    idx += 2
    gx, gy = params[idx], params[idx + 1]
    idx += 2
    hx, hy = params[idx], params[idx + 1]
    idx += 2
    h = params[idx]

    coords["E"] = np.array([ex, ey, h])
    coords["F"] = np.array([fx, fy, h])
    coords["G"] = np.array([gx, gy, h])
    coords["H"] = np.array([hx, hy, h])
    return coords


def residuals(
    params: np.ndarray,
    distances: dict[tuple[str, str], float],
    plane_height_prior_m: float,
    distance_sigma_m: float,
    height_sigma_m: float,
    vertical_sigma_m: float,
) -> np.ndarray:
    coords = unpack_params(params)
    res = []

    # Primary ranging residuals.
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            target = distances[(a, b)]
            actual = np.linalg.norm(coords[a] - coords[b])
            res.append((actual - target) / distance_sigma_m)

    # Shared upper-plane height prior.
    upper_height = coords["E"][2]
    res.append((upper_height - plane_height_prior_m) / height_sigma_m)

    # Encourage paired anchors to stay roughly vertically aligned in XY.
    for lower, upper in VERTICAL_PAIRS:
        dx = coords[upper][0] - coords[lower][0]
        dy = coords[upper][1] - coords[lower][1]
        res.append(dx / vertical_sigma_m)
        res.append(dy / vertical_sigma_m)

    # Encourage the lower plane to keep a right-handed orientation.
    # This avoids accidental mirror/flip solutions around the A-B axis.
    dy = coords["D"][1]
    res.append(min(0.0, dy) / 0.05)

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
    args = parser.parse_args()

    raw = load_input(Path(args.input))
    distances = load_distances(raw)
    x0 = build_initial_guess(distances)

    result = least_squares(
        residuals,
        x0,
        args=(
            distances,
            args.height_prior_m,
            args.distance_sigma_mm / 1000.0,
            args.height_sigma_mm / 1000.0,
            args.vertical_sigma_mm / 1000.0,
        ),
        max_nfev=8000,
        loss="soft_l1",
        f_scale=1.0,
        verbose=0,
    )
    coords = unpack_params(result.x)

    # Keep the upper plane positive in Z for readability.
    if coords["E"][2] < 0:
        for name in ANCHORS:
            coords[name] = coords[name] * np.array([1.0, 1.0, -1.0])

    rms = rms_mm(coords, distances)
    pair_report = vertical_pair_report(coords)

    serializable = {
        "units": "m",
        "solver": {
            "type": "constrained_least_squares_v2",
            "distance_sigma_mm": args.distance_sigma_mm,
            "height_prior_m": args.height_prior_m,
            "height_sigma_mm": args.height_sigma_mm,
            "vertical_sigma_mm": args.vertical_sigma_mm,
            "termination_status": int(result.status),
            "message": result.message,
        },
        "rms_error_mm": rms,
        "anchors": {name: coords[name].round(6).tolist() for name in ANCHORS},
        "vertical_pairs": pair_report,
    }
    Path(args.output).write_text(json.dumps(serializable, indent=2) + "\n")

    print(f"RMS error: {rms:.2f} mm")
    print(f"Shared upper-plane height: {coords['E'][2]:.3f} m")
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
