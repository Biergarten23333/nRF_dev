"""Conditional matrix-Fisher pose factor for a frozen learned prior.

Uses -tr(F^T R), shifted by its optimum on SO(3). Normalization is constant
only while F is frozen. Concentration is learned confidence, not calibrated
sensor noise; callers must not add a second copy of the same pose prior.
"""
import torch


class FrozenFisherPrior:
    def __init__(self, parameter, joints, valid):
        self.parameter = torch.as_tensor(parameter, dtype=torch.float64).detach().clone()
        self.joints = tuple(joints)
        self.valid = torch.as_tensor(valid).clone()
        if (self.parameter.ndim != 4 or self.parameter.shape[1:] != (len(self.joints), 3, 3)
                or not self.joints or len(set(self.joints)) != len(self.joints)
                or any(not isinstance(j, int) or not 0 <= j < 24 for j in self.joints)
                or not torch.isfinite(self.parameter).all()
                or self.valid.dtype != torch.bool
                or self.valid.shape != self.parameter.shape[:1] or not self.valid.any()):
            raise ValueError('finite frame-by-joint Fisher parameters and valid support required')
        u, s, vh = torch.linalg.svd(self.parameter)
        sign = torch.linalg.det(u @ vh)
        self.optimum_trace = s[...,0] + s[...,1] + sign*s[...,2]

    def energy(self, rotation):
        if (rotation.shape != (len(self.parameter),24,3,3)
                or not torch.isfinite(rotation).all()):
            raise ValueError('finite global body rotations on the same time grid required')
        # No clamping: it would conceal sign/convention mistakes and gradients.
        score = (self.parameter.to(rotation) * rotation[:,self.joints]).sum((-1,-2))
        return (self.optimum_trace.to(rotation)-score)[self.valid.to(rotation.device)].mean()
