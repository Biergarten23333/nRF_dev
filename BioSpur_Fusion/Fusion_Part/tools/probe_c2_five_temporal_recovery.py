#!/usr/bin/env python3
"""Controlled dynamic recovery gate for the optional torso parameterization.

This generator supplies an explicitly imperfect pose prior and known injected
heading errors. It validates solver mechanics under those declared priors,
not raw-sensor observability, released-network performance, or real H motion.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.spatial.transform import Rotation
import torch

from biospur_fusion.c2_five_calibration.anatomy import JointModel, exp_rotation
from biospur_fusion.c2_five_calibration.geometry import sensor_positions
from biospur_fusion.c2_five_calibration.shared_fit import _optimize
from biospur_fusion.c2_five_calibration.shared_orientation import transport_heading
from biospur_fusion.c2_five_calibration.solver import PoseObjective, bend_cosines
from biospur_fusion.c2_sparse_nodes.inputs import sha


class NoHeadingTarget:
    """No true heading, protocol direction, or true missing joint reaches fit."""
    factor_actions = {}

    def energy(self, delta):
        return delta.sum()*0.

    def energy_for_action(self, action, delta, rotation):
        return delta.sum()*0.+rotation.sum()*0.


def fixture(geometry, sign):
    t = np.arange(201)/20
    observed = np.tile(np.eye(3), (len(t), 5, 1, 1))
    for i in range(5):
        amplitudes = np.array([.35, .25, .45]) if i else np.array([.15, .3, .1])
        angles = np.column_stack([np.sin((.7+j*.31+i*.19)*t+i*.6+j*.4) for j in range(3)])
        observed[:, i] = Rotation.from_euler('xyz', angles*amplitudes).as_matrix()
    neutral = torch.eye(3, dtype=torch.float64).repeat(len(t), 24, 1, 1)
    parameters = torch.zeros(len(t), 9, dtype=torch.float64)
    parameters[:, :3] = torch.from_numpy(np.column_stack((.15*np.sin(1.1*t),
                                                         .3*np.sin(.8*t), .1*np.cos(.9*t))))
    for i in range(4):
        parameters[:, 3+i] = torch.from_numpy(.65+.25*np.sin((.9+.2*i)*t+i))
    parameters[:, 7] = torch.from_numpy(.2*np.sin(.9*t))
    parameters[:, 8] = torch.from_numpy(.2*np.cos(1.2*t))
    model = JointModel(geometry)
    truth = model.rotation(neutral, torch.from_numpy(observed), parameters)
    levers = torch.as_tensor(geometry['nominal_sensor_levers_m'], dtype=torch.float64)
    position = sensor_positions(truth, geometry, levers).numpy()
    acc = CubicSpline(t, position).derivative(2)(t)
    # Shared translational acceleration must cancel under the pelvis-relative
    # measurement equation, not be mistaken for motion of the arm joints.
    acc += np.column_stack((.3*np.sin(.7*t), .15*np.cos(1.1*t), .2*np.sin(.8*t)))[:, None]
    injected = torch.deg2rad(torch.tensor([12., -9., 8., -11.], dtype=torch.float64))*sign
    obs_bad, acc_bad = transport_heading(torch.from_numpy(observed), torch.from_numpy(acc), -injected)
    prior = truth.clone()
    # A known 5-degree bias makes the prior imperfect. It is fixed before
    # fitting and independent of the injected heading. No exact true parent
    # is supplied to the optimizer. This remains a favorable synthetic prior.
    biases = np.deg2rad(np.array([[3., -4., 1.], [-2., 3., 3.], [4., 1., -2.], [-3., -2., 3.], [2., -4., 2.]]))
    for j, b in zip((9, 16, 17, 1, 2), biases):
        prior[:, j] = exp_rotation(torch.from_numpy(b))@prior[:, j]
    q = dict(prior=prior.numpy(), observed=obs_bad.numpy(), acceleration=acc_bad.numpy(),
             valid=np.ones(len(t), dtype=bool), time_s=t)
    return q, truth, injected, levers


def main(geometry_path, out, *, refresh_projection=False, projection_gap=False):
    if out.exists():
        raise ValueError('new gate directory required')
    out.mkdir(parents=True)
    contract = dict(status='RUNNING', geometry=str(geometry_path.resolve()),
        geometry_sha256=sha(geometry_path), source_sha256=sha(Path(__file__)),
        implementation_sha256={name:sha(Path('src/biospur_fusion/c2_five_calibration')/name)
                               for name in ('shared_fit.py', 'temporal_torso.py')},
        knot_spacings_s=[None] if projection_gap else [None, 1.] if refresh_projection else [None, .5, 1., 2.], signs=[1, -1],
        refresh_projection=refresh_projection,projection_gap=projection_gap,
        iterations=180, case_budget_s=90, max_heading_error_deg=3.,
        max_torso_error_rms_deg=7.,
        declared_level='controlled synthetic mechanism only; frozen imperfect prior, no network replay',
        H_or_ten_node_data_opened=False, all_C2_fit_authorized_by_this_gate=False)
    (out/'CONTRACT.json').write_text(json.dumps(contract, indent=2))
    geometry = json.loads(geometry_path.read_text())
    results = []
    for sign in contract['signs']:
        q, truth, injected, levers = fixture(geometry, sign)
        objective = PoseObjective(**q, geometry=geometry)
        for spacing in contract['knot_spacings_s']:
            start = time.monotonic()
            fitted = _optimize({'synthetic':objective}, {'synthetic':objective.initial}, levers,
                torch.zeros(4, dtype=torch.float64), levers, NoHeadingTarget(),
                iterations=contract['iterations'], deadline=start+90, shared=True,
                torso_knot_spacing_s=spacing,refresh_projection=refresh_projection,projection_gap=projection_gap)
            estimated = fitted['rotations']['synthetic']
            heading_error = torch.rad2deg(fitted['delta']-injected).numpy()
            torso_error = np.rad2deg(Rotation.from_matrix(
                (estimated[:, 9]@truth[:, 9].transpose(-1, -2)).numpy()).magnitude())
            bend_error = torch.rad2deg(torch.acos(bend_cosines(estimated, geometry).clamp(-1, 1))
                -torch.acos(bend_cosines(truth, geometry).clamp(-1, 1))).abs().mean(0).numpy()
            row = dict(sign=sign, torso_knot_spacing_s=spacing,
                heading_error_deg=heading_error.tolist(), bend_mae_deg=bend_error.tolist(),
                torso_error_rms_deg=float(np.sqrt(np.mean(torso_error**2))),
                passed=bool(np.max(np.abs(heading_error))<=3. and np.sqrt(np.mean(torso_error**2))<=7.),
                energy=fitted['energy'], selected_step=fitted['step'], wall_s=time.monotonic()-start)
            results.append(row)
            print(json.dumps(row), flush=True)
            (out/'RESULT.json').write_text(json.dumps(dict(contract=contract, cases=results,
                                                          complete=False), indent=2))
    contract['status']='CONTROLLED_RECOVERY_COMPLETE'
    temporal = [r for r in results if r['torso_knot_spacing_s'] is not None]
    (out/'RESULT.json').write_text(json.dumps(dict(contract=contract, cases=results, complete=True,
        all_cases_passed=bool(results) and all(r['passed'] for r in results),
        all_temporal_cases_passed=all(r['passed'] for r in temporal) if temporal else None), indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--geometry', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--refresh-projection', action='store_true')
    p.add_argument('--projection-gap', action='store_true')
    args = p.parse_args()
    torch.set_num_threads(1)
    main(args.geometry, args.out,refresh_projection=args.refresh_projection,projection_gap=args.projection_gap)
