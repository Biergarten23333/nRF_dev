"""Shared-only factor rows, evaluated once per complete C2 system."""
import torch
from .residual_blocks import ResidualBlocks
from .shared_fit import shared_regularization
from .streaming_information import linearize_residual


def shared_prior_jacobian(prior, delta, coefficients, levers, nominal, *, heading_model=None, deadline=None):
    """Columns are heading coefficients followed by 15 lever coordinates.

    No pose columns; adding these rows at every window would duplicate prior
    information. Inherited constants remain outside this interface.
    """
    count=coefficients.numel()
    if coefficients.ndim!=1 or levers.shape!=(5,3) or nominal.shape!=(5,3):
        raise ValueError('shared heading vector and five 3D lever vectors required')
    point=torch.cat((coefficients.flatten(),levers.flatten()))
    def residual(x):
        h=x[:count];lever=x[count:].reshape(5,3)
        inc=h if heading_model is None else heading_model.increments(h)
        blocks=ResidualBlocks()
        shared_regularization(prior,delta+inc,lever,nominal,heading_model=heading_model,
                              coefficients=h,residual_blocks=blocks)
        return torch.cat([blocks[k].flatten() for k in sorted(blocks)])
    return linearize_residual(residual,point,deadline=deadline)
