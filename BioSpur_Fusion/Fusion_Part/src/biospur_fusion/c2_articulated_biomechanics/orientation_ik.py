"""Analytic orientation IK for the four C2 hinge joints.

The native C2 stream measures every segment orientation at 200 Hz.  For an
elbow or knee, the angle between the proximal and distal long axes observes
the flexion magnitude without relying on the independently drifting absolute
headings.  The capture-calibrated functional hinge axis supplies the missing
anatomical bend plane.  FK then reconstructs the distal direction, while the
measured rotation about that distal long axis is retained.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from .model import (
    DOWN,
    HingeJoint,
    _long_axis_hinge_angle,
    _minimal_alignment,
    _rotation,
    _wxyz,
    hinge_coordinate_deg,
)


HINGE_ROM_TOLERANCE_DEG = 3e-6
HINGE_PROJECTION_NUMERIC_KERNEL = "matrix-rodrigues-float64-v1"


def _unsigned_bend_deg(parent: Rotation, child: Rotation) -> np.ndarray:
    parent_down = parent.apply(DOWN)
    child_down = child.apply(DOWN)
    return np.degrees(np.arccos(np.clip(
        np.sum(parent_down * child_down, axis=1), -1.0, 1.0
    )))


def reconstruct_distal_orientation(
    parent_q_wxyz: np.ndarray,
    child_q_wxyz: np.ndarray,
    flexion_deg: np.ndarray,
    joint: HingeJoint,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Set the hinge direction while retaining distal axial twist."""

    parent = _rotation(parent_q_wxyz)
    child = _rotation(child_q_wxyz)
    flexion = np.asarray(flexion_deg, dtype=float)
    if flexion.shape != (len(parent_q_wxyz),):
        raise ValueError("one flexion coordinate is required per frame")
    parent_down = parent.apply(DOWN)
    child_down = child.apply(DOWN)
    positive_axis_local = joint.positive_sign * np.asarray(joint.parent_axis)
    axis_world = parent.apply(np.repeat(
        positive_axis_local[None, :], len(flexion), axis=0
    ))
    target_down = Rotation.from_rotvec(
        axis_world * np.radians(flexion)[:, None]
    ).apply(parent_down)
    direction_correction = _minimal_alignment(child_down, target_down)
    corrected = direction_correction * child
    correction_deg = np.degrees(direction_correction.magnitude())
    residual = np.degrees(np.arccos(np.clip(
        np.sum(corrected.apply(DOWN) * target_down, axis=1), -1.0, 1.0
    )))
    return _wxyz(corrected), {
        "direction_correction_rms_deg": float(np.sqrt(np.mean(
            correction_deg * correction_deg
        ))),
        "direction_correction_p95_deg": float(np.quantile(correction_deg, 0.95)),
        "direction_correction_maximum_deg": float(np.max(correction_deg)),
        "fk_direction_residual_maximum_deg": float(np.max(residual)),
        "distal_axial_twist_policy": "preserved by shortest direction alignment",
    }


