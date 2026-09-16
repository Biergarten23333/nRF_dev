"""Approximate C2 bend instructions, separate from observed IMU directions.

Source: ACTUAL_ACTION_EXECUTION_TABLE.md, elbow late phases and hip raises.
`About 90 degrees` supplies a broad intent prior, never exact pose truth.
Each complete phase contributes one duration-normalized factor; invalid time
loses weight. Nothing in this module is used for H/runtime inference.
"""
from dataclasses import dataclass

import numpy as np
import torch
import torch.nn.functional as F

from biospur_fusion.c2_sparse_nodes.inputs import ROOT, sha
from .frontend import FIT
from .operators import HZ

SOURCE = ROOT/'datasets/phase2_calibration/phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/subject/ACTUAL_ACTION_EXECUTION_TABLE.md'
INTENTS = (
    ('06_elbow_left', 0, 15., 30., '后15秒肘约90度'),
    ('07_elbow_right', 1, 15., 30., '后15秒肘约90度'),
    ('08_hip_left', 2, 0., 30., '左膝自然弯曲约90度'),
    ('09_hip_right', 3, 0., 30., '右膝自然弯曲约90度'),
)
SIGMA_DEG = 25.  # Declared broad engineering uncertainty, not measured SD.


@dataclass(frozen=True)
class BendIntent:
    action: str
    limb: int
    index: np.ndarray
    weights: np.ndarray
    interval: tuple[float, float]


class BendProtocol:
    def __init__(self, rows, source_sha256, sigma_deg=SIGMA_DEG):
        if not np.isfinite(sigma_deg) or sigma_deg < SIGMA_DEG:
            raise ValueError('approximate instructions require broad uncertainty >=25 degrees')
        self.rows = tuple(rows)
        self.actions = {r.action for r in self.rows}
        self.source_sha256 = source_sha256
        self.sigma_rad = float(np.deg2rad(sigma_deg))
        self.nominal_bend_rad = float(np.pi/2)
        if {(r.action, r.limb) for r in self.rows} != {(a, i) for a, i, *_ in INTENTS} or len(self.rows) != 4:
            raise ValueError('all four distinct recorded bend intents required')
        for row in self.rows:
            if (row.index.ndim != 1 or row.weights.shape != row.index.shape
                    or not np.isfinite(row.weights).all() or np.any(row.weights < 0)
                    or row.weights.sum() > 1.+1e-9 or np.any(np.diff(row.index) <= 0)):
                raise ValueError('invalid phase time support')

    def energy_for_action(self, action, parameters, *, residual_blocks=None):
        if action.startswith('H'):
            raise ValueError('calibration bend intent is forbidden in H inference')
        total = parameters.sum()*0.
        for row in self.rows:
            if row.action != action:
                continue
            angle = parameters[row.index, 3+row.limb]
            normalized = (angle-self.nominal_bend_rad)/self.sigma_rad
            robust = 2*F.smooth_l1_loss(normalized, torch.zeros_like(normalized), reduction='none')
            if residual_blocks is not None:
                from .residual_blocks import record_weighted, signed_huber_residual
                record_weighted(residual_blocks, f'bend/{row.action}/{row.limb}',
                                signed_huber_residual(normalized), row.weights)
            total = total+(torch.as_tensor(row.weights, dtype=parameters.dtype,
                                          device=parameters.device)*robust).sum()
        return total

    def audit(self):
        return dict(source=str(SOURCE), source_sha256=self.source_sha256,
            nominal_bend_deg=float(np.rad2deg(self.nominal_bend_rad)), uncertainty_deg=float(np.rad2deg(self.sigma_rad)),
            uncertainty_is_measured=False, exact_angle_target=False, H_used=False,
            contribution='one robust duration-normalized soft factor per phase',
            phases=[dict(action=r.action, limb=r.limb, interval=list(r.interval),
                         frames=len(r.index), retained_weight=float(r.weights.sum())) for r in self.rows])


def build_bend_protocol(contracts, actions):
    if ({n[:2] for n in actions} != FIT or len(actions) != 19 or set(actions) != set(contracts)):
        raise ValueError('all recorded C2 actions, and no H, must supply protocol support')
    text = SOURCE.read_text()
    rows = []
    for action, limb, start, stop, evidence in INTENTS:
        line = next((line for line in text.splitlines() if f'`{action}`' in line), '')
        if evidence not in line:
            raise ValueError('recorded bend instruction changed: '+action)
        q = actions[action]
        t = np.asarray(q['time_s']); valid = np.asarray(q['valid'])
        if (t.ndim != 1 or valid.shape != t.shape or valid.dtype != bool
                or not np.isfinite(t).all() or not np.allclose(np.diff(t), 1/HZ, atol=1e-6)
                or np.shape(q['observed']) != (len(t), 5, 3, 3)):
            raise ValueError('protocol requires the original five-node 20 Hz pose grid')
        lo = contracts[action]['lo']+start
        hi = min(contracts[action]['hi'], contracts[action]['lo']+stop)
        if hi <= lo:
            raise ValueError('recorded interval does not cover its bend phase')
        weight = np.maximum(0., np.minimum(t+.5/HZ, hi)-np.maximum(t-.5/HZ, lo))/(hi-lo)
        keep = np.flatnonzero(valid & (t >= lo) & (t < hi) & (weight > 0))
        rows.append(BendIntent(action, limb, keep, weight[keep], (lo, hi)))
    return BendProtocol(rows, sha(SOURCE))
