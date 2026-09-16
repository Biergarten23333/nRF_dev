"""Replay one captured committed-state rejection without a reset or retuning."""
import argparse
from dataclasses import asdict
from functools import partial
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import scipy

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import project_hinge_corrections
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.c2_uwb_calibration.contact_motion_step import ContactMotionStepConfig
from biospur_fusion.c2_uwb_calibration.contact_hinge_motion import ContactHingeMotionConfig
from biospur_fusion.c2_uwb_calibration.contact_motion_tracking import (
    ContactMotionTrackingState, ContactMotionTrackingConfig, advance_contact_motion_tracking)
from build_c2_full_session_ten_node_ab import _hinges


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    parser.add_argument('--hinge-only', action='store_true')
    parser.add_argument('--report-name', default='REJECTION_REPLAY.json')
    args = parser.parse_args()
    with np.load(args.folder/'REJECTED_INPUT.npz') as data:
        d = {k: data[k].copy() for k in data.files}
    config = json.loads((args.folder/'REJECTED_INPUT.json').read_text())['solver_config']
    state = ContactMotionTrackingState(d['root_committed'], d['root_velocity'],
        dict(zip(SEGMENTS, d['previous_correction'])), dict(zip(SEGMENTS, d['correction_velocity'])))
    model = {k: v for k, v in _hinges().items() if k.startswith('knee_')}
    kwargs = dict(root_target_m=d['root_target'], upstream_root_velocity_m_s=d['upstream_velocity'],
        dt_s=float(d['dt_s']), base_rotations_world=dict(zip(SEGMENTS, d['rotations'])),
        geometry=load_frozen_c2_3a().geometry, embedding=d['embedding'],
        hinge_projector=partial(project_hinge_corrections, model=model),
        previous_feet_world_m={k: v for k, v, active in zip(
            ('ankle_left', 'ankle_right'), d['previous_world_feet'], d['active']) if active})
    kwargs['foot_speed_limits_m_s'] = {k: .01 for k in kwargs['previous_feet_world_m']}
    with np.load(args.folder/'PROBE.npz') as p:
        names = p['joint_names'].tolist()
        previous_pose = p['pose_candidate'][-1].copy()
        native_previous = p['pose_baseline'][-1].copy()
    with np.load('logs/c2_passive_seated_repair_20260914_070933/full/CONTINUOUS_AB.npz') as p:
        native = p['joints_relative'][int(d['frame'])]
    knee_prior = {k: d['root_committed']+previous_pose[names.index(k)]+native[names.index(k)]-native_previous[names.index(k)]
                  for k in ('knee_left', 'knee_right')}
    if 'knee_motion_prior_m' in d:
        knee_prior = dict(zip(('knee_left', 'knee_right'), d['knee_motion_prior_m']))
    results = {}
    runs = [('hinge', ContactHingeMotionConfig(**config), dict(hinge_model=model, knee_motion_prior_m=knee_prior))]
    if not args.hinge_only:
        runs.insert(0, ('original', ContactMotionStepConfig(**config), {}))
    for label, solver_config, extra in runs:
        tick = time.monotonic()
        new, result = advance_contact_motion_tracking(state, solver_config=solver_config, **kwargs, **extra)
        results[label] = dict(accepted=result.accepted, reason=result.reason,
            wall_s=time.monotonic()-tick, violation_m=result.maximum_constraint_violation_m,
            projection=result.projection, iterations=result.nit,
            root_step_m=float(np.linalg.norm(new.root_position_m-state.root_position_m)))
    source_paths = [Path(__file__), *Path('src/biospur_fusion/c2_uwb_calibration').glob('contact*motion*.py')]
    report = dict(frame=int(d['frame']), time_s=float(d['time_s']), results=results,
        numpy=np.__version__, scipy=scipy.__version__, tracking_config=asdict(ContactMotionTrackingConfig()),
        model={k: asdict(v) for k, v in model.items()},
        source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths})
    text = json.dumps(report, indent=2, default=lambda v: v.tolist() if isinstance(v, np.ndarray) else str(v))
    (args.folder/args.report_name).write_text(text)
    print(text)


if __name__ == '__main__':
    main()