def solve_hinge_flexion_deg(
    parent_q_wxyz: np.ndarray,
    child_q_wxyz: np.ndarray,
    joint: HingeJoint,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Project a measured segment pair onto its anatomical hinge ROM."""

    observed = _unsigned_bend_deg(
        _rotation(parent_q_wxyz), _rotation(child_q_wxyz)
    )
    flexion = np.clip(observed, joint.minimum_deg, joint.maximum_deg)
    return flexion, {
        "frame_count": len(flexion),
        "observed_minimum_deg": float(np.min(observed)),
        "observed_maximum_deg": float(np.max(observed)),
        "flexion_minimum_deg": float(np.min(flexion)),
        "flexion_maximum_deg": float(np.max(flexion)),
        "below_rom_count": int(np.sum(observed < joint.minimum_deg)),
        "above_rom_count": int(np.sum(observed > joint.maximum_deg)),
        "source": "native-200-Hz proximal/distal segment long-axis angle",
    }


def apply_orientation_constrained_ik(
    trajectory: Mapping[str, Any],
    model: Mapping[str, HingeJoint],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply analytic hinge IK and return a renderer-compatible trajectory."""

    corrected: dict[str, Any] = {"trajectory": {}}
    metrics: dict[str, Any] = {}
    for episode, segments in trajectory["trajectory"].items():
        corrected["trajectory"][episode] = {
            segment: {
                field: np.array(value, copy=True)
                for field, value in row.items()
            }
            for segment, row in segments.items()
        }
        metrics[episode] = {}
        for name, joint in model.items():
            parent_row = corrected["trajectory"][episode][joint.parent]
            child_row = corrected["trajectory"][episode][joint.child]
            flexion, solve_metrics = solve_hinge_flexion_deg(
                parent_row["quat_world_segment_wxyz"],
                child_row["quat_world_segment_wxyz"],
                joint,
            )
            child_corrected, reconstruction_metrics = (
                reconstruct_distal_orientation(
                    parent_row["quat_world_segment_wxyz"],
                    child_row["quat_world_segment_wxyz"],
                    flexion,
                    joint,
                )
            )
            child_row["quat_world_segment_wxyz"] = child_corrected
            metrics[episode][name] = {
                **solve_metrics,
                **reconstruction_metrics,
                "flexion_deg": flexion,
            }
    if "output_coordinate_convention" in trajectory:
        corrected["output_coordinate_convention"] = {
            key: np.array(value, copy=True)
            if isinstance(value, np.ndarray)
            else value
            for key, value in trajectory["output_coordinate_convention"].items()
        }
    return corrected, metrics


def _project_hinge_corrections_scalar(
    base_rotations_world: Mapping[str, np.ndarray],
    correction_rotvec: Mapping[str, np.ndarray],
    model: Mapping[str, HingeJoint],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Project one articulated update through the public hinge/ROM owner.

    Raw-range IK is allowed to propose small rotations for every segment, but
    it must not create a second joint convention.  This function applies those
    proposals, invokes the same analytic hinge reconstruction used by the
    native-200 trajectory, and converts the projected absolute orientations
    back to right-multiplicative segment corrections.
    """

    base = {
        segment: np.asarray(rotation, dtype=float).reshape(3, 3)
        for segment, rotation in base_rotations_world.items()
    }
    corrections = {
        segment: np.asarray(value, dtype=float).reshape(3).copy()
        for segment, value in correction_rotvec.items()
    }
    if set(base) != set(corrections):
        raise ValueError("base rotations and corrections must own the same segments")
    if any(
        not np.all(np.isfinite(value))
        for collection in (base, corrections)
        for value in collection.values()
    ):
        raise ValueError("hinge projection inputs must be finite")

    absolute = {
        segment: base[segment] @ Rotation.from_rotvec(corrections[segment]).as_matrix()
        for segment in base
    }
    def one_wxyz(matrix: np.ndarray) -> np.ndarray:
        quaternion = Rotation.from_matrix(matrix).as_quat()
        return np.r_[quaternion[3], quaternion[:3]][None, :]

    joint_metrics: dict[str, Any] = {}
    for name, joint in model.items():
        parent_q = one_wxyz(absolute[joint.parent])
        child_q = one_wxyz(absolute[joint.child])
        pre_signed = float(hinge_coordinate_deg(parent_q, child_q, joint)[0])
        flexion, solve_metrics = solve_hinge_flexion_deg(
            parent_q, child_q, joint
        )
        child_projected_q, reconstruction = reconstruct_distal_orientation(
            parent_q, child_q, flexion, joint
        )
        absolute[joint.child] = _rotation(child_projected_q).as_matrix()[0]
        corrections[joint.child] = Rotation.from_matrix(
            base[joint.child].T @ absolute[joint.child]
        ).as_rotvec()
        post_child_q = one_wxyz(absolute[joint.child])
        post_signed = float(
            hinge_coordinate_deg(parent_q, post_child_q, joint)[0]
        )
        joint_metrics[name] = {
            "pre_projection_signed_deg": pre_signed,
            "post_projection_signed_deg": post_signed,
            "pre_projection_below_rom": bool(pre_signed < joint.minimum_deg),
            "pre_projection_above_rom": bool(pre_signed > joint.maximum_deg),
            "post_projection_inside_rom": bool(
                joint.minimum_deg - HINGE_ROM_TOLERANCE_DEG
                <= post_signed
                <= joint.maximum_deg + HINGE_ROM_TOLERANCE_DEG
            ),
            "flexion_deg": float(flexion[0]),
            "fk_direction_residual_deg": reconstruction[
                "fk_direction_residual_maximum_deg"
            ],
            "observed_unsigned_bend_deg": solve_metrics[
                "observed_maximum_deg"
            ],
        }
    all_inside = all(
        row["post_projection_inside_rom"] for row in joint_metrics.values()
    )
    maximum_residual = max(
        row["fk_direction_residual_deg"] for row in joint_metrics.values()
    )
    return corrections, {
        "joint": joint_metrics,
        "pre_projection_below_rom_count": sum(
            row["pre_projection_below_rom"] for row in joint_metrics.values()
        ),
        "pre_projection_above_rom_count": sum(
            row["pre_projection_above_rom"] for row in joint_metrics.values()
        ),
        "post_projection_all_inside_rom": all_inside,
        "fk_direction_residual_maximum_deg": maximum_residual,
    }


def _rodrigues_matrix(rotvec: np.ndarray) -> np.ndarray:
    """Convert an ``(N, 3)`` float64 rotation vector batch to matrices.

    ``sinc`` keeps both Rodrigues coefficients well-conditioned at zero, so
    the scalar and small-batch owners can use this exact same primitive for
    zero, tiny, ordinary, and near-pi rotations.
    """

    vectors = np.asarray(rotvec, dtype=np.float64).reshape(-1, 3)
    theta = np.linalg.norm(vectors, axis=1)
    skew = np.zeros((len(vectors), 3, 3), dtype=np.float64)
    skew[:, 0, 1] = -vectors[:, 2]
    skew[:, 0, 2] = vectors[:, 1]
    skew[:, 1, 0] = vectors[:, 2]
    skew[:, 1, 2] = -vectors[:, 0]
    skew[:, 2, 0] = -vectors[:, 1]
    skew[:, 2, 1] = vectors[:, 0]
    first = np.sinc(theta / np.pi)
    second = 0.5 * np.sinc(theta / (2.0 * np.pi)) ** 2
    identity = np.broadcast_to(np.eye(3, dtype=np.float64), skew.shape)
    return (
        identity
        + first[:, None, None] * skew
        + second[:, None, None] * np.matmul(skew, skew)
    )


def _apply_matrix(rotation: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """Apply one matrix per row without leaving the float64 matrix kernel."""

    return np.einsum("nij,nj->ni", rotation, vector)


def _signed_hinge_deg(
    parent: np.ndarray, child: np.ndarray, joint: HingeJoint
) -> np.ndarray:
    parent_down = _apply_matrix(parent, np.broadcast_to(DOWN, (len(parent), 3)))
    child_down = _apply_matrix(child, np.broadcast_to(DOWN, (len(child), 3)))
    positive_axis = joint.positive_sign * np.asarray(
        joint.parent_axis, dtype=np.float64
    )
    axis_world = _apply_matrix(
        parent, np.broadcast_to(positive_axis, (len(parent), 3))
    )
    sine = np.einsum("ni,ni->n", np.cross(parent_down, child_down), axis_world)
    cosine = np.einsum("ni,ni->n", parent_down, child_down)
    return np.degrees(np.arctan2(sine, cosine))


def _shortest_alignment_matrix(
    source: np.ndarray, target: np.ndarray
) -> np.ndarray:
    """Return the model owner's deterministic shortest-direction alignment."""

    cross = np.cross(source, target)
    sine = np.linalg.norm(cross, axis=1)
    cosine = np.einsum("ni,ni->n", source, target)
    axis = np.zeros_like(cross)
    regular = sine > 1e-10
    axis[regular] = cross[regular] / sine[regular, None]
    opposite = (~regular) & (cosine < 0.0)
    if np.any(opposite):
        candidate = np.cross(source[opposite], np.array([1.0, 0.0, 0.0]))
        weak = np.linalg.norm(candidate, axis=1) < 1e-8
        candidate[weak] = np.cross(
            source[opposite][weak], np.array([0.0, 1.0, 0.0])
        )
        candidate /= np.linalg.norm(candidate, axis=1)[:, None]
        axis[opposite] = candidate
    angle = np.arctan2(sine, cosine)
    angle[opposite] = np.pi
    return _rodrigues_matrix(axis * angle[:, None])


def project_hinge_rotation_pairs(parent, child, positive_axes, minimum_deg, maximum_deg):
    """Vectorized numeric owner for independent parent/child matrix pairs.

    The regular public projector and its sparse derivative evaluator share
    this kernel; batching independent joints does not introduce another ROM
    or bend-plane convention. Inputs have one matrix/axis/bound per row.
    """
    down = np.broadcast_to(DOWN, (len(parent), 3))
    parent_down = _apply_matrix(parent, down)
    child_down = _apply_matrix(child, down)
    axis_world = _apply_matrix(parent, positive_axes)
    cross = np.cross(parent_down, child_down)
    dot = np.einsum('ni,ni->n', parent_down, child_down)
    pre_signed = np.degrees(np.arctan2(np.einsum('ni,ni->n', cross, axis_world), dot))
    observed = np.degrees(np.arctan2(np.linalg.norm(cross, axis=1), dot))
    flexion = np.clip(observed, minimum_deg, maximum_deg)
    target_down = _apply_matrix(
        _rodrigues_matrix(axis_world * np.radians(flexion)[:, None]), parent_down)
    child_projected = np.matmul(_shortest_alignment_matrix(child_down, target_down), child)
    corrected_down = _apply_matrix(child_projected, down)
    post_signed = np.degrees(np.arctan2(
        np.einsum('ni,ni->n', np.cross(parent_down, corrected_down), axis_world),
        np.einsum('ni,ni->n', parent_down, corrected_down)))
    residual = np.degrees(np.arctan2(
        np.linalg.norm(np.cross(corrected_down, target_down), axis=1),
        np.einsum('ni,ni->n', corrected_down, target_down)))
    return child_projected, pre_signed, post_signed, flexion, residual, observed


def _project_hinge_corrections_matrix_kernel(
    base_rotations_world: Mapping[str, np.ndarray],
    correction_rotvec_batch: Mapping[str, np.ndarray],
    model: Mapping[str, HingeJoint],
    *,
    varying_base: bool,
) -> tuple[list[dict[str, np.ndarray]], list[dict[str, Any]]]:
    """Authoritative float64 four-hinge projection for one to sixteen rows."""

    base = {
        segment: np.asarray(rotation, dtype=np.float64).reshape(
            (-1, 3, 3) if varying_base else (3, 3)
        )
        for segment, rotation in base_rotations_world.items()
    }
    corrections = {
        segment: np.asarray(value, dtype=np.float64).reshape(-1, 3).copy()
        for segment, value in correction_rotvec_batch.items()
    }
    if set(base) != set(corrections):
        raise ValueError("base rotations and corrections must own the same segments")
    counts = {len(value) for value in corrections.values()}
    if varying_base:
        counts.update(len(value) for value in base.values())
    if len(counts) != 1:
        raise ValueError("batched hinge corrections must own one sample count")
    count = counts.pop()
    if count < 1:
        raise ValueError("hinge projection batch must contain at least one row")
    if varying_base and count > 16:
        raise ValueError("varying-base hinge projection batch exceeds 16 rows")
    if any(
        not np.all(np.isfinite(value))
        for collection in (base, corrections)
        for value in collection.values()
    ):
        raise ValueError("hinge projection inputs must be finite")

    absolute: dict[str, np.ndarray] = {}
    for segment in base:
        delta = _rodrigues_matrix(corrections[segment])
        absolute[segment] = np.matmul(
            base[segment] if varying_base else base[segment][None, :, :],
            delta,
        )

    metrics: list[dict[str, Any]] = [dict() for _ in range(count)]
    projected_children: list[str] = []
    for name, joint in model.items():
        parent = absolute[joint.parent]
        child = absolute[joint.child]
        positive_axis = joint.positive_sign * np.asarray(
            joint.parent_axis, dtype=np.float64
        )
        child_projected, pre_signed, post_signed, flexion, residual, observed = project_hinge_rotation_pairs(
            parent, child, np.broadcast_to(positive_axis, (count, 3)),
            joint.minimum_deg, joint.maximum_deg)
        absolute[joint.child] = child_projected
        if joint.child not in projected_children:
            projected_children.append(joint.child)
        for index in range(count):
            metrics[index][name] = {
                "pre_projection_signed_deg": float(pre_signed[index]),
                "post_projection_signed_deg": float(post_signed[index]),
                "pre_projection_below_rom": bool(
                    pre_signed[index] < joint.minimum_deg
                ),
                "pre_projection_above_rom": bool(
                    pre_signed[index] > joint.maximum_deg
                ),
                "post_projection_inside_rom": bool(
                    joint.minimum_deg - HINGE_ROM_TOLERANCE_DEG
                    <= post_signed[index]
                    <= joint.maximum_deg + HINGE_ROM_TOLERANCE_DEG
                ),
                "flexion_deg": float(flexion[index]),
                "fk_direction_residual_deg": float(residual[index]),
                "observed_unsigned_bend_deg": float(observed[index]),
            }

    # Rodrigues owns the hot path.  A single batched logarithm at the public
    # correction-vector boundary retains SciPy's reliable near-pi convention.
    if projected_children:
        relative = np.stack([
            np.matmul(
                np.swapaxes(base[segment], 1, 2)
                if varying_base else base[segment].T[None, :, :],
                absolute[segment],
            )
            for segment in projected_children
        ])
        logarithm = Rotation.from_matrix(
            relative.reshape(-1, 3, 3)
        ).as_rotvec().reshape(len(projected_children), count, 3)
        for child_index, segment in enumerate(projected_children):
            corrections[segment] = logarithm[child_index]

    projected = [
        {segment: corrections[segment][index].copy() for segment in corrections}
        for index in range(count)
    ]
    projections = []
    for joint_rows in metrics:
        projections.append({
            "joint": joint_rows,
            "pre_projection_below_rom_count": sum(
                row["pre_projection_below_rom"] for row in joint_rows.values()
            ),
            "pre_projection_above_rom_count": sum(
                row["pre_projection_above_rom"] for row in joint_rows.values()
            ),
            "post_projection_all_inside_rom": all(
                row["post_projection_inside_rom"] for row in joint_rows.values()
            ),
            "fk_direction_residual_maximum_deg": max(
                row["fk_direction_residual_deg"] for row in joint_rows.values()
            ),
            "numeric_kernel": HINGE_PROJECTION_NUMERIC_KERNEL,
        })
    return projected, projections


def _project_hinge_corrections_one_frame(
    base_rotations_world: Mapping[str, np.ndarray],
    correction_rotvec: Mapping[str, np.ndarray],
    model: Mapping[str, HingeJoint],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Project one row through the authoritative small-batch kernel."""

    projected, metrics = _project_hinge_corrections_matrix_kernel(
        base_rotations_world,
        correction_rotvec,
        model,
        varying_base=False,
    )
    if len(projected) != 1:
        raise ValueError("one-frame hinge projection requires exactly one row")
    return projected[0], metrics[0]


def project_hinge_corrections(
    base_rotations_world: Mapping[str, np.ndarray],
    correction_rotvec: Mapping[str, np.ndarray],
    model: Mapping[str, HingeJoint],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Project one frame through the authoritative float64 matrix kernel."""

    return _project_hinge_corrections_one_frame(
        base_rotations_world, correction_rotvec, model,
    )


def _evaluate_hinge_projection_batch(
    base_rotations_world: Mapping[str, np.ndarray],
    correction_rotvec_batch: Mapping[str, np.ndarray],
    model: Mapping[str, HingeJoint],
    *, varying_base: bool,
) -> tuple[list[dict[str, np.ndarray]], list[dict[str, Any]]]:
    """Evaluate independent rows with the same owner used by scalar calls."""

    return _project_hinge_corrections_matrix_kernel(
        base_rotations_world,
        correction_rotvec_batch,
        model,
        varying_base=varying_base,
    )


def evaluate_hinge_projection_batch(
    base_rotations_world: Mapping[str, np.ndarray],
    correction_rotvec_batch: Mapping[str, np.ndarray],
    model: Mapping[str, HingeJoint],
) -> tuple[list[dict[str, np.ndarray]], list[dict[str, Any]]]:
    """Evaluate correction rows against one common base pose."""

    return _evaluate_hinge_projection_batch(
        base_rotations_world, correction_rotvec_batch, model,
        varying_base=False,
    )


def evaluate_varying_base_hinge_projection_batch(
    base_rotations_world_batch: Mapping[str, np.ndarray],
    correction_rotvec_batch: Mapping[str, np.ndarray],
    model: Mapping[str, HingeJoint],
) -> tuple[list[dict[str, np.ndarray]], list[dict[str, Any]]]:
    """Project up to sixteen independent per-frame base/correction rows."""

    return _evaluate_hinge_projection_batch(
        base_rotations_world_batch, correction_rotvec_batch, model,
        varying_base=True,
    )
