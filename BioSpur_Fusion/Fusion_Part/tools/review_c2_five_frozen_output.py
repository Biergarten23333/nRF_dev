#!/usr/bin/env python3
"""One-way evaluation of a completed five-node run, outside its input boundary.

This tool may read ten-node references. Calibration and H inference must have
already finished; no evaluation artifact is written into the computation run.
"""
import argparse
import json
from pathlib import Path

from biospur_fusion.c2_sparse_nodes.inputs import sha
from biospur_fusion.c2_imucoco.workflow import write
from build_c2_five_calibration_review import main as build
from assess_c2_five_calibration import assess, assess_h

FILES = ('TASK_CONTRACT.json', 'FRONTEND.json', 'GEOMETRY.json', 'INITIAL_STATE.json',
         'C2_PRIOR.json', 'C2_PRIOR.npz', 'PHYSICAL_CALIBRATION.json',
         'PHYSICAL_CALIBRATION.npz', 'C2_VALIDATION.json', 'C2_VALIDATION.npz',
         'H_REPLAY.json', 'H_REPLAY.npz')


def review(source, out, *, previous_source=None):
    source, out = source.resolve(), out.resolve()
    if source == out or out.exists():
        raise ValueError('evaluation needs a new directory separate from computation')
    h = json.loads((source/'H_REPLAY.json').read_text())
    if h['output_sha256'] != sha(source/'H_REPLAY.npz'):
        raise ValueError('completed H output changed')
    for name, expected in h['frozen_inputs'].items():
        if sha(source/name) != expected:
            raise ValueError('frozen computation input changed: '+name)
    bindings={name:sha(source/name) for name in FILES}
    out.mkdir()
    for name in FILES:
        (out/name).symlink_to(source/name)
    write(out/'REVIEW_BOUNDARY.json',dict(source_run=str(source),compute_files_sha256=bindings,
        ten_node_read_scope='evaluation only, after frozen C2/H completion',
        calculation_parameters_selected_from_this_comparison=False))
    build(out,previous_source=previous_source)
    assess(out)
    build(out,with_h=True,previous_source=previous_source)
    assess_h(out)
    if any(sha(source/name)!=expected for name,expected in bindings.items()):
        raise ValueError('evaluation changed protected computation files')
    write(out/'COMPUTATION_UNCHANGED.json',dict(passed=True,compute_files_sha256=bindings))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--previous-source',type=Path)
    args=parser.parse_args()
    review(args.source,args.out,previous_source=args.previous_source)
