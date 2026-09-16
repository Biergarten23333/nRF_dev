"""Continuous calibration support and lossless action export boundaries.

One 20 Hz phase is selected from the existing continuous 60 Hz neural tape.
Raw navigation remains native 200 Hz. Inter-action samples are never removed
from physical fitting. Invalid sample support remains authoritative.
"""
import numpy as np
import torch

from .shared_orientation import transport_heading


def check_continuous_transport(baseline, candidate, delta):
    """A new calibration may change observations, never time or gap support."""
    if set(baseline) != set(candidate):
        raise ValueError('continuous replay fields changed')
    for key in ('time_s','valid'):
        if not np.array_equal(baseline[key],candidate[key]):
            raise ValueError('continuous replay changed time/support')
    r,a=transport_heading(torch.as_tensor(baseline['observed']),
        torch.as_tensor(baseline['acceleration']),delta)
    if not np.allclose(r.numpy(),candidate['observed'],atol=1e-6,rtol=0):
        raise ValueError('continuous replay orientation does not match heading')
    if not np.allclose(a.numpy(),candidate['acceleration'],atol=1e-6,rtol=0):
        raise ValueError('continuous replay acceleration does not match heading')


def action_checkpoints(checkpoint, continuous, actions):
    """Slice an already fitted trajectory exactly; never fit an export pose."""
    result=dict(checkpoint,parameters={},rotations={})
    for name,q in actions.items():
        index=np.searchsorted(continuous['time_s'],q['time_s'])
        if (np.any(index>=len(continuous['time_s']))
                or not np.array_equal(continuous['time_s'][index],q['time_s'])
                or not np.array_equal(continuous['valid'][index],q['valid'])):
            raise ValueError('action export is not an exact continuous subset')
        result['parameters'][name]=checkpoint['parameters']['_continuous'][index]
        result['rotations'][name]=checkpoint['rotations']['_continuous'][index]
    return result
