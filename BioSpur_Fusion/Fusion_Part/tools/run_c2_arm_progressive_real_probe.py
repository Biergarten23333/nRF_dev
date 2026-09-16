#!/usr/bin/env python3
"""Offline real-prefix diagnostic; not a streaming/product calibration gate.

Reuse the continuous raw five-node frontend and arm objective. Recorded VQF
outputs are causal per sensor, but the reader buffers a whole action including
postroll before fitting. No final frontend state is used as earlier evidence.
"""
import argparse
import json
from pathlib import Path
import time

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from biospur_fusion.c2_sparse_nodes.inputs import FiveNodeFrontend, NODES, episode_contracts, sha
from biospur_fusion.c2_five_calibration.progressive.arm_factors import PHASES, build_phase
from biospur_fusion.c2_five_calibration.progressive.arm_model import STARTS_DEG, initial_parameters, solve_one
from biospur_fusion.c2_five_calibration.progressive.neutral_frame import initial_neutral_correction


def align_phase(stream, lo, hi):
    """Interpolate inside this prefix only; never bridge a gap above 25 ms."""
    clipped = {n: a[(a[:, 0] >= lo) & (a[:, 0] < hi)] for n, a in stream.items()}
    if any(len(a) < 100 for a in clipped.values()):
        raise ValueError('insufficient real phase support')
    start = max(a[0, 0] for a in clipped.values())
    stop = min(a[-1, 0] for a in clipped.values())
    grid = np.arange(start, stop, .005)
    valid = np.ones(len(grid), dtype=bool)
    for a in clipped.values():
        index = np.searchsorted(a[:, 0], grid).clip(1, len(a)-1)
        valid &= a[index, 0]-a[index-1, 0] <= .025
    grid = grid[valid]
    rows = {}
    for n, a in clipped.items():
        rotation = Rotation.from_quat(a[:, [2, 3, 4, 1]])
        q = Slerp(a[:, 0], rotation)(grid).as_quat()[:, [3, 0, 1, 2]]
        values = np.column_stack([np.interp(grid, a[:, 0], a[:, j]) for j in range(5, 11)])
        rows[n] = np.column_stack((grid, q, values))
    # Front abdominal placement: sensor -Z forward, sensor -Y down.
    # This is a nominal assumption, NOT a fitted or measured pelvis mount.
    body_to_sensor = np.array([[0., -1., 0.], [0., 0., 1.], [-1., 0., 0.]])
    pelvis = Rotation.from_quat(rows[NODES[0]][:, [2, 3, 4, 1]])
    q = (pelvis * Rotation.from_matrix(body_to_sensor)).as_quat()
    rows[NODES[0]][:, 1:5] = q[:, [3, 0, 1, 2]]
    return rows, dict(raw_rows={n: len(a) for n, a in clipped.items()},
                      synchronized_rows=len(grid), rejected_gap_grid_rows=int((~valid).sum()),
                      latest_source_time=max(float(a[-1, 0]) for a in clipped.values()))


def main(out, pelvis_neutral_from_initial=False, protocol='synthetic_ideal_pose'):
    out.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__), *Path('src/biospur_fusion/c2_five_calibration/progressive').glob('*.py'),
               Path('src/biospur_fusion/c2_sparse_nodes/inputs.py'),
               Path('src/biospur_fusion/c2_sparse_nodes/calibration.py')]
    contract = dict(kind='REAL_OFFLINE_UPPER_PREFIX_DIAGNOSTIC', ten_node_parameters_used=False,
                    H_used=False, calibration_accepted=False, full_C2_complete=False,
                    pelvis_mount='nominal front abdomen; sensitivity not yet validated',
                    pelvis_neutral_from_initial=pelvis_neutral_from_initial,
                    protocol_policy=protocol,
                    branch_policy='retain all converged branches; no synthetic wear rejection',
                    clock_policy='existing transport clock mapping; no UWB spatial observations',
                    scope='00 through 07 only; does not validate full-body pose or anthropometry',
                    sources={str(p): sha(p) for p in sources})
    (out/'CONTRACT.json').write_text(json.dumps(contract, indent=2))
    contracts = episode_contracts()
    frontend = FiveNodeFrontend()
    factors, trace, audits = [], [], []
    previous = [[], []]
    started = time.monotonic()
    action_loaded = None
    neutral = None
    for action, phase, duration in PHASES:
        if time.monotonic()-started > 600:
            raise TimeoutError('ten-minute probe budget exceeded')
        c = contracts[action]
        if action_loaded != action:
            data, audit = frontend.read({action: c}, start=frontend.cursor)
            audits.append(audit)
            stream = {n: data[action][n]['imu'] for n in NODES}
            action_loaded = action
        lo = c['lo'] + (15. if phase == 'pronation' else 0.)
        hi = min(c['hi'], lo+duration)
        rows, alignment = align_phase(stream, lo, hi)
        if pelvis_neutral_from_initial:
            pelvis = Rotation.from_quat(rows[NODES[0]][:, [2, 3, 4, 1]])
            if neutral is None:
                if action != '00_initial_still':
                    raise ValueError('initial pelvis reference must come from 00 only')
                neutral = initial_neutral_correction(pelvis)
                (out/'PELVIS_NEUTRAL.json').write_text(json.dumps(dict(
                    right_correction_matrix=neutral.as_matrix().tolist(),
                    source_action=action, source_max_time=float(rows[NODES[0]][-1, 0]),
                    interpretation='00 neutral-pelvis convention, not independently measured anatomical mounting'), indent=2))
            q = (pelvis*neutral).as_quat()
            rows[NODES[0]][:, 1:5] = q[:, [3, 0, 1, 2]]
        phase_id = action+':'+phase
        new, diagnostics = build_phase(phase_id, lo, hi, rows, policy=protocol)
        factors.extend(new)
        arms = []
        for limb in range(2):
            subset = [f for f in factors if f['limb'] == limb]
            if not subset:
                arms.append(dict(candidates=[],status='NO_ARM_DIRECTION_EVIDENCE',best_cost=None))
                continue
            seeds = [initial_parameters(limb, np.deg2rad(s)) for s in STARTS_DEG]
            seeds += [np.asarray(r['parameters']) for r in previous[limb][:4]]
            candidates = sorted([solve_one(subset, p) for p in seeds], key=lambda r: r['cost'])
            previous[limb] = candidates
            arms.append(dict(candidates=candidates, status='DIAGNOSTIC_ONLY',
                             best_cost=candidates[0]['cost']))
        snapshot = dict(phase=phase_id, arms=arms, alignment=alignment, diagnostics=diagnostics,
                        cumulative_factors=len(factors), calibration_accepted=False)
        trace.append(snapshot)
        (out/'TRACE.json').write_text(json.dumps(trace, indent=2))
        (out/'FACTORS.json').write_text(json.dumps(factors))
        (out/'INPUT_AUDIT.json').write_text(json.dumps(audits, indent=2))
        print(phase_id, 'costs', [round(a['best_cost'], 4) if a['best_cost'] is not None else None for a in arms], flush=True)
    (out/'RESULT.json').write_text(json.dumps(dict(completed=True, phases=len(trace),
        elapsed_seconds=time.monotonic()-started, calibration_accepted=False), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--pelvis-neutral-from-initial', action='store_true')
    parser.add_argument('--protocol', choices=('synthetic_ideal_pose','actual_c2_conditional'),default='synthetic_ideal_pose')
    args = parser.parse_args()
    main(args.out, args.pelvis_neutral_from_initial, args.protocol)
