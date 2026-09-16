"""Optional low-dimensional *correction* to the moving learned thorax.

The knot spacing is an experimental regularizer, not measured biomechanics.
The underlying prior remains time-varying. No action name, measured parent,
or reference pose enters this parameterization. Default fitting stays free.
"""
import numpy as np
from scipy.interpolate import BSpline
from scipy.sparse.linalg import lsmr
import torch

from .operators import HZ


class TorsoBasis:
    def __init__(self, count, spacing_s):
        if count < 4 or not np.isfinite(spacing_s) or spacing_s <= 0:
            raise ValueError('at least four frames and positive finite knot spacing required')
        time = np.arange(count)/HZ
        knots = np.r_[np.zeros(4), np.arange(spacing_s, time[-1], spacing_s),
                      np.repeat(time[-1], 4)]
        # SciPy >=1.8 public API; sparse rows evaluate four cubic basis terms.
        self.matrix = BSpline.design_matrix(time, knots, 3)
        if not np.all(np.diff(self.matrix.indptr) == 4):
            raise ValueError('unexpected cubic B-spline support')
        self.indices = torch.as_tensor(self.matrix.indices.reshape(count, 4), dtype=torch.long)
        self.weights = torch.as_tensor(self.matrix.data.reshape(count, 4), dtype=torch.float64)
        self.spacing_s = float(spacing_s)

    def expand(self, controls):
        if controls.shape != (self.matrix.shape[1], 3):
            raise ValueError('three rotation-vector coordinates per torso control required')
        return (controls[self.indices.to(controls.device)]
                * self.weights.to(controls)[..., None]).sum(dim=1)

    def project(self, torso):
        if torso.shape != (self.matrix.shape[0], 3) or not torch.isfinite(torso).all():
            raise ValueError('one finite torso correction per original frame required')
        values = torso.detach().cpu().numpy()
        controls = np.column_stack([lsmr(self.matrix, values[:, i], atol=1e-12, btol=1e-12)[0]
                                    for i in range(3)])
        return torch.as_tensor(controls, dtype=torso.dtype, device=torso.device)


class PoseVariables:
    """Own pose variables, with optional retained-orientation residuals."""
    def __init__(self, parameters, torso_knot_spacing_s=None, *, parameter_count=9):
        if parameter_count not in (9,21) or parameters.ndim != 2 or parameters.shape[1] != parameter_count:
            raise ValueError('pose coordinate count does not match its objective owner')
        self.basis = (None if torso_knot_spacing_s is None
                      else TorsoBasis(len(parameters), torso_knot_spacing_s))
        if self.basis is None:
            self.full = parameters.detach().clone().requires_grad_()
            self.trainable = [self.full]
        else:
            self.controls = self.basis.project(parameters[:, :3]).requires_grad_()
            self.limbs = parameters[:, 3:].detach().clone().requires_grad_()
            self.trainable = [self.controls, self.limbs]

    def value(self):
        if self.basis is None:
            return self.full
        return torch.cat((self.basis.expand(self.controls), self.limbs), dim=-1)

    def clamp_flexion(self, maximum):
        with torch.no_grad():
            values = self.full[:, 3:7] if self.basis is None else self.limbs[:, :4]
            values.copy_(torch.minimum(values.clamp_min(0.), maximum))
