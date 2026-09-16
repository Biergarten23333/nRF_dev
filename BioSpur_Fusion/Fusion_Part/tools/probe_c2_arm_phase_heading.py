#!/usr/bin/env python3
"""C2-only nested-model diagnostic, never a deployable calibration choice.

Compare constant heading with phase-specific heading while preserving the
same mount, factors and residual normalization. A lower cost is expected with
more freedom; it does not prove drift or validate independent phase resets.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_five_calibration.progressive.arm_model import residual_terms


def direction_diagnostics(parameters, factors):
    mount = Rotation.from_rotvec(parameters[:3]).as_matrix()
    yaw = Rotation.from_euler('z', parameters[3]).as_matrix()
    result = []
    for f in factors:
        if f['kind'] != 'direction':
            continue
        target = np.asarray(f['target'])
        local = -mount[:, 2] if f['axis'] is None else np.asarray(f['axis'])
        observed = yaw @ np.asarray(f['observed']) @ local
        cosine = np.sum(target*observed, axis=1).clip(-1., 1.)
        error = np.arccos(abs(cosine) if f['axial'] else cosine)
        # Z is invariant under world-yaw changes. Opposite axial signs are
        # equivalent for an unoriented gyro axis, unlike a distal bone vector.
        elevation_o = np.arcsin(observed[:, 2].clip(-1., 1.))
        elevation_t = np.arcsin(target[:, 2].clip(-1., 1.))
        elevation_error = (abs(abs(elevation_o)-abs(elevation_t)) if f['axial']
                           else abs(elevation_o-elevation_t))
        result.append(dict(factor=f['id'], angle_rms_deg=float(np.rad2deg(np.sqrt(np.mean(error**2)))),
                           yaw_invariant_elevation_rms_deg=float(np.rad2deg(np.sqrt(np.mean(elevation_error**2))))))
    return result


def main(run, out):
    factors = json.loads((run/'FACTORS.json').read_text())
    final = json.loads((run/'TRACE.json').read_text())[-1]
    output = []
    for limb, arm in enumerate(final['arms']):
        subset = [f for f in factors if f['limb'] == limb]
        phases = list(dict.fromkeys(f['phase'] for f in subset if f['kind'] == 'direction'))
        def residual(p):
            values = []
            for f in subset:
                heading = p[3+phases.index(f['phase'])]
                values.extend(residual_terms(np.r_[p[:3], heading], [f]).values())
            return np.concatenate(values)
        candidates = []
        for previous in arm['candidates'][:4]:
            p = previous['parameters']
            fit = least_squares(residual, np.r_[p[:3], np.repeat(p[3], len(phases))],
                                max_nfev=150, ftol=1e-10, xtol=1e-10, gtol=1e-10)
            candidates.append(dict(parameters=fit.x.tolist(), cost=float(fit.cost), success=bool(fit.success)))
        best = min(candidates, key=lambda r: r['cost'])
        old = arm['candidates'][0]
        delta = Rotation.from_rotvec(best['parameters'][:3])*Rotation.from_rotvec(old['parameters'][:3]).inv()
        output.append(dict(limb=limb, constant_heading_cost=old['cost'], phase_heading_candidates=candidates,
            phase_heading_cost=best['cost'], mount_change_deg=float(np.rad2deg(delta.magnitude())),
            phase_heading_deg=dict(zip(phases, np.rad2deg(best['parameters'][3:]).tolist())),
            constant_model_directions=direction_diagnostics(old['parameters'], subset),
            phase_model_directions=[d for i, phase in enumerate(phases) for d in direction_diagnostics(
                np.r_[best['parameters'][:3], best['parameters'][3+i]], [f for f in subset if f['phase'] == phase])]))
    out.write_text(json.dumps(dict(kind='NESTED_MODEL_DIAGNOSTIC_ONLY', deployable=False,
                                  H_used=False, ten_node_used=False, arms=output), indent=2))
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    main(args.run, args.out)
