#!/usr/bin/env python3
"""Finite-amplitude counterfactual for the current five-only observation model.

Change one wrist's calibrated heading and profile the missing joints while
preserving the original acceleration residual vector. This is an ablation,
not calibration, a recovered motion, or a physically complete human model.
It enforces existing elbow/knee ROM but adds no assumed chest pose or label.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
import torch

from biospur_fusion.c2_five_calibration.anatomy import FLEXION
from biospur_fusion.c2_five_calibration.geometry import sensor_positions
from biospur_fusion.c2_five_calibration.shared_orientation import transport_heading
from biospur_fusion.c2_five_calibration.solver import acceleration_residual, valid_support
from biospur_fusion.c2_sparse_nodes.inputs import sha
from c2_five_continuous_review import load_continuous_calibration
from probe_c2_five_heading_information import position_jacobian, position_operator, window_model


def compensate(q, rotation, geometry, levers, angle_deg):
    start = time.monotonic()
    model, prior, observed, acceleration, parameters = window_model(q, rotation, geometry)
    good = valid_support(q['valid'])
    operator = position_operator(len(prior), good)
    original = model.rotation(prior, observed, parameters)
    baseline = acceleration_residual(original, acceleration, geometry, levers)[good]
    baseline = baseline.transpose(0, 1).reshape(-1).numpy()
    delta = torch.tensor([0., np.deg2rad(angle_deg), 0., 0.], dtype=torch.float64)
    obs, acc = transport_heading(observed, acceleration, delta)
    low, high = np.full(parameters.shape, -np.inf), np.full(parameters.shape, np.inf)
    low[:, FLEXION], high[:, FLEXION] = 0., model.maximum_bend.numpy()

    def check_time():
        if time.monotonic()-start > 90:
            raise TimeoutError('finite counterfactual exceeded 90 seconds')

    def evaluate(x):
        r = model.rotation(prior, obs, torch.from_numpy(x.reshape(parameters.shape)))
        return acceleration_residual(r, acc, geometry, levers)[good].transpose(0, 1).reshape(-1).numpy()

    def fun(x):
        check_time()
        return evaluate(x)-baseline

    def jac(x):
        check_time()
        return position_jacobian(model, prior, obs,
            torch.from_numpy(x.reshape(parameters.shape)), geometry, levers, operator)

    initial = np.clip(parameters.numpy(), low, high).ravel()
    before = fun(initial)
    fit = least_squares(fun, initial, jac=jac, bounds=(low.ravel(), high.ravel()),
                        max_nfev=40, tr_solver='lsmr', ftol=1e-8, xtol=1e-8, gtol=1e-8)
    final = torch.from_numpy(fit.x.reshape(parameters.shape))
    revised = model.rotation(prior, obs, final)
    moved = sensor_positions(revised, geometry, levers)-sensor_positions(original, geometry, levers)
    torso_relative = revised[:, 9]@original[:, 9].transpose(-1, -2)
    from scipy.spatial.transform import Rotation
    torso_deg = np.rad2deg(Rotation.from_matrix(torso_relative.numpy()).magnitude())
    raw_rms = float(np.sqrt(np.mean(before**2)))
    remaining = float(np.sqrt(np.mean(fit.fun**2)))
    result = dict(injected_right_wrist_yaw_deg=angle_deg,
        initial_residual_change_rms_mps2=raw_rms,
        final_residual_change_rms_mps2=remaining,
        remaining_fraction=remaining/max(raw_rms, 1e-12),
        baseline_acceleration_residual_rms_mps2=float(np.sqrt(np.mean(baseline**2))),
        max_torso_rotation_change_deg=float(torso_deg.max()),
        max_flexion_change_deg=float(torch.rad2deg((final-parameters)[:, FLEXION].abs()).max()),
        max_root_relative_sensor_position_change_m=float(moved.norm(dim=-1).max()),
        existing_flexion_limits_satisfied=bool(np.all(fit.x >= low.ravel()-1e-10)
                                              and np.all(fit.x <= high.ravel()+1e-10)),
        nonlinear_optimizer_success=bool(fit.success), stop_message=fit.message,
        function_evaluations=fit.nfev, wall_s=time.monotonic()-start,
        full_anatomical_validity_claimed=False, neural_prior_refreshed=False,
        pose_candidate=False)
    return result


def main(source, out):
    if out.exists():
        raise ValueError('new output directory required')
    out.mkdir(parents=True)
    selected = [('05_shoulder_right', 2.), ('07_elbow_right', 2.), ('07_elbow_right', 17.)]
    contract = dict(status='RUNNING', selected_windows=selected, window_frames=61,
        perturbation_deg=[-5., 5.], per_case_budget_s=90, total_case_count=6,
        five_only=True, H_opened=False, ten_node_reference_opened=False,
        source_sha256=sha(Path(__file__)), helper_sha256=sha(Path(__file__).with_name(
            'probe_c2_five_heading_information.py')),
        qualification='finite residual-preservation ablation with existing elbow/knee bounds; not full biomechanics or product acceptance')
    (out/'CONTRACT.json').write_text(json.dumps(contract, indent=2))
    contracts, actions, outputs = load_continuous_calibration(source)
    geometry = json.loads((source/'GEOMETRY.json').read_text())
    report = json.loads((source/'SHARED_CALIBRATION.json').read_text())
    rows = []
    for name, offset in selected:
        q = actions[name]
        i = np.searchsorted(q['time_s'], contracts[name]['lo']+offset)
        sl = slice(i, i+61)
        for angle in (-5., 5.):
            result = compensate({k:v[sl] for k,v in q.items()}, outputs[name+'/rotation'][sl],
                                geometry, report['fitted_sensor_levers_m'], angle)
            result.update(action=name, offset_s=offset)
            rows.append(result)
            print(json.dumps(result), flush=True)
            (out/'RESULT.json').write_text(json.dumps(dict(contract=contract, rows=rows,
                                                          complete=False), indent=2))
    contract['status']='COMPLETE_FINITE_DIAGNOSTIC'
    (out/'RESULT.json').write_text(json.dumps(dict(contract=contract, rows=rows, complete=True), indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    torch.set_num_threads(1)
    main(args.source, args.out)
