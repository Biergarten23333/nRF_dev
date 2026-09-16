#!/usr/bin/env python3
"""Fit local gyro axes only, then diagnose conditional protocol directions.

Four sign branches are kept. T-pose supplies only a conditional horizontal
heading, never its elevation. No pose acceptance or deployment is implied.
"""
import argparse
import itertools
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from probe_c2_arm_phase_heading import direction_diagnostics


def main(run, out):
    factors = json.loads((run/'FACTORS.json').read_text())
    result = []
    for limb in range(2):
        subset = [f for f in factors if f['limb'] == limb]
        axes = {f['column']: np.asarray(f['axis']) for f in subset if f['kind'] == 'sensor_axis'}
        tpose = next(f for f in subset if f['phase'] == '02_t_pose:full')
        still = next(f for f in subset if f['phase'] == '00_initial_still:full')
        branches = []
        for hinge_sign, long_sign in itertools.product((-1, 1), repeat=2):
            observed_axes = np.array([hinge_sign*axes[1], long_sign*axes[2]])
            mount, rssd = Rotation.align_vectors(observed_axes, np.eye(3)[[1, 2]])
            long = mount.as_matrix()[:, 2]
            observed = np.asarray(tpose['observed']) @ (-long)
            target = np.asarray(tpose['target'])
            weights = np.linalg.norm(observed[:, :2], axis=1)*np.linalg.norm(target[:, :2], axis=1)
            delta = np.arctan2(target[:, 1], target[:, 0])-np.arctan2(observed[:, 1], observed[:, 0])
            circular = np.sum(weights*np.exp(1j*delta))
            if weights.sum() <= 1e-12:
                raise ValueError('T-pose lacks horizontal direction support')
            heading = float(np.angle(circular))
            initial = np.asarray(still['observed']) @ (-long)
            branches.append(dict(hinge_sign=hinge_sign, long_sign=long_sign,
                parameters=np.r_[mount.as_rotvec(), heading].tolist(),
                local_axis_rssd=float(rssd),
                initial_distal_world_z_median=float(np.median(initial[:, 2])),
                conditional_tpose_heading_deg=float(np.rad2deg(heading)),
                tpose_heading_resultant=float(abs(circular)/weights.sum()),
                direction_diagnostics=direction_diagnostics(np.r_[mount.as_rotvec(), heading], subset)))
        result.append(dict(limb=limb, branches=branches))
    out.write_text(json.dumps(dict(kind='LOCAL_AXES_PROTOCOL_DIAGNOSTIC',
        H_used=False, ten_node_used=False, branches_selected=False, calibration_accepted=False,
        axes_source='both halves of each recorded elbow phase; no protocol direction in axis fit',
        arms=result), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    main(args.run, args.out)
