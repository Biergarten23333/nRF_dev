"""Matched windowed position/acceleration operators on the original time grid.

For the position stencil c, sum(c)=sum(c*t)=0 eliminates unknown initial
position and velocity. Integrating piecewise-linear acceleration gives the
matching acceleration stencil c @ integration. Ordinary Savitzky-Golay
smoothing is not the acceleration counterpart of its derivative stencil.
"""
from functools import lru_cache

import numpy as np
from scipy.signal import savgol_coeffs
import torch
import torch.nn.functional as F

HZ = 20
WIDTH = 11
ACCELERATION_WIDTHS = (11, 5)


@lru_cache(maxsize=8)
def position_coefficients(derivative, width=WIDTH):
    return savgol_coeffs(width, 3, deriv=derivative, delta=1/HZ, use='dot').copy()


@lru_cache(maxsize=2)
def acceleration_coefficients(width=WIDTH):
    step = 1/HZ
    basis = np.eye(width)
    velocity = np.zeros(width)
    positions = [np.zeros(width)]
    for k in range(width-1):
        positions.append(positions[-1] + step*velocity +
                         step**2*(2*basis[k]+basis[k+1])/6)
        velocity = velocity + step*(basis[k]+basis[k+1])/2
    return position_coefficients(2, width) @ np.stack(positions)


@lru_cache(maxsize=2)
def velocity_coefficients(width=WIDTH):
    """Match the position derivative for piecewise-linear velocity samples."""
    basis = np.eye(width)
    positions = [np.zeros(width)]
    for k in range(width-1):
        positions.append(positions[-1] + (basis[k]+basis[k+1])/(2*HZ))
    return position_coefficients(1, width) @ np.stack(positions)


def apply_stencil(values, coefficients):
    kernel = torch.as_tensor(coefficients, dtype=values.dtype, device=values.device)
    shape = values.shape
    x = values.reshape(shape[0], -1).T[:, None]
    y = F.conv1d(x, kernel[None, None])[:, 0].T
    return y.reshape(-1, *shape[1:])


def filtered(values, derivative=0):
    return apply_stencil(values, position_coefficients(derivative))


def integrated_acceleration(values):
    return apply_stencil(values, acceleration_coefficients())


def multiscale(values, *, acceleration=False):
    """Two matched bandwidths at identical centres and gap-valid support.

    Axis 1 indexes scale, not independent measurements. The pose objective
    averages over that axis; offset fitting uses the same total weight. Keep
    the long window for slow motion and the short one for fast motion, rather
    than replacing the long window and discarding its low-frequency support.
    """
    result = []
    for width in ACCELERATION_WIDTHS:
        coefficients = acceleration_coefficients(width) if acceleration else position_coefficients(2, width)
        filtered_values = apply_stencil(values, coefficients)
        trim = (WIDTH-width)//2
        result.append(filtered_values[trim:-trim] if trim else filtered_values)
    return torch.stack(result, dim=1)
