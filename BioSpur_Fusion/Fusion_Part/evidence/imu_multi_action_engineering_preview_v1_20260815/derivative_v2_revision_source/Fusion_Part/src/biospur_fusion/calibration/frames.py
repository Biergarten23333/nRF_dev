"""Proper-rotation fitting and conservative frame observability gates."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import numpy as np


@dataclass(frozen=True)
class FrameFitResult:
    qualified: bool
    rank: int
    singular_values: tuple[float, float, float]
    condition: float | None
    R_N_from_V4: list[list[float]] | None
    yaw_observable: bool
    reason: str
    uncertainty_deg: float | None


def fit_proper_rotation(vectors_v4: np.ndarray, vectors_n: np.ndarray,
                        *, require_yaw: bool = False) -> FrameFitResult:
    source = np.asarray(vectors_v4, float); target = np.asarray(vectors_n, float)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3:
        raise ValueError("frame correspondences must be Nx3")
    source = source / np.linalg.norm(source, axis=1, keepdims=True)
    target = target / np.linalg.norm(target, axis=1, keepdims=True)
    cross = target.T @ source
    u, singular, vt = np.linalg.svd(cross)
    correction = np.eye(3); correction[-1, -1] = np.linalg.det(u @ vt)
    rotation = u @ correction @ vt
    rank = int(np.sum(singular > max(singular[0], 1.0) * 1e-6)) if singular.size else 0
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else float("inf")
    residual = target - (rotation @ source.T).T
    uncertainty = float(np.degrees(np.sqrt(np.mean(np.sum(residual * residual, axis=1)))))
    yaw_observable = rank >= 2
    qualified = rank >= 2 and (yaw_observable or not require_yaw) and np.linalg.det(rotation) > .999999
    return FrameFitResult(qualified, rank, tuple(float(x) for x in singular), condition,
                          rotation.tolist() if qualified else None, yaw_observable,
                          "PASS" if qualified else "INSUFFICIENT_INDEPENDENT_FRAME_CORRESPONDENCES",
                          uncertainty if qualified else None)


def current_capture_frame_gate() -> FrameFitResult:
    """Fail closed until natural-motion joint calibration supplies N/V4 pairs.

    T4 supplies V4 positions; independent Q1 frontends supply gravity and ten
    unrelated yaw gauges. Static body-chain directions define H, not N. They do
    not form a surveyed or dynamically identified V4->N correspondence set.
    """
    return FrameFitResult(
        False, 1, (1.0, 0.0, 0.0), None, None, False,
        "BLOCKED: gravity constrains roll/pitch per sensor, but no qualified joint natural-motion solution closes R_N<-V4 and all R_SB_i; H/T-Pose axes are display-only",
        None,
    )


def result_json(result: FrameFitResult) -> dict:
    return asdict(result)
