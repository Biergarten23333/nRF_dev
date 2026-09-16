#!/usr/bin/env python3
"""Verify actual C2 parameter dependencies and previously unused values."""
import argparse
import json
from pathlib import Path

import numpy as np

from biospur_fusion.c2_imucoco.workflow import load_input, INPUT_RUN, SURFACE
from biospur_fusion.c2_imucoco.preprocessing import prepare_stream
from biospur_fusion.c2_imucoco.protocol import FIT_PRODUCTS
from biospur_fusion.c2_sparse_nodes.calibration import calibrate
from biospur_fusion.c2_sparse_nodes.inputs import ROOT, sha


class IndexedReads(dict):
    """Trace the indexed episode reads used by the legacy numerical fit."""
    def __init__(self, values):
        super().__init__(values)
        self.reads = set()

    def __getitem__(self, key):
        self.reads.add(key)
        return super().__getitem__(key)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT/'logs'):
        parser.error('output must be under logs')
    out.mkdir(parents=True, exist_ok=True)
    target = out/'DEPENDENCY_AUDIT.json'
    if target.exists():
        raise ValueError('do not overwrite existing audit evidence')
    episodes = load_input(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz')
    surface = json.loads(SURFACE.read_text())
    traced = IndexedReads(episodes)
    full = calibrate(traced, surface)
    assert traced.reads == set(FIT_PRODUCTS), 'implemented action table no longer matches numerical fit'
    removed = sorted(set(episodes)-traced.reads-{'_continuous'})
    subset = calibrate({k:episodes[k] for k in traced.reads}, surface)
    identical = json.dumps(full, sort_keys=True) == json.dumps(subset, sort_keys=True)
    assert identical

    previous = ROOT/'logs/c2_imucoco_reproduction_20260906_154318/CALIBRATION_FROZEN.json'
    c = json.loads(previous.read_text())
    original = prepare_stream(episodes['00_initial_still'], c)['features']
    altered = dict(c, standing_elbow_bend_estimate_deg=[90., 90.],
                   lengths={key:2*value for key,value in c['lengths'].items()},
                   hinge_axes=[[1.,0.,0.]]*4)
    different = prepare_stream(episodes['00_initial_still'], altered)['features']
    np.testing.assert_array_equal(original, different)
    result = dict(status='IMPLEMENTATION_GAPS_CONFIRMED',
        parameter_fit_actions=sorted(traced.reads), unused_in_numerical_fit=removed,
        removal_of_unused_actions_changes_fitted_parameters=not identical,
        changing_stored_elbow_lengths_and_hinge_axes_changes_network_input=False,
        old_conditional_standing_elbow_deg=c['standing_elbow_bend_estimate_deg'],
        statement='These stored values do not enter the default encoder inputs; pose initialization is separately fixed to the upstream T-pose.',
        input_sha256=sha(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz'),
        previous_calibration_sha256=sha(previous), audit_source_sha256=sha(Path(__file__)),
        h_series_consumed=False, new_motion_accuracy_claim=False)
    target.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
