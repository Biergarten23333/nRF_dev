"""Conditional constant sensor-bias block of the shared C2 inertial equation.

The response comes from the raw frontend, including filtering/resampling.
This block does not infer bias independently of pose, or supply a covariance.
"""
import numpy as np
import torch

from .acceleration_metric import metric_residual
from .operators import multiscale
from .solver import acceleration_residual, valid_support


class ConstantBiasProfile:
    """Eliminate one session-wide bias vector, retaining pose derivatives.

    The design must already use the caller's residual metric and row order.
    It is fixed only while raw sensor orientation/heading are fixed. Rebuild
    after those change. This unregularized diagnostic is not a bias estimate
    to publish, a noise model, or a posterior covariance.
    """
    def __init__(self, design, *, rtol=1e-10):
        matrix = torch.as_tensor(design, dtype=torch.float64).detach()
        if (matrix.ndim != 2 or matrix.shape[1] != 15 or not len(matrix)
                or not torch.isfinite(matrix).all()
                or not np.isfinite(rtol) or rtol <= 0):
            raise ValueError('finite session-wide fifteen-column bias design required')
        u, singular, vh = torch.linalg.svd(matrix, full_matrices=False)
        supported = singular > rtol * singular.max()
        self.basis = u[:, supported]
        self.inverse = vh[supported].T / singular[supported]
        self.rows = len(matrix)
        self.rank = int(supported.sum())

    def evaluate(self, residual):
        """Return profiled residual and minimizing constant bias increment.

        For predicted-minus-measured r, minimize ||r + A db||². No dense
        row-by-row projection matrix and no frame-dependent bias is created.
        """
        if (residual.shape != (self.rows,) or not torch.isfinite(residual).all()
                or residual.dtype != self.basis.dtype
                or residual.device != self.basis.device):
            raise ValueError('matching finite float64 residual vector required')
        coefficients = self.basis.T @ residual
        return residual - self.basis @ coefficients, -self.inverse @ coefficients


def bias_system(rotation, acceleration, bias_response, valid, geometry, levers):
    """Return A, b with residual(new_bias) = A @ delta_bias - b.

    Positive bias increments subtract from measured acceleration, hence add
    to predicted-minus-measured residual. Columns are five sensor XYZ biases.
    Targets are conditional on frozen pose, mounting, heading and lever arms.
    """
    r, a, response = [torch.as_tensor(x, dtype=torch.float64)
                      for x in (rotation, acceleration, bias_response)]
    if (response.shape != (len(r), 5, 3, 3) or a.shape != (len(r), 5, 3)
            or not torch.isfinite(response).all()):
        raise ValueError('five finite raw-transport bias responses required per frame')
    valid = np.asarray(valid)
    if valid.shape != (len(r),) or valid.dtype != bool:
        raise ValueError('bias system requires boolean sample validity')
    keep = valid_support(valid)
    if not keep.any():
        raise ValueError('bias system has no complete derivative support')
    filtered_response = multiscale(response, acceleration=True)
    matrix = torch.zeros((*filtered_response.shape[:2], 4, 3, 15), dtype=r.dtype)
    for node in range(1, 5):
        matrix[:, :, node-1, :, :3] = -filtered_response[:, :, 0]
        matrix[:, :, node-1, :, node*3:node*3+3] = filtered_response[:, :, node]
    residual = acceleration_residual(r, a, geometry, levers)
    matrix = metric_residual(matrix, geometry, node_axis=2)[keep]
    residual = metric_residual(residual, geometry, node_axis=2)[keep]
    return matrix.numpy().reshape(-1, 15), -residual.numpy().ravel()
