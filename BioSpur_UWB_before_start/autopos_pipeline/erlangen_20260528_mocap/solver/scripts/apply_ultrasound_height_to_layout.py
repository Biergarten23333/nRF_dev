#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize


REPO = Path(__file__).resolve().parents[4]
FIELD_ROOT = REPO / "autopos_pipeline" / "erlangen_20260528_mocap"
LOWER_ANCHORS = ("A", "B", "C", "D")
UPPER_ANCHORS = ("E", "F", "G", "H")
DEFAULT_US_ANCHORS = ("F", "G", "H")
PASS_RMS_MM = 50.0
PASS_MAX_MM = 80.0
MIN_PHYSICAL_Z_MM = -20.0


def newest(paths: list[Path]) -> Path | None:
    paths = [p for p in paths if p.exists()]
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime)


def find_session_from_staged(staged: Path) -> Path | None:
    manifest = staged / "stage_manifest.json"
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None
    session = data.get("session")
    if not session:
        return None
    p = Path(session)
    return p if p.exists() else None


def find_ultrasound_csv(session: Path, anchor: str) -> Path | None:
    anchor = anchor.upper()
    candidates: list[Path] = []
    candidates.extend(session.glob(f"us_*_{anchor}_*/ultrasound_{anchor}.csv"))
    candidates.extend(session.glob(f"us_*_FGH_*/ultrasound_{anchor}.csv"))
    candidates.extend(session.glob(f"**/ultrasound_{anchor}.csv"))
    return newest(sorted(set(candidates)))


def read_ultrasound_height(csv_path: Path) -> dict[str, Any]:
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)

    usable = []
    for row in rows:
        try:
            ant_center = float(row.get("median_ant_center_mm") or "")
            median = float(row.get("median_mm") or "")
            offset = float(row.get("ant_center_offset_mm") or 0.0)
        except ValueError:
            continue
        if ant_center > 0:
            usable.append((row, median, offset, ant_center))
    if not usable:
        raise SystemExit(f"no usable median_ant_center_mm in {csv_path}")

    done = [item for item in usable if (item[0].get("state") or "").upper() == "DONE"]
    row, median, offset, ant_center = done[-1] if done else usable[-1]
    return {
        "source_csv": str(csv_path.resolve()),
        "timestamp": row.get("timestamp") or "",
        "state": row.get("state") or "",
        "median_mm": median,
        "ant_center_offset_mm": offset,
        "height_ant_center_mm": ant_center,
        "raw": row.get("raw") or "",
    }


def anchor_entries(layout: dict[str, Any]) -> list[dict[str, Any]]:
    anchors = layout.get("anchors")
    if not isinstance(anchors, list):
        raise SystemExit("layout anchors must be a list")
    return anchors


