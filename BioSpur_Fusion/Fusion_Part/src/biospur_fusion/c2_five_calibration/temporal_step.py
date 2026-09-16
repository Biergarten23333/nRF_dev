"""Local numerical search direction; not an output filter or extra prior."""
import torch


def local_average_direction(parameters, valid):
    """Remove a three-sample component only where both neighbours are valid.

    The caller must evaluate trials against its complete objective and retain
    the original state as a candidate. This direction itself accepts nothing.
    Convex averaging preserves box-constrained bend coordinates. It must be
    applied to a continuous uniform parameter tape, never wrapped rotations.
    """
    if parameters.ndim != 2 or len(parameters) < 3 or not torch.isfinite(parameters).all():
        raise ValueError('finite continuous parameter tape required')
    valid = torch.as_tensor(valid, device=parameters.device)
    if valid.dtype != torch.bool or valid.shape != (len(parameters),):
        raise ValueError('one boolean validity per frame required')
    direction = torch.zeros_like(parameters)
    usable = valid[:-2] & valid[1:-1] & valid[2:]
    middle = .25*parameters[:-2] + .5*parameters[1:-1] + .25*parameters[2:]
    direction[1:-1] = torch.where(usable[:,None], middle-parameters[1:-1], 0.)
    return direction
