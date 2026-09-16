"""Explicit mounting adapter; natural standing is not a segment zero pose.

The historical replay owner is sealed. Its right-multiplication slot is
adapted explicitly here without changing its source or the measured still
statistics used for common heading drift. Long axes remain qualitative wear
priors until independently observed; functional hinge lines do not supply them.
"""
from dataclasses import replace
import numpy as np
from biospur_fusion.c2_coupled_progressive.estimator import (
    _broad_mount, _limb_frame_from_vectors,
)


def calibration_with_mounts(calibration, mounts):
    if set(mounts) != set(calibration.initial_world_sensor):
        raise ValueError('one explicit mounting rotation required per segment')
    inverse = {}
    for segment, value in mounts.items():
        matrix = np.asarray(value, dtype=float)
        if (matrix.shape != (3, 3) or not np.isfinite(matrix).all()
                or not np.allclose(matrix.T@matrix, np.eye(3), atol=1e-10)
                or not np.isclose(np.linalg.det(matrix), 1, atol=1e-10)):
            raise ValueError('mount must be a proper rotation: '+segment)
        inverse[segment] = matrix.T.copy()
    # This legacy field is an inverse right-mount slot in downstream equations.
    # The measured initial means remain untouched in the caller's calibration.
    return replace(calibration, initial_world_sensor=inverse)


def functional_wear_mounts(native, episodes, calibration):
    axes, evidence = native.estimate_hinge_axes_olsson(episodes, calibration)
    sensor_axes = {}
    for joint, (parent_axis, child_axis) in axes.items():
        parent, child = native.HINGE_SEGMENTS[joint]
        sensor_axes[parent] = calibration.initial_world_sensor[parent].T @ parent_axis
        sensor_axes[child] = calibration.initial_world_sensor[child].T @ child_axis
    mounts = {}; audit = {}
    for segment in calibration.initial_world_sensor:
        axis = sensor_axes.get(segment)
        posterior = (_broad_mount(segment) if axis is None else
                     _limb_frame_from_vectors(segment, None, None, (axis, 1.0)))
        mounts[segment] = posterior.sensor_from_segment_mean
        audit[segment] = dict(sensor_from_segment=mounts[segment].tolist(),
            evidence=list(posterior.evidence), anatomical_mount_validated=False,
            long_axis_source='existing qualitative wear prior, NOT measured anatomy',
            hinge_axis_sensor=None if axis is None else axis.tolist())
    return mounts, dict(segments=audit, raw_functional_axes=evidence,
                       natural_standing_zero_used=False, scientific_pass=False,
                       role='FUNCTIONAL_AXIS_PLUS_WEAR_PRIOR_DIAGNOSTIC')
