#!/usr/bin/env python3
"""Local five-only acceleration information audit, never a pose candidate.

Profile constant limb-heading perturbations against per-frame missing-joint
perturbations. This linearization intentionally excludes learned/protocol
priors: curvature from a prior is not information in the five measurements.
No H or ten-node reference is loaded, and no calibration is changed.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import scipy.linalg
import scipy.sparse
import torch

from biospur_fusion.c2_five_calibration.anatomy import JointModel
from biospur_fusion.c2_five_calibration.geometry import sensor_positions
from biospur_fusion.c2_five_calibration.operators import (
    WIDTH, ACCELERATION_WIDTHS, position_coefficients,
)
from biospur_fusion.c2_five_calibration.shared_orientation import transport_heading
from biospur_fusion.c2_five_calibration.solver import acceleration_residual, valid_support
from biospur_fusion.c2_sparse_nodes.inputs import sha
from c2_five_continuous_review import load_continuous_calibration


def project_heading(joint_jacobian, heading_jacobian, *, rcond=1e-8):
    """Least-squares nuisance projection; returns raw and profiled sensitivity."""
    solution, _, rank, singular = scipy.linalg.lstsq(
        joint_jacobian, -heading_jacobian, cond=rcond, lapack_driver='gelsd')
    residual = heading_jacobian + joint_jacobian @ solution
    direct = np.linalg.norm(heading_jacobian, axis=0)
    profiled = np.linalg.norm(residual, axis=0)
    return dict(
        nuisance_rank=int(rank), nuisance_columns=joint_jacobian.shape[1],
        largest_nuisance_singular_value=float(singular[0]),
        direct_rms_per_rad=(direct / np.sqrt(len(residual))).tolist(),
        profiled_rms_per_rad=(profiled / np.sqrt(len(residual))).tolist(),
        surviving_fraction=np.divide(profiled, direct, out=np.zeros_like(direct),
                                     where=direct > 1e-12).tolist(),
        profiled_heading_singular_values=np.linalg.svd(
            residual / np.sqrt(len(residual)), compute_uv=False).tolist(),
    ), solution, residual


def position_operator(count, good):
    """Exact production derivative stencils, scale-major output ordering."""
    operators = []
    centres = np.flatnonzero(good)
    for width in ACCELERATION_WIDTHS:
        coefficients = position_coefficients(2, width)
        start = centres + (WIDTH-width)//2
        rows = np.repeat(np.arange(len(centres)), width)
        cols = (start[:, None]+np.arange(width)).ravel()
        values = np.tile(coefficients, len(centres))
        temporal = scipy.sparse.coo_matrix(
            (values, (rows, cols)), shape=(len(centres), count)).tocsr()
        operators.append(scipy.sparse.kron(temporal, scipy.sparse.eye(12)))
    return scipy.sparse.vstack(operators).tocsr()


def window_model(q, rotation, geometry):
    """Re-express a five-only checkpoint in its authoritative pose coordinates."""
    model = JointModel(geometry)
    prior, observed, acceleration = [torch.as_tensor(q[k], dtype=torch.float64)
                                    for k in ('prior', 'observed', 'acceleration')]
    previous = torch.as_tensor(rotation, dtype=torch.float64)
    parameters = model.initial(previous, observed)
    from scipy.spatial.transform import Rotation
    parameters[:, :3] = torch.from_numpy(Rotation.from_matrix(
        (previous[:, 9] @ prior[:, 9].transpose(-1, -2)).numpy()).as_rotvec())
    reconstructed = model.rotation(prior, observed, parameters)
    if not torch.allclose(reconstructed[:, [0, 1, 2, 3, 4, 5, 6, 9, 13, 14, 16, 17, 18, 19]],
                          previous[:, [0, 1, 2, 3, 4, 5, 6, 9, 13, 14, 16, 17, 18, 19]], atol=1e-6):
        raise ValueError('could not re-express checkpoint on the existing manifold')
    return model, prior, observed, acceleration, parameters


def position_jacobian(model, prior, observed, parameters, geometry, levers, operator):
    """Block-local pose derivatives followed by the production time operator."""
    epsilon = 1e-5
    local = []
    for j in range(9):
        change = torch.zeros_like(parameters)
        change[:, j] = epsilon
        plus = sensor_positions(model.rotation(prior, observed, parameters+change), geometry, levers)
        minus = sensor_positions(model.rotation(prior, observed, parameters-change), geometry, levers)
        local.append(((plus-minus)/(2*epsilon))[:, 1:].reshape(len(prior), 12).numpy())
    jacobian = np.stack(local, axis=-1)
    return (operator @ scipy.sparse.block_diag(list(jacobian), format='csr')).tocsr()


def audit_window(q, rotation, geometry, levers):
    model, prior, observed, acceleration, parameters = window_model(q, rotation, geometry)
    good = valid_support(q['valid'])
    if good.sum() < 20:
        raise ValueError('insufficient derivative support')
    operator = position_operator(len(prior), good)
    epsilon = 1e-5
    joint = position_jacobian(model, prior, observed, parameters, geometry, levers, operator).toarray()

    def residual(delta, p=parameters):
        obs, acc = transport_heading(observed, acceleration, delta)
        r = model.rotation(prior, obs, p)
        return acceleration_residual(r, acc, geometry, levers)[good].transpose(0, 1).reshape(-1).numpy()

    heading = []
    for j in range(4):
        delta = torch.zeros(4, dtype=torch.float64)
        delta[j] = epsilon
        heading.append((residual(delta)-residual(-delta))/(2*epsilon))
    heading = np.stack(heading, axis=-1)
    # Check sparse assembly against the full nonlinear production residual.
    rng = np.random.default_rng(20260913)
    step = torch.from_numpy(rng.normal(size=parameters.shape))*epsilon
    zero = torch.zeros(4, dtype=torch.float64)
    actual = residual(zero, parameters+step)-residual(zero, parameters-step)
    predicted = joint @ (2*step.numpy().ravel())
    derivative_error = np.linalg.norm(actual-predicted)/max(np.linalg.norm(actual), 1e-12)
    if derivative_error > 1e-5:
        raise ValueError('nuisance derivative does not match production residual')
    result, solution, _ = project_heading(joint, heading)
    # Ablation identifies whether *time-varying torso freedom* is necessary
    # for the compensation. Neither restricted model is adopted as truth.
    limb_columns = np.tile(np.arange(9) >= 3, len(prior))
    limb_only = joint[:, limb_columns]
    constant_torso = np.stack([joint[:, j::9].sum(axis=1) for j in range(3)], axis=1)
    result['fixed_torso'], _, _ = project_heading(limb_only, heading)
    result['constant_torso_correction'], _, _ = project_heading(
        np.column_stack((limb_only, constant_torso)), heading)
    # Finite nonlinear check of the calculated compensation, not a new fit.
    baseline = residual(zero)
    finite = []
    for limb in (0, 1):
        d = zero.clone(); d[limb] = np.deg2rad(.1)
        dp = torch.from_numpy(solution[:, limb].reshape(parameters.shape))*d[limb]
        direct = residual(d)-baseline
        compensated = residual(d, parameters+dp)-baseline
        finite.append(dict(limb=limb, injected_yaw_deg=.1,
            direct_change_rms=float(np.sqrt(np.mean(direct**2))),
            compensated_change_rms=float(np.sqrt(np.mean(compensated**2))),
            max_nuisance_change_deg=float(torch.rad2deg(dp.abs()).max()),
            torso_change_rms_deg=float(torch.rad2deg(dp[:, :3]).square().mean().sqrt())))
    result.update(derivative_relative_error=float(derivative_error), finite_checks=finite,
                  frame_count=len(prior), valid_derivative_frames=int(good.sum()),
                  baseline_acceleration_rms=float(np.sqrt(np.mean(baseline**2))))
    return result


def main(source, out, *, window_frames=61, maximum_windows=None):
    if out.exists():
        raise ValueError('new output directory required')
    out.mkdir(parents=True)
    started = time.monotonic()
    contract = dict(status='RUNNING', source=str(source.resolve()),
        purpose='local acceleration sensitivity after missing-joint nuisance projection',
        sensor_count=5, H_opened=False, ten_node_reference_opened=False,
        calibration_changed=False, prior_or_protocol_information_counted_as_data=False,
        window_frames=window_frames, declaration='DIAGNOSTIC_ONLY',
        limitation='local unconstrained tangent, not global observability or product feasibility; frozen prior supplies coordinates only',
        deadline_s=540, maximum_windows=maximum_windows,
        source_sha256=sha(Path(__file__)))
    (out/'CONTRACT.json').write_text(json.dumps(contract, indent=2))
    contracts, actions, outputs = load_continuous_calibration(source)
    geometry = json.loads((source/'GEOMETRY.json').read_text())
    report = json.loads((source/'SHARED_CALIBRATION.json').read_text())
    levers = report['fitted_sensor_levers_m']
    rows = []
    for name, q in actions.items():
        # Fixed, preselected early window for every recorded action, plus late
        # elbow phase. No reference errors or outcomes select time support.
        offsets = (2., 17.) if name.startswith(('06_', '07_')) else (2.,)
        for offset in offsets:
            if maximum_windows is not None and len(rows) >= maximum_windows:
                break
            if time.monotonic()-started > 540:
                raise TimeoutError('information audit stage budget exceeded')
            i = np.searchsorted(q['time_s'], contracts[name]['lo']+offset)
            sl = slice(i, i+window_frames)
            if i+window_frames > len(q['time_s']):
                raise ValueError('preselected window exceeds recorded action')
            result = audit_window({k:v[sl] for k,v in q.items()},
                                  outputs[name+'/rotation'][sl], geometry, levers)
            result.update(action=name, offset_s=offset, first_time_s=float(q['time_s'][i]))
            rows.append(result)
            print(json.dumps(result), flush=True)
            (out/'RESULT.json').write_text(json.dumps(dict(contract=contract, windows=rows,
                wall_s=time.monotonic()-started, complete=False), indent=2))
    contract['status']='COMPLETE_LOCAL_DIAGNOSTIC'
    contract['inputs_sha256']={n:sha(source/n) for n in (
        'C2_PRIOR.npz','SHARED_CALIBRATION.npz','GEOMETRY.json','SHARED_CALIBRATION.json')}
    (out/'RESULT.json').write_text(json.dumps(dict(contract=contract, windows=rows,
        wall_s=time.monotonic()-started, complete=True), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--maximum-windows', type=int)
    args = parser.parse_args()
    torch.set_num_threads(1)
    main(args.source, args.out, maximum_windows=args.maximum_windows)
