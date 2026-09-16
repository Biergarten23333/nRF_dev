"""Initial neutral pelvis convention, separate from anatomical mounting truth."""
import numpy as np
from scipy.spatial.transform import Rotation


def initial_neutral_correction(pelvis):
    """Right correction from arrived 00 rotations; preserve horizontal heading.

    Caller owns phase/time provenance. This defines an upright neutral reference,
    not a measured anatomical pelvic tilt or an inference about the arms.
    """
    initial = pelvis.mean()
    forward = initial.as_matrix()[:, 0]
    if np.linalg.norm(forward[:2]) < 1e-6:
        raise ValueError('neutral forward direction has no horizontal support')
    yaw = Rotation.from_euler('z', np.arctan2(forward[1], forward[0]))
    return initial.inv()*yaw