def layout_points(anchors: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for a in anchors:
        label = (a.get("label") or "").upper()
        out[label] = np.array(
            [float(a["x_mm"]), float(a["y_mm"]), float(a["z_mm"])],
            dtype=float,
        )
    return out


def choose_initial_z_axis(points: dict[str, np.ndarray]) -> tuple[np.ndarray, dict[str, float]]:
    missing = [label for label in LOWER_ANCHORS + UPPER_ANCHORS if label not in points]
    if missing:
        raise SystemExit(f"layout missing anchors for z convention check: {','.join(missing)}")

    lower_mean_raw = float(np.mean([points[label][2] for label in LOWER_ANCHORS]))
    upper_mean_raw = float(np.mean([points[label][2] for label in UPPER_ANCHORS]))
    # Final physical coordinates should obey lower layer below upper layer.
    sign = 1.0 if lower_mean_raw < upper_mean_raw else -1.0
    return np.array([0.0, 0.0, sign], dtype=float), {
        "lower_mean_raw_z_mm": lower_mean_raw,
        "upper_mean_raw_z_mm": upper_mean_raw,
    }


def unit_from_angles(theta: float, phi: float) -> np.ndarray:
    return np.array(
        [
            math.sin(theta) * math.cos(phi),
            math.sin(theta) * math.sin(phi),
            math.cos(theta),
        ],
        dtype=float,
    )


def angles_from_unit(n: np.ndarray) -> tuple[float, float]:
    n = n / np.linalg.norm(n)
    theta = math.acos(float(np.clip(n[2], -1.0, 1.0)))
    phi = math.atan2(float(n[1]), float(n[0]))
    return theta, phi


def fit_z_axis(
    points: np.ndarray,
    heights: np.ndarray,
    initial_n: np.ndarray,
    all_points_by_label: dict[str, np.ndarray],
) -> tuple[np.ndarray, float, np.ndarray]:
    def residual_for_angles(x: np.ndarray) -> float:
        n = unit_from_angles(float(x[0]), float(x[1]))
        z = points @ n
        shift = float(np.mean(heights - z))
        residual = z + shift - heights
        return float(np.sum(residual * residual))

    def evaluate(x: np.ndarray) -> dict[str, Any]:
        n = unit_from_angles(float(x[0]), float(x[1]))
        z = points @ n
        shift = float(np.mean(heights - z))
        residual = z + shift - heights
        lower_mean = float(np.mean([all_points_by_label[label] @ n + shift for label in LOWER_ANCHORS]))
        upper_mean = float(np.mean([all_points_by_label[label] @ n + shift for label in UPPER_ANCHORS]))
        return {
            "n": n,
            "shift": shift,
            "residual": residual,
            "rss": float(np.sum(residual * residual)),
            "lower_mean": lower_mean,
            "upper_mean": upper_mean,
        }

    initial = np.array(angles_from_unit(initial_n), dtype=float)
    candidates = []
    for start in (
        initial,
        np.array(angles_from_unit(-initial_n), dtype=float),
        np.array([math.pi / 2.0, 0.0], dtype=float),
        np.array([math.pi / 2.0, math.pi / 2.0], dtype=float),
    ):
        res = minimize(residual_for_angles, start, method="Nelder-Mead")
        candidates.append(evaluate(np.asarray(res.x, dtype=float)))

    # F/G/H are nearly coplanar and can admit two near-zero-residual height
    # planes.  Residual alone may select the physically inverted solution, so
    # first require the known layer convention, then minimize residual.
    valid = [c for c in candidates if c["lower_mean"] < c["upper_mean"]]
    best = min(valid or candidates, key=lambda c: c["rss"])
    return best["n"], best["shift"], best["residual"]


def build_rotation_rows(n: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    e3 = n / np.linalg.norm(n)
    preferred = np.array([1.0, 0.0, 0.0], dtype=float)
    if abs(float(np.dot(preferred, e3))) > 0.95:
        preferred = np.array([0.0, 1.0, 0.0], dtype=float)
    e1 = preferred - np.dot(preferred, e3) * e3
    e1 = e1 / np.linalg.norm(e1)
    e2 = np.cross(e3, e1)
    e2 = e2 / np.linalg.norm(e2)
    return e1, e2, e3


def apply_height(
    layout_path: Path,
    ultrasound: dict[str, dict[str, Any]],
    out_path: Path,
    anchors_used: tuple[str, ...],
) -> dict[str, Any]:
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    anchors = anchor_entries(layout)
    points_by_label = layout_points(anchors)
    initial_n, convention = choose_initial_z_axis(points_by_label)

    missing = [a for a in anchors_used if a not in points_by_label or a not in ultrasound]
    if missing:
        raise SystemExit(f"missing ultrasound/layout anchors for: {','.join(missing)}")

    fit_points = np.stack([points_by_label[a] for a in anchors_used], axis=0)
    heights = np.array([float(ultrasound[a]["height_ant_center_mm"]) for a in anchors_used], dtype=float)
    n, z_shift, residual = fit_z_axis(fit_points, heights, initial_n, points_by_label)
    e1, e2, e3 = build_rotation_rows(n)

    all_points = np.stack([points_by_label[(a.get("label") or "").upper()] for a in anchors], axis=0)
    rotated_xy = np.stack([all_points @ e1, all_points @ e2], axis=1)
    raw_xy = all_points[:, :2]
    xy_shift = np.mean(raw_xy, axis=0) - np.mean(rotated_xy, axis=0)

    corrected = json.loads(json.dumps(layout))
    fitted_rows = []
    for label, target_height, err in zip(anchors_used, heights, residual, strict=True):
        fitted_rows.append(
            {
                "anchor": label,
                "target_z_mm": float(target_height),
                "fitted_z_mm": float(target_height + err),
                "residual_mm": float(err),
                "source_csv": ultrasound[label]["source_csv"],
            }
        )

    for a in anchor_entries(corrected):
        label = (a.get("label") or "").upper()
        raw = points_by_label[label]
        a["x_mm_raw_autopos"] = float(raw[0])
        a["y_mm_raw_autopos"] = float(raw[1])
        a["z_mm_raw_autopos"] = float(raw[2])
        a["x_mm"] = float(np.dot(raw, e1) + xy_shift[0])
        a["y_mm"] = float(np.dot(raw, e2) + xy_shift[1])
        a["z_mm"] = float(np.dot(raw, e3) + z_shift)

    rms = float(np.sqrt(np.mean(residual * residual)))
    max_abs = float(np.max(np.abs(residual)))
    lower_mean = float(
        np.mean([next(a for a in anchor_entries(corrected) if a["label"] == label)["z_mm"] for label in LOWER_ANCHORS])
    )
    upper_mean = float(
        np.mean([next(a for a in anchor_entries(corrected) if a["label"] == label)["z_mm"] for label in UPPER_ANCHORS])
    )
    aligned_z_by_label = {
        (a.get("label") or "").upper(): float(a["z_mm"])
        for a in anchor_entries(corrected)
    }
    min_aligned_z = float(min(aligned_z_by_label.values()))
    physical_z_ok = min_aligned_z >= MIN_PHYSICAL_Z_MM
    fail_reasons = []
    if rms > PASS_RMS_MM:
        fail_reasons.append("ultrasound_rms_residual_high")
    if max_abs > PASS_MAX_MM:
        fail_reasons.append("ultrasound_max_residual_high")
    if lower_mean >= upper_mean:
        fail_reasons.append("lower_not_below_upper")
    if not physical_z_ok:
        fail_reasons.append("negative_physical_z_after_us_alignment")
    status = "pass" if not fail_reasons else "fail"

    corrected.setdefault("extra", {})
    corrected["extra"]["ultrasound_height_alignment"] = {
        "anchors_used": list(anchors_used),
        "method": "rigid_pitch_roll_z_best_fit",
        "z_model": "z_corrected_mm = dot(z_axis_unit, raw_xyz_mm) + z_shift_mm",
        "z_convention": "mean_z(ABCD) < mean_z(EFGH)",
        "status": status,
        "rms_residual_mm": rms,
        "max_residual_mm": max_abs,
        "pass_thresholds": {
            "rms_residual_mm": PASS_RMS_MM,
            "max_residual_mm": PASS_MAX_MM,
            "min_physical_z_mm": MIN_PHYSICAL_Z_MM,
        },
        "z_axis_unit_in_raw_frame": [float(v) for v in e3],
        "x_axis_unit_in_raw_frame": [float(v) for v in e1],
        "y_axis_unit_in_raw_frame": [float(v) for v in e2],
        "z_shift_mm": z_shift,
        "xy_shift_mm": [float(xy_shift[0]), float(xy_shift[1])],
        **convention,
        "lower_mean_aligned_z_mm": lower_mean,
        "upper_mean_aligned_z_mm": upper_mean,
        "min_aligned_z_mm": min_aligned_z,
        "physical_z_ok": physical_z_ok,
        "fail_reasons": fail_reasons,
        "residuals": fitted_rows,
        "ultrasound": ultrasound,
        "note": "Post-process coordinate-frame alignment only. Inter-anchor solve residuals are unchanged.",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(corrected, indent=2) + "\n", encoding="utf-8")
    return corrected["extra"]["ultrasound_height_alignment"]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Post-process a solver layout into a physical z-up frame using F/G/H ultrasound heights."
    )
    ap.add_argument("--layout", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--staged", default=str(FIELD_ROOT / "solver" / "work" / "field_dataset_staged"))
    ap.add_argument("--session", default=None)
    ap.add_argument("--anchors", default="F,G,H")
    args = ap.parse_args()

    layout = Path(args.layout)
    out = Path(args.out) if args.out else layout.with_name("layout_us_height.json")
    staged = Path(args.staged)
    if not staged.is_absolute():
        staged = FIELD_ROOT / "solver" / "work" / staged

    session = Path(args.session) if args.session else find_session_from_staged(staged)
    if session is None:
        raise SystemExit("cannot infer session; pass --session")

    anchors_used = tuple(a.strip().upper() for a in args.anchors.split(",") if a.strip())
    if len(anchors_used) < 3:
        raise SystemExit("at least three ultrasound anchors are required")

    ultrasound: dict[str, dict[str, Any]] = {}
    for anchor in anchors_used:
        csv_path = find_ultrasound_csv(session, anchor)
        if csv_path is None or not csv_path.exists():
            raise SystemExit(f"ultrasound csv not found for anchor {anchor}")
        ultrasound[anchor] = read_ultrasound_height(csv_path)

    meta = apply_height(layout, ultrasound, out, anchors_used)
    print(f"[ok] wrote {out}")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
