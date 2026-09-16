#!/usr/bin/env python3
"""One frozen C2-only prior experiment; no reference is opened here."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_five_calibration.personalization import personalize_prior
from biospur_fusion.c2_five_calibration.solver import solve_pose
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN
from biospur_fusion.c2_imucoco.upstream import verify_assets
from biospur_fusion.c2_sparse_nodes.inputs import ROOT, sha


def main(out):
    torch.set_num_threads(1)
    frozen_path = out/'PERSONALIZATION.json'
    frozen_hash = sha(frozen_path)
    fit = json.loads(frozen_path.read_text())
    source = Path(fit['source_run'])
    if (out/'H_START.json').exists():
        raise ValueError('preserve completed or interrupted H attempt')
    if fit['H_used_for_fit'] or fit['ten_node_used']:
        raise ValueError('five-only C2 fit required')
    bindings = {source/name: expected for name, expected in fit['source_binding'].items()}
    bindings.update({ROOT/name: expected for name, expected in fit['implementation_binding'].items()})
    record = json.loads((source/'H_REPLAY.json').read_text())
    bindings[source/'H_REPLAY.json'] = sha(source/'H_REPLAY.json')
    bindings[source/'H_REPLAY.npz'] = record['output_sha256']
    bindings.update({source/name: expected for name, expected in record['frozen_inputs'].items()})
    bindings.update({INPUT_RUN/name: expected for name, expected in record['input_sha256'].items()})
    # Reuse only the unchanged neural producer. Physical/calibration optimizer
    # sources are intentionally outside this cache dependency set.
    neural_files = {'frontend.py', 'navigation.py', 'placement.py', 'geometry.py'}
    for name, expected in record['source_sha256'].items():
        path = Path(name)
        if 'c2_imucoco' in path.parts or (path.parent.name == 'c2_five_calibration' and path.name in neural_files):
            bindings[ROOT/name] = expected
    for path, expected in bindings.items():
        if sha(path) != expected:
            raise ValueError('frozen input or neural dependency changed: '+str(path))
    verify_assets()
    sources = {str(p):sha(p) for p in (ROOT/'src/biospur_fusion/c2_five_calibration').glob('*.py')}
    sources[str(Path(__file__))] = sha(Path(__file__))
    start = dict(personalization_sha256=frozen_hash,
        verified_reuse_bindings={str(p):v for p,v in bindings.items()}, source_sha256=sources,
        iterations=150, wall_limit_s=120, H_parameter_updates=False,
        reference_opened=False, old_physical_output_reused=False)
    (out/'H_START.json').write_text(json.dumps(start, indent=2))
    with np.load(source/'H_REPLAY.npz', allow_pickle=False) as archive:
        q = {k:archive[k] for k in ('time_s','prior','observed','acceleration','valid')}
    geometry = json.loads((source/'GEOMETRY.json').read_text())
    levers = json.loads((source/'SHARED_CALIBRATION.json').read_text())['fitted_sensor_levers_m']
    prior, audit = personalize_prior(q['prior'], q['observed'], geometry, fit)
    rotation, physical = solve_pose(prior, q['observed'], q['acceleration'], q['valid'],
        q['time_s'], geometry, levers, iterations=150, wall_limit_s=120)
    if sha(frozen_path) != frozen_hash or any(sha(p)!=v for p,v in bindings.items()):
        raise ValueError('frozen input changed during H')
    if any(sha(Path(p))!=v for p,v in sources.items()):
        raise ValueError('source changed during H')
    np.savez_compressed(out/'H_CANDIDATE.npz', **q, personalized_prior=prior, rotation=rotation)
    result = dict(status='EXPERIMENT_PENDING_DOWNSTREAM_COMPARISON', product_accepted=False,
        start_sha256=sha(out/'H_START.json'), output_sha256=sha(out/'H_CANDIDATE.npz'),
        personalization=audit, physical=physical, offline_noncausal=True,
        H_used_for_fit=False, ten_node_used_for_fit=False)
    (out/'H_CANDIDATE.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    main(parser.parse_args().out)
