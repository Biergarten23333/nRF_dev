"""Four-heading quadrature for a non-equivariant learned pose prior.

Each member must run the same five streams and recurrent initial state in
its constant world-yaw gauge. Restore first, then take a chordal SO(3) mean.
This changes the learned prior, not measurements or anatomical calibration.
Member spread is model sensitivity, never independent measurement noise.
"""
import numpy as np
from scipy.spatial.transform import Rotation

YAWS_DEG = (0, 90, 180, 270)


def average_yaw_predictions(predictions):
    """Average all four raw-gauge global-rotation predictions, equally."""
    if set(predictions) != set(YAWS_DEG):
        raise ValueError('all four fixed yaw members required; no cherry-picking')
    restored = []
    shape = None
    for angle in YAWS_DEG:
        r = np.asarray(predictions[angle], dtype=float)
        if r.ndim != 4 or r.shape[1:] != (24, 3, 3) or not np.isfinite(r).all():
            raise ValueError('finite N x 24 proper rotations required')
        if shape is None:
            shape = r.shape
        if r.shape != shape:
            raise ValueError('all yaw members must share one pose grid')
        if (not np.allclose(r @ r.swapaxes(-1, -2), np.eye(3), atol=1e-5, rtol=0)
                or not np.allclose(np.linalg.det(r), 1., atol=1e-5, rtol=0)):
            raise ValueError('proper rotation predictions required')
        q = Rotation.from_euler('y', angle, degrees=True).as_matrix()
        restored.append(q.T @ r)
    restored = np.stack(restored)
    u, singular, vh = np.linalg.svd(restored.mean(axis=0))
    sign = np.linalg.det(u @ vh)
    # For positive determinant polar factor, uniqueness requires nonsingular
    # mean. For negative determinant, the smallest singular value must be
    # distinct to choose a unique axis to reflect back to SO(3).
    gap = np.where(sign < 0, singular[..., 1]-singular[..., 2], singular[..., 2])
    if np.any(gap < 1e-8):
        raise ValueError('ambiguous rotation mean; do not manufacture a pose')
    fix = np.ones_like(singular)
    fix[..., -1] = sign
    mean = (u * fix[..., None, :]) @ vh
    error = restored @ mean.swapaxes(-1, -2)
    angle = np.rad2deg(Rotation.from_matrix(error.reshape(-1, 3, 3)).magnitude())
    spread = angle.reshape(4, *shape[:2])
    return mean, dict(member_deviation_deg=spread,
                      meaning='correlated learned-model gauge sensitivity, not measurement covariance')
