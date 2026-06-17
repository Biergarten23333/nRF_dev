"""Label-based layout alignment and error metrics for Phase 0.

The coordinate convention is fixed by the Erlangen OptiTrack truth export:
X/Z are horizontal and Y is vertical.  This module has no import-time side
effects and does not call any AutoPos solver.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np


Layout = Mapping[str, Mapping[str, float]]


def _anchors_dict(layout: Mapping[str, Any]) -> Layout:
    if "anchors" in layout and isinstance(layout["anchors"], Mapping):
        return layout["anchors"]  # type: ignore[return-value]
    return layout  # type: ignore[return-value]


def _point_for_anchor(layout: Layout, anchor: str) -> np.ndarray:
    item = layout[anchor]
    try:
        return np.array([item["x_mm"], item["y_mm"], item["z_mm"]], dtype=float)
    except KeyError:
        return np.array([item["x"], item["y"], item["z"]], dtype=float)


def _ordered_points(
    solved_layout: Mapping[str, Any],
    truth_layout: Mapping[str, Any],
    anchors: list[str] | None,
) -> tuple[list[str], np.ndarray, np.ndarray]:
    solved = _anchors_dict(solved_layout)
    truth = _anchors_dict(truth_layout)
    if anchors is None:
        anchors = sorted(set(solved.keys()) & set(truth.keys()))
    missing_solved = [a for a in anchors if a not in solved]
    missing_truth = [a for a in anchors if a not in truth]
    if missing_solved or missing_truth:
        raise ValueError(f"missing anchors: solved={missing_solved}, truth={missing_truth}")
    if len(anchors) != 8:
        raise ValueError(f"expected exactly 8 matched anchors, got {len(anchors)}")
    solved_pts = np.vstack([_point_for_anchor(solved, a) for a in anchors])
    truth_pts = np.vstack([_point_for_anchor(truth, a) for a in anchors])
    return anchors, solved_pts, truth_pts


def _fit_umeyama(
    source: np.ndarray,
    target: np.ndarray,
    *,
    similarity: bool,
    allow_reflection: bool,
) -> dict[str, Any]:
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3:
        raise ValueError("source and target must both have shape Nx3")
    n = source.shape[0]
    src_mean = source.mean(axis=0)
    tgt_mean = target.mean(axis=0)
    src_c = source - src_mean
    tgt_c = target - tgt_mean
    covariance = (src_c.T @ tgt_c) / n
    u, singular_values, vt = np.linalg.svd(covariance)
    d = np.ones(3)
    if not allow_reflection and np.linalg.det(vt.T @ u.T) < 0:
        d[-1] = -1.0
    rotation = vt.T @ np.diag(d) @ u.T
    if similarity:
        variance = float(np.sum(src_c * src_c) / n)
        if variance <= 0.0:
            raise ValueError("source layout has zero variance")
        scale = float(np.sum(singular_values * d) / variance)
    else:
        scale = 1.0
    translation = tgt_mean - scale * (src_mean @ rotation.T)
    aligned = scale * (source @ rotation.T) + translation
    return {
        "scale": scale,
        "rotation": rotation,
        "translation": translation,
        "aligned": aligned,
        "det_rotation": float(np.linalg.det(rotation)),
    }


def _error_summary(residuals: np.ndarray) -> dict[str, Any]:
    norms = np.linalg.norm(residuals, axis=1)
    horizontal = np.linalg.norm(residuals[:, [0, 2]], axis=1)
    vertical = np.abs(residuals[:, 1])
    return {
        "total": {
            "rms_mm": float(math.sqrt(np.mean(norms * norms))),
            "median_mm": float(np.median(norms)),
        },
        "horizontal_xz": {
            "rms_mm": float(math.sqrt(np.mean(horizontal * horizontal))),
            "median_mm": float(np.median(horizontal)),
        },
        "vertical_y": {
            "rms_mm": float(math.sqrt(np.mean(vertical * vertical))),
            "median_mm": float(np.median(vertical)),
        },
    }


def align_layout_metrics(
    solved_layout: Mapping[str, Any],
    truth_layout: Mapping[str, Any],
    *,
    anchors: list[str] | None = None,
    allow_reflection: bool = True,
) -> dict[str, Any]:
    """Align a solved 8-anchor layout to truth and report decomposed errors.

    Args:
        solved_layout: Mapping keyed by anchor label, or a JSON-style object with
            an ``anchors`` mapping. Coordinates may be ``x_mm/y_mm/z_mm`` or
            ``x/y/z``.
        truth_layout: Same structure as ``solved_layout``.
        anchors: Optional explicit anchor labels. If omitted, the sorted common
            label set is used. Exactly eight labels are required.
        allow_reflection: Keep true by default because range-only AutoPos layout
            recovery has a handedness ambiguity.

    Returns:
        A JSON-serializable report containing rigid and similarity alignment
        metrics plus rigid-minus-similarity scale contribution terms.
    """
    labels, solved_pts, truth_pts = _ordered_points(solved_layout, truth_layout, anchors)
    modes: dict[str, Any] = {}
    for mode, similarity in (("rigid", False), ("similarity", True)):
        fit = _fit_umeyama(
            solved_pts,
            truth_pts,
            similarity=similarity,
            allow_reflection=allow_reflection,
        )
        residuals = fit["aligned"] - truth_pts
        modes[mode] = {
            "scale": fit["scale"],
            "det_rotation": fit["det_rotation"],
            "translation_mm": fit["translation"].tolist(),
            "rotation": fit["rotation"].tolist(),
            "errors": _error_summary(residuals),
            "per_anchor": {
                label: {
                    "residual_x_mm": float(residuals[i, 0]),
                    "residual_y_vertical_mm": float(residuals[i, 1]),
                    "residual_z_mm": float(residuals[i, 2]),
                    "residual_3d_mm": float(np.linalg.norm(residuals[i])),
                    "residual_horizontal_xz_mm": float(np.linalg.norm(residuals[i, [0, 2]])),
                    "residual_vertical_y_abs_mm": float(abs(residuals[i, 1])),
                }
                for i, label in enumerate(labels)
            },
        }
    rigid_errors = modes["rigid"]["errors"]
    similarity_errors = modes["similarity"]["errors"]
    scale_contribution = {
        "total": {
            "rms_mm": rigid_errors["total"]["rms_mm"] - similarity_errors["total"]["rms_mm"],
            "median_mm": rigid_errors["total"]["median_mm"] - similarity_errors["total"]["median_mm"],
        },
        "horizontal_xz": {
            "rms_mm": rigid_errors["horizontal_xz"]["rms_mm"]
            - similarity_errors["horizontal_xz"]["rms_mm"],
            "median_mm": rigid_errors["horizontal_xz"]["median_mm"]
            - similarity_errors["horizontal_xz"]["median_mm"],
        },
    }
    return {
        "anchors": labels,
        "convention": "Y is vertical; horizontal error is computed in the X/Z plane. Units are mm.",
        "allow_reflection": allow_reflection,
        "modes": modes,
        "scale_contribution": scale_contribution,
    }


def _rotation_z(theta_rad: float) -> np.ndarray:
    c = math.cos(theta_rad)
    s = math.sin(theta_rad)
    return np.array(
        [
            [c, -s, 0.0],
            [s, c, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=float,
    )


def _self_test() -> None:
    labels = list("ABCDEFGH")
    truth_pts = np.array(
        [
            [-1000.0, 100.0, -1500.0],
            [-1200.0, 120.0, 1100.0],
            [900.0, 110.0, 1200.0],
            [1150.0, 105.0, -1400.0],
            [-1050.0, 1600.0, -1450.0],
            [-1220.0, 1620.0, 1150.0],
            [950.0, 1580.0, 1180.0],
            [1100.0, 1590.0, -1500.0],
        ],
        dtype=float,
    )
    expected_scale = 0.925
    rotation = _rotation_z(math.radians(17.0))
    translation = np.array([320.0, -210.0, 87.0], dtype=float)
    solved_pts = ((truth_pts - translation) @ rotation) / expected_scale
    perturb = np.array(
        [
            [2.0, 0.0, -1.0],
            [-1.0, 1.5, 0.0],
            [0.5, -2.0, 1.0],
            [-2.0, 0.5, 0.5],
            [1.0, -1.0, -0.5],
            [-0.5, 2.0, -1.0],
            [1.5, -0.5, 1.5],
            [-1.5, -0.5, -0.5],
        ],
        dtype=float,
    )
    solved_pts = solved_pts + perturb
    truth = {
        label: {"x_mm": float(p[0]), "y_mm": float(p[1]), "z_mm": float(p[2])}
        for label, p in zip(labels, truth_pts)
    }
    solved = {
        label: {"x_mm": float(p[0]), "y_mm": float(p[1]), "z_mm": float(p[2])}
        for label, p in zip(labels, solved_pts)
    }
    report = align_layout_metrics(solved, truth, anchors=labels, allow_reflection=False)
    recovered_scale = report["modes"]["similarity"]["scale"]
    if abs(recovered_scale - expected_scale) > 5e-4:
        raise AssertionError(
            f"scale recovery failed: expected {expected_scale}, got {recovered_scale}"
        )
    rigid_total = report["modes"]["rigid"]["errors"]["total"]["rms_mm"]
    sim_total = report["modes"]["similarity"]["errors"]["total"]["rms_mm"]
    rigid_horizontal = report["modes"]["rigid"]["errors"]["horizontal_xz"]["rms_mm"]
    sim_horizontal = report["modes"]["similarity"]["errors"]["horizontal_xz"]["rms_mm"]
    if not (rigid_total > sim_total * 10.0):
        raise AssertionError(f"rigid total error did not exceed similarity error: {rigid_total} vs {sim_total}")
    if not (rigid_horizontal > sim_horizontal * 10.0):
        raise AssertionError(
            f"rigid horizontal error did not exceed similarity error: {rigid_horizontal} vs {sim_horizontal}"
        )
    if report["scale_contribution"]["total"]["rms_mm"] <= 0.0:
        raise AssertionError("total scale contribution should be positive")
    if report["scale_contribution"]["horizontal_xz"]["rms_mm"] <= 0.0:
        raise AssertionError("horizontal scale contribution should be positive")
    print(
        "layout_align_metrics self-test PASS: "
        f"scale={recovered_scale:.6f}, "
        f"rigid_rms={rigid_total:.3f} mm, "
        f"similarity_rms={sim_total:.3f} mm"
    )


if __name__ == "__main__":
    _self_test()
