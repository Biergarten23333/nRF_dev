"""Semantics of the released DTP contact head, not a ground-contact sensor.

The pinned upstream data_generation._foot_ground_probs labels displacement
of SMPL joints 10/11, without testing ground height. Consumers must not turn
its probability directly into a hard foot/shank zero-velocity observation.
"""
from .upstream import REVISION


def contact_head_contract():
    return dict(
        source='IMUCoCo/data_generation.py::_foot_ground_probs',
        upstream_revision=REVISION,
        smpl_point_joints=[10,11],
        label_displacement_threshold_m=.008,
        label_sample_rate_hz=60.,
        equivalent_speed_threshold_mps=.48,
        label_tests_ground_height=False,
        label_is_zero_velocity=False,
        probability_calibration_verified=False,
        independent_measurement=False,
        shank_stationarity_implied=False,
        interpretation='learned slow-foot-point likelihood; not verified stationary support')
