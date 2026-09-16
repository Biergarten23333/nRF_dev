"""Fixture-stage skeleton-conditioned implicit RF shadow features.

The field is a smooth, dimensionless geometry descriptor.  It is not a LOS
classifier, does not consume the current range, and is not wired into the
production range solver.  Seven global, side-symmetric morphology parameters
control effective transverse scales and non-negative attenuation amplitudes;
no manual limb circumference is an input.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import numpy as np
INTEGRATION_METHOD = "analytic_finite_ray_gaussian_with_longitudinal_taper"
INTEGRATION_REFERENCE_ABSOLUTE_TOLERANCE = 2e-8
INTEGRATION_REFERENCE_RELATIVE_TOLERANCE = 2e-6
LONGITUDINAL_SIGMOID_SHARPNESS = 12.0

MORPHOLOGY_PARAMETER_NAMES = (
    "torso_lateral_scale",
    "torso_ap_scale",
    "torso_opacity_m",
    "arm_transverse_scale",
    "leg_transverse_scale",
    "arm_opacity_m",
    "leg_opacity_m",
)
MORPHOLOGY_BOUNDS = {
    "torso_lateral_scale": (0.05, 1.00),
    "torso_ap_scale": (0.05, 1.00),
    "torso_opacity_m": (0.0, 2.0),
    "arm_transverse_scale": (0.01, 0.75),
    "leg_transverse_scale": (0.01, 0.75),
    "arm_opacity_m": (0.0, 2.0),
    "leg_opacity_m": (0.0, 2.0),
}

ARM_SEGMENTS = (
    ("upper_arm_left", "shoulder_left", "elbow_left"),
    ("forearm_left", "elbow_left", "wrist_left"),
    ("upper_arm_right", "shoulder_right", "elbow_right"),
    ("forearm_right", "elbow_right", "wrist_right"),
)
LEG_SEGMENTS = (
    ("thigh_left", "hip_left", "knee_left"),
    ("shank_left", "knee_left", "ankle_left"),
    ("thigh_right", "hip_right", "knee_right"),
    ("shank_right", "knee_right", "ankle_right"),
)
REQUIRED_LANDMARKS = frozenset({
    "pelvis_center", "shoulder_mid", "shoulder_left", "shoulder_right",
    "hip_left", "hip_right", "elbow_left", "elbow_right", "wrist_left",
    "wrist_right", "knee_left", "knee_right", "ankle_left", "ankle_right",
})
FORBIDDEN_MODEL_LANDMARK_TOKENS = ("head", "hand", "foot", "toe", "heel")

EMITTER_LOCAL_SEGMENTS = {
    "BSF31CC": ("torso",),
    "BSFC2CC": ("torso",),
    "BSFAA61": ("upper_arm_left", "forearm_left"),
    "BSF1120": ("upper_arm_right", "forearm_right"),
    "BSFEC35": ("forearm_left",),
    "BSFB165": ("forearm_right",),
    "BSF44AD": ("thigh_left", "shank_left"),
    "BSF3C79": ("thigh_right", "shank_right"),
    "BSF6C53": ("shank_left",),
    "BSF8BC4": ("shank_right",),
}

NUISANCE_NAMES = (
    "intercept",
    "own_facing_score",
    "log_predicted_path_length_m",
    "quality_sqrt_fraction_minus_one",
    "t_round_over_10000us",
    *(f"node_sumzero_{index}" for index in range(9)),
    *(f"anchor_sumzero_{index}" for index in range(7)),
)


@dataclass(frozen=True)
class ShadowMorphology:
    torso_lateral_scale: float
    torso_ap_scale: float
    torso_opacity_m: float
    arm_transverse_scale: float
    leg_transverse_scale: float
    arm_opacity_m: float
    leg_opacity_m: float

    def __post_init__(self) -> None:
        for name in MORPHOLOGY_PARAMETER_NAMES:
            value = float(getattr(self, name))
            lower, upper = MORPHOLOGY_BOUNDS[name]
            if not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"{name} is outside its frozen finite bounds")

    def vector(self) -> np.ndarray:
        return np.asarray([getattr(self, name) for name in MORPHOLOGY_PARAMETER_NAMES])


@dataclass(frozen=True)
class ShadowFeatures:
    torso_exposure: float
    arm_exposure: float
    leg_exposure: float
    arm_incremental_exposure: float
    leg_incremental_exposure: float
    total_occupancy_exposure: float
    local_emitter_segments: tuple[str, ...]
    large_field_unobservable_local: bool
    small_field_local_segment_excluded: bool

    def __post_init__(self) -> None:
        for value in (
            self.torso_exposure, self.arm_exposure, self.leg_exposure,
            self.arm_incremental_exposure, self.leg_incremental_exposure,
            self.total_occupancy_exposure,
        ):
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("shadow exposure must lie in [0, 1]")
        if self.torso_exposure + self.arm_incremental_exposure + self.leg_incremental_exposure > self.total_occupancy_exposure + 2e-8:
            raise ValueError("non-overlapping component exposures exceed occupancy")


@dataclass(frozen=True)
class CompiledShadowGeometry:
    """Immutable causal geometry, reusable across morphology optimizer calls."""

    tag_origin_m: np.ndarray
    ray_vector_m: np.ndarray
    landmarks: Mapping[str, np.ndarray]
    local_emitter_segments: tuple[str, ...]

    def __post_init__(self) -> None:
        tag = np.array(self.tag_origin_m, dtype=float, copy=True)
        ray = np.array(self.ray_vector_m, dtype=float, copy=True)
        if tag.shape != (3,) or ray.shape != (3,):
            raise ValueError("compiled integration geometry has invalid shape")
        if not np.all(np.isfinite(tag)) or not np.all(np.isfinite(ray)):
            raise ValueError("compiled integration geometry is non-finite")
        if float(np.linalg.norm(ray)) <= np.finfo(float).eps:
            raise ValueError("compiled tag-anchor ray is degenerate")
        tag.setflags(write=False)
        ray.setflags(write=False)
        copied_landmarks = {}
        for name, value in self.landmarks.items():
            point = np.array(value, dtype=float, copy=True)
            if point.shape != (3,) or not np.all(np.isfinite(point)):
                raise ValueError(f"invalid compiled landmark: {name}")
            point.setflags(write=False)
            copied_landmarks[name] = point
        object.__setattr__(self, "tag_origin_m", tag)
        object.__setattr__(self, "ray_vector_m", ray)
        object.__setattr__(self, "landmarks", copied_landmarks)


def _point(name: str, landmarks: Mapping[str, np.ndarray]) -> np.ndarray:
    if name not in landmarks:
        raise ValueError(f"missing skeleton landmark: {name}")
    value = np.asarray(landmarks[name], dtype=float)
    if value.shape != (3,) or not np.all(np.isfinite(value)):
        raise ValueError(f"invalid skeleton landmark: {name}")
    return value


def _validate_geometry(
    tag_origin_m: np.ndarray,
    anchor_position_m: np.ndarray,
    landmarks: Mapping[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    tag = np.asarray(tag_origin_m, dtype=float)
    anchor = np.asarray(anchor_position_m, dtype=float)
    if tag.shape != (3,) or anchor.shape != (3,) or not (
        np.all(np.isfinite(tag)) and np.all(np.isfinite(anchor))
    ):
        raise ValueError("tag and anchor must be finite 3-vectors")
    ray = anchor - tag
    if float(np.linalg.norm(ray)) <= np.finfo(float).eps:
        raise ValueError("tag-anchor ray is degenerate")
    if not REQUIRED_LANDMARKS.issubset(landmarks):
        raise ValueError("skeleton landmark set is incomplete")
    return tag, anchor, ray


def _sigmoid(value: np.ndarray) -> np.ndarray:
    positive = value >= 0.0
    output = np.empty_like(value, dtype=float)
    output[positive] = 1.0 / (1.0 + np.exp(-value[positive]))
    exponential = np.exp(value[~positive])
    output[~positive] = exponential / (1.0 + exponential)
    return output


def _longitudinal_taper(raw_fraction: np.ndarray) -> np.ndarray:
    sharpness = LONGITUDINAL_SIGMOID_SHARPNESS
    taper = _sigmoid(sharpness * raw_fraction) * _sigmoid(
        sharpness * (1.0 - raw_fraction)
    )
    if np.any((taper < 0.0) | (taper > 1.0)):
        raise RuntimeError("longitudinal taper escaped [0, 1]")
    return taper


def _segment_field(
    ray_points: np.ndarray,
    start: np.ndarray,
    stop: np.ndarray,
    transverse_scale: float,
) -> np.ndarray:
    axis = np.asarray(stop, dtype=float) - np.asarray(start, dtype=float)
    length = float(np.linalg.norm(axis))
    if not math.isfinite(length) or length <= 1e-9:
        raise ValueError("body segment length is degenerate")
    unit = axis / length
    delta = ray_points - start[None, :]
    raw_fraction = delta @ unit / length
    radial = delta - (delta @ unit)[:, None] * unit[None, :]
    width = float(transverse_scale) * length
    if not math.isfinite(width) or width <= 0.0:
        raise ValueError("effective transverse scale is not positive")
    field = np.exp(-0.5 * np.sum(radial * radial, axis=1) / (width * width))
    field *= _longitudinal_taper(raw_fraction)
    if not np.all(np.isfinite(field)) or np.any((field < 0.0) | (field > 1.0)):
        raise RuntimeError("segment field escaped [0, 1]")
    return field


def _torso_field(
    ray_points: np.ndarray,
    landmarks: Mapping[str, np.ndarray],
    lateral_scale: float,
    ap_scale: float,
) -> np.ndarray:
    pelvis = _point("pelvis_center", landmarks)
    shoulder_mid = _point("shoulder_mid", landmarks)
    axis = shoulder_mid - pelvis
    length = float(np.linalg.norm(axis))
    if length <= 1e-9:
        raise ValueError("torso length is degenerate")
    longitudinal = axis / length
    shoulder_span = _point("shoulder_right", landmarks) - _point(
        "shoulder_left", landmarks
    )
    hip_span = _point("hip_right", landmarks) - _point("hip_left", landmarks)
    lateral_raw = 0.5 * (shoulder_span + hip_span)
    lateral_raw -= longitudinal * float(lateral_raw @ longitudinal)
    reference_span = 0.5 * (
        float(np.linalg.norm(shoulder_span)) + float(np.linalg.norm(hip_span))
    )
    lateral_norm = float(np.linalg.norm(lateral_raw))
    if min(reference_span, lateral_norm) <= 1e-9:
        raise ValueError("torso lateral landmarks are degenerate")
    lateral = lateral_raw / lateral_norm
    ap = np.cross(longitudinal, lateral)
    ap_norm = float(np.linalg.norm(ap))
    if ap_norm <= 1e-9:
        raise ValueError("torso AP frame is degenerate")
    ap /= ap_norm
    delta = ray_points - pelvis[None, :]
    raw_fraction = delta @ longitudinal / length
    lateral_width = float(lateral_scale) * reference_span
    ap_width = float(ap_scale) * reference_span
    if min(lateral_width, ap_width) <= 0.0:
        raise ValueError("torso scale is not positive")
    exponent = (
        (delta @ lateral / lateral_width) ** 2
        + (delta @ ap / ap_width) ** 2
    )
    field = np.exp(-0.5 * exponent) * _longitudinal_taper(raw_fraction)
    if not np.all(np.isfinite(field)) or np.any((field < 0.0) | (field > 1.0)):
        raise RuntimeError("torso field escaped [0, 1]")
    return field


def _bounded_union(fields: list[np.ndarray], row_count: int) -> np.ndarray:
    if not fields:
        return np.zeros(row_count, dtype=float)
    values = np.vstack(fields)
    if np.any((values < 0.0) | (values > 1.0)):
        raise ValueError("union member escaped [0, 1]")
    union = 1.0 - np.prod(1.0 - values, axis=0)
    return np.clip(union, 0.0, 1.0)


def _integrate_gaussian_quadratic(a: float, b: float, c: float) -> float:
    """Analytically integrate exp(-(a*s^2+b*s+c)) over s in [0,1]."""

    if not all(math.isfinite(value) for value in (a, b, c)) or a < 0.0:
        raise ValueError("invalid Gaussian-ray quadratic")
    if a <= 1e-14:
        value = math.exp(-c)
    else:
        root = math.sqrt(a)
        center = -b / (2.0 * a)
        log_scale = -(c - b * b / (4.0 * a))
        value = (
            math.exp(min(0.0, log_scale))
            * math.sqrt(math.pi)
            / (2.0 * root)
            * (math.erf(root * (1.0 - center)) - math.erf(-root * center))
        )
    if not math.isfinite(value) or not -1e-12 <= value <= 1.0 + 1e-12:
        raise RuntimeError("analytic Gaussian-ray integral escaped [0,1]")
    return min(1.0, max(0.0, value))


def _segment_ray_exposure(
    tag: np.ndarray,
    ray: np.ndarray,
    start: np.ndarray,
    stop: np.ndarray,
    transverse_scale: float,
) -> float:
    axis = stop - start
    length = float(np.linalg.norm(axis))
    if length <= 1e-9:
        raise ValueError("body segment length is degenerate")
    unit = axis / length
    width = float(transverse_scale) * length
    if not math.isfinite(width) or width <= 0.0:
        raise ValueError("effective transverse scale is not positive")
    projector = np.eye(3) - np.outer(unit, unit)
    q0 = projector @ (tag - start)
    q1 = projector @ ray
    inverse_width2 = 1.0 / (width * width)
    a = 0.5 * float(q1 @ q1) * inverse_width2
    b = float(q0 @ q1) * inverse_width2
    c = 0.5 * float(q0 @ q0) * inverse_width2
    radial_integral = _integrate_gaussian_quadratic(a, b, c)
    midpoint = 0.5 * (start + stop)
    ray_fraction = float((midpoint - tag) @ ray / (ray @ ray))
    closest_to_midpoint = tag + ray_fraction * ray
    longitudinal_fraction = float((closest_to_midpoint - start) @ unit / length)
    taper = float(_longitudinal_taper(np.asarray([longitudinal_fraction]))[0])
    return radial_integral * taper


def _torso_ray_exposure(
    tag: np.ndarray,
    ray: np.ndarray,
    landmarks: Mapping[str, np.ndarray],
    lateral_scale: float,
    ap_scale: float,
) -> float:
    pelvis = _point("pelvis_center", landmarks)
    shoulder_mid = _point("shoulder_mid", landmarks)
    axis = shoulder_mid - pelvis
    length = float(np.linalg.norm(axis))
    if length <= 1e-9:
        raise ValueError("torso length is degenerate")
    longitudinal = axis / length
    shoulder_span = _point("shoulder_right", landmarks) - _point("shoulder_left", landmarks)
    hip_span = _point("hip_right", landmarks) - _point("hip_left", landmarks)
    lateral_raw = 0.5 * (shoulder_span + hip_span)
    lateral_raw -= longitudinal * float(lateral_raw @ longitudinal)
    reference_span = 0.5 * (float(np.linalg.norm(shoulder_span)) + float(np.linalg.norm(hip_span)))
    lateral_norm = float(np.linalg.norm(lateral_raw))
    if min(reference_span, lateral_norm) <= 1e-9:
        raise ValueError("torso lateral landmarks are degenerate")
    lateral = lateral_raw / lateral_norm
    ap = np.cross(longitudinal, lateral)
    ap_norm = float(np.linalg.norm(ap))
    if ap_norm <= 1e-9:
        raise ValueError("torso AP frame is degenerate")
    ap /= ap_norm
    lateral_width = float(lateral_scale) * reference_span
    ap_width = float(ap_scale) * reference_span
    if min(lateral_width, ap_width) <= 0.0:
        raise ValueError("torso scale is not positive")
    d0 = tag - pelvis
    q0_lateral, q1_lateral = float(d0 @ lateral), float(ray @ lateral)
    q0_ap, q1_ap = float(d0 @ ap), float(ray @ ap)
    a = 0.5 * ((q1_lateral / lateral_width) ** 2 + (q1_ap / ap_width) ** 2)
    b = q0_lateral * q1_lateral / lateral_width**2 + q0_ap * q1_ap / ap_width**2
    c = 0.5 * ((q0_lateral / lateral_width) ** 2 + (q0_ap / ap_width) ** 2)
    radial_integral = _integrate_gaussian_quadratic(a, b, c)
    midpoint = 0.5 * (pelvis + shoulder_mid)
    ray_fraction = float((midpoint - tag) @ ray / (ray @ ray))
    closest_to_midpoint = tag + ray_fraction * ray
    longitudinal_fraction = float((closest_to_midpoint - pelvis) @ longitudinal / length)
    taper = float(_longitudinal_taper(np.asarray([longitudinal_fraction]))[0])
    return radial_integral * taper


def compile_shadow_geometry(
    *,
    tag_origin_m: np.ndarray,
    anchor_position_m: np.ndarray,
    landmarks: Mapping[str, np.ndarray],
    emitter_node: str,
) -> CompiledShadowGeometry:
    """Compile a range-independent quadrature grid once per causal ray."""

    if emitter_node not in EMITTER_LOCAL_SEGMENTS:
        raise ValueError("unknown C2 emitter node")
    tag, _, ray = _validate_geometry(tag_origin_m, anchor_position_m, landmarks)
    return CompiledShadowGeometry(
        tag_origin_m=tag,
        ray_vector_m=ray,
        landmarks={name: _point(name, landmarks) for name in REQUIRED_LANDMARKS},
        local_emitter_segments=EMITTER_LOCAL_SEGMENTS[emitter_node],
    )


def shadow_features_from_compiled(
    geometry: CompiledShadowGeometry,
    morphology: ShadowMorphology,
) -> ShadowFeatures:
    tag = geometry.tag_origin_m
    ray = geometry.ray_vector_m
    landmarks = geometry.landmarks
    local = geometry.local_emitter_segments
    large_unobservable = "torso" in local
    torso = (
        0.0
        if large_unobservable
        else _torso_ray_exposure(
            tag, ray, landmarks, morphology.torso_lateral_scale,
            morphology.torso_ap_scale,
        )
    )
    arm_fields = [
        _segment_ray_exposure(tag, ray, _point(start, landmarks), _point(stop, landmarks),
                              morphology.arm_transverse_scale)
        for segment, start, stop in ARM_SEGMENTS if segment not in local
    ]
    leg_fields = [
        _segment_ray_exposure(tag, ray, _point(start, landmarks), _point(stop, landmarks),
                              morphology.leg_transverse_scale)
        for segment, start, stop in LEG_SEGMENTS if segment not in local
    ]
    arm = float(_bounded_union([np.asarray([value]) for value in arm_fields], 1)[0])
    leg = float(_bounded_union([np.asarray([value]) for value in leg_fields], 1)[0])
    arm_share = arm * (1.0 - 0.5 * leg)
    leg_share = leg * (1.0 - 0.5 * arm)
    arm_increment = (1.0 - torso) * arm_share
    leg_increment = (1.0 - torso) * leg_share
    occupancy = torso + arm_increment + leg_increment
    values = np.asarray([torso, arm, leg, arm_increment, leg_increment, occupancy])
    values = np.clip(values, 0.0, 1.0)
    return ShadowFeatures(
        torso_exposure=float(values[0]), arm_exposure=float(values[1]),
        leg_exposure=float(values[2]), arm_incremental_exposure=float(values[3]),
        leg_incremental_exposure=float(values[4]),
        total_occupancy_exposure=float(values[5]),
        local_emitter_segments=tuple(local),
        large_field_unobservable_local=large_unobservable,
        small_field_local_segment_excluded=any(item != "torso" for item in local),
    )


def shadow_features(
    *,
    tag_origin_m: np.ndarray,
    anchor_position_m: np.ndarray,
    landmarks: Mapping[str, np.ndarray],
    emitter_node: str,
    morphology: ShadowMorphology,
) -> ShadowFeatures:
    """Return causal geometric features without accepting any range value."""

    return shadow_features_from_compiled(
        compile_shadow_geometry(
            tag_origin_m=tag_origin_m,
            anchor_position_m=anchor_position_m,
            landmarks=landmarks,
            emitter_node=emitter_node,
        ),
        morphology,
    )


def nested_shadow_shift_m(
    features: ShadowFeatures,
    morphology: ShadowMorphology,
) -> dict[str, float]:
    """Return exactly nested B0/B1/B2 non-negative positive-tail shifts."""

    b0 = 0.0
    b1 = morphology.torso_opacity_m * features.torso_exposure
    b2 = (
        b1
        + morphology.arm_opacity_m * features.arm_incremental_exposure
        + morphology.leg_opacity_m * features.leg_incremental_exposure
    )
    if not all(math.isfinite(value) and value >= 0.0 for value in (b0, b1, b2)):
        raise RuntimeError("nested RF shadow shift is invalid")
    if b2 > max(
        morphology.torso_opacity_m,
        morphology.arm_opacity_m,
        morphology.leg_opacity_m,
    ) + 2e-8:
        raise RuntimeError("non-additive RF shadow shift exceeded opacity bound")
    return {"B0": b0, "B1": b1, "B2": b2}


def _sum_zero_contrast(index: int, count: int) -> np.ndarray:
    if not 0 <= int(index) < int(count) or count < 2:
        raise ValueError("categorical index is outside fixed contrast basis")
    output = np.zeros(count - 1, dtype=float)
    if index == count - 1:
        output[:] = -1.0
    else:
        output[index] = 1.0
    return output


def common_nuisance_vector(
    *,
    node_index: int,
    anchor_index: int,
    own_facing_score: float,
    predicted_path_length_m: float,
    quality: float,
    t_round_us: float,
) -> np.ndarray:
    """Frozen B0 nuisance basis, shared byte-for-byte by B1 and B2.

    The path length is a held-link LOO prediction, not the current measured
    range.  There is no action/time intercept, pair interaction, or adaptive
    spatial basis.
    """

    scalar = np.asarray([
        own_facing_score,
        predicted_path_length_m,
        quality,
        t_round_us,
    ], dtype=float)
    if not np.all(np.isfinite(scalar)):
        raise ValueError("nuisance inputs must be finite")
    if not -1.0 <= own_facing_score <= 1.0:
        raise ValueError("own-facing score is outside [-1, 1]")
    if predicted_path_length_m <= 0.0 or quality <= 0.0 or t_round_us < 0.0:
        raise ValueError("nuisance scale input is outside its physical domain")
    vector = np.r_[
        1.0,
        own_facing_score,
        math.log(predicted_path_length_m / 1.0),
        math.sqrt(quality / 100.0) - 1.0,
        t_round_us / 10_000.0,
        _sum_zero_contrast(node_index, 10),
        _sum_zero_contrast(anchor_index, 8),
    ]
    if len(vector) != len(NUISANCE_NAMES):
        raise RuntimeError("nuisance basis dimension drifted")
    return vector


def nested_design_vectors(
    nuisance: np.ndarray,
    features: ShadowFeatures,
    morphology: ShadowMorphology,
) -> dict[str, np.ndarray]:
    """Expose strict nesting while preserving an identical nuisance prefix."""

    common = np.asarray(nuisance, dtype=float)
    if common.shape != (len(NUISANCE_NAMES),) or not np.all(np.isfinite(common)):
        raise ValueError("invalid common nuisance vector")
    shifts = nested_shadow_shift_m(features, morphology)
    return {
        "B0": common.copy(),
        "B1": np.r_[common, shifts["B1"]],
        "B2": np.r_[common, shifts["B1"], shifts["B2"] - shifts["B1"]],
    }
