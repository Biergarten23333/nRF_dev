#!/usr/bin/env python3
"""All recorded C2 action-window proposal A/B; no candidate is adopted.

Each variant starts from the same existing five-only checkpoint. Neural
predictions stay fixed here. Any useful proposal still requires a complete
continuous, freshly encoded neural replay before calibration acceptance.
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_five_calibration.arm_protocol import build_arm_protocol
from biospur_fusion.c2_five_calibration.calibration_prior import RegisteredHeadingPrior
from biospur_fusion.c2_five_calibration.shared_fit import _optimize
from biospur_fusion.c2_five_calibration.shared_orientation import transport_heading
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN, load_input
from biospur_fusion.c2_sparse_nodes.inputs import sha
from c2_five_continuous_review import load_continuous_calibration
from biospur_fusion.c2_five_calibration.protocol_pose import build_bend_protocol


def main(source, out, *, bend_protocol=False):
    if out.exists():
        raise ValueError('new proposal audit directory required')
    out.mkdir(parents=True)
    contract = dict(status='RUNNING', source=str(source.resolve()), iterations=60,
        case_budget_s=240, variants=[False, True], bend_protocol=bend_protocol,
        calibration_adopted=False, neural_replay_refreshed=False, H_opened=False,
        ten_node_reference_opened=False, all_recorded_actions_required=19,
        scope='action-window proposal diagnostic; not the full continuous calibration',
        source_sha256=sha(Path(__file__)))
    (out/'CONTRACT.json').write_text(json.dumps(contract, indent=2))
    contracts, actions, outputs = load_continuous_calibration(source)
    geometry = json.loads((source/'GEOMETRY.json').read_text())
    calibration = json.loads((source/'FRONTEND.json').read_text())
    record = json.loads((source/'SHARED_CALIBRATION.json').read_text())
    registered = RegisteredHeadingPrior(calibration['heading_factors'],
                                         calibration['frozen_heading_correction_rad'])
    episodes = load_input(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz')
    protocol = build_arm_protocol(episodes, calibration, contracts, actions,
                                 registered.all_information, conditional_only=True)
    registered.bind_arm_protocol(protocol, actions)
    bends=build_bend_protocol(contracts,actions) if bend_protocol else None
    objectives = {n:PoseObjective(**q, geometry=geometry) for n, q in actions.items()}
    initial = {n:o.parameters_from_rotation(outputs[n+'/rotation']) for n,o in objectives.items()}
    nominal = torch.as_tensor(geometry['nominal_sensor_levers_m'], dtype=torch.float64)
    levers = torch.as_tensor(record['fitted_sensor_levers_m'], dtype=torch.float64)
    rows = []
    for variant in contract['variants']:
        refresh=False if bend_protocol else variant
        active_bends=bends if bend_protocol and variant else None
        start = time.monotonic()
        fit = _optimize(objectives, initial, levers, torch.zeros(4, dtype=torch.float64),
                        nominal, registered, iterations=60, deadline=start+240, shared=True,
                        refresh_projection=refresh,pose_protocol=active_bends)
        scores = {}
        for score_refresh in (False, True):
            total = 0.
            with torch.no_grad():
                for n, objective in objectives.items():
                    obs, acc = transport_heading(objective.observed, objective.acceleration, fit['delta'])
                    rotation, terms = objective.evaluate(fit['parameters'][n], fit['levers'],
                        observed=obs, acceleration=acc, refresh_projection=score_refresh)
                    total += float(terms['loss'])/len(objectives)
                    total += float(registered.energy_for_action(n, fit['delta'], rotation))
                    if bends is not None:
                        total += float(bends.energy_for_action(n,fit['parameters'][n]))
                total += float(registered.energy(fit['delta'])+((fit['levers']-nominal)/.025).square().sum())
            scores['refreshed_projection' if score_refresh else 'frozen_projection'] = total
        row = dict(refresh_projection=refresh, bend_protocol=active_bends is not None,
                   heading_increment_deg=torch.rad2deg(fit['delta']).tolist(),
                   bend_intent_audit=None if bends is None else bends.audit(),
                   bend_phase_means_deg={} if bends is None else {r.action:float(torch.rad2deg(
                       fit['parameters'][r.action][r.index,3+r.limb]).mean()) for r in bends.rows},
                   surrogate_energy=fit['energy'], common_scores=scores, selected_step=fit['step'],
                   wall_s=time.monotonic()-start)
        rows.append(row)
        print(json.dumps(row), flush=True)
        (out/'RESULT.json').write_text(json.dumps(dict(contract=contract, variants=rows, complete=False), indent=2))
    contract['status']='PROPOSAL_DIAGNOSTIC_COMPLETE_NOT_ADOPTED'
    (out/'RESULT.json').write_text(json.dumps(dict(contract=contract, variants=rows, complete=True), indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--bend-protocol', action='store_true')
    args = p.parse_args()
    torch.set_num_threads(1)
    main(args.source, args.out,bend_protocol=args.bend_protocol)
