#!/usr/bin/env python3
"""Bounded physical refinement of a sealed arrived-only neural prefix.

Reuse the existing objective, coarse-to-fine optimizer, and bounded lever fit.
Previous physical output is a numerical warm start, never an extra likelihood.
"""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import sha
from biospur_fusion.c2_five_calibration.solver import solve_pose,lever_system,fit_levers


def main(source,out):
    source=source.resolve();out=out.resolve();out.mkdir(parents=True,exist_ok=False)
    result=json.loads((source/'RESULT.json').read_text())
    contract=json.loads((source/'CONTRACT.json').read_text())
    prior_path=source/'PREFIX_PRIOR.npz'
    if (not result.get('completed') or result['output_sha256']!=sha(prior_path)
            or contract['H_used'] or contract['ten_node_used']):
        raise ValueError('sealed C2-only prefix source required')
    geometry=json.loads((source/'GEOMETRY.json').read_text())
    with np.load(prior_path,allow_pickle=False) as z:
        if set(z.files)!={'time_s','prior','observed','acceleration','valid'}:
            raise ValueError('unexpected prefix fields')
        data={k:z[k][::3] for k in z.files}
    started=time.monotonic();nominal=np.asarray(geometry['nominal_sensor_levers_m'])
    (out/'CONTRACT.json').write_text(json.dumps(dict(kind='ARRIVED_PREFIX_PHYSICAL_PILOT',
        source=str(source),input_sha256=sha(prior_path),geometry_sha256=sha(source/'GEOMETRY.json'),
        iterations=30,wall_limit_s=600,H_used=False,ten_node_used=False,
        whole_arrived_continuous_support=True,final_C2_calibration=False),indent=2))
    rotation,first=solve_pose(**data,geometry=geometry,levers=nominal,iterations=30,
                              wall_limit_s=500.)
    (out/'FIRST_POSE_AUDIT.json').write_text(json.dumps(first,indent=2))
    np.savez_compressed(out/'FIRST_POSE.npz',rotation=rotation,time_s=data['time_s'],valid=data['valid'])
    systems=[lever_system(rotation,data['acceleration'],data['valid'],geometry)]
    levers,lever_audit=fit_levers(systems,nominal)
    (out/'LEVER_AUDIT.json').write_text(json.dumps(lever_audit,indent=2))
    remaining=600-(time.monotonic()-started)
    if remaining<=0:raise TimeoutError('physical prefix pilot budget')
    rotation,second=solve_pose(**data,geometry=geometry,levers=levers,iterations=30,
                               wall_limit_s=remaining,initial_rotation=rotation)
    np.savez_compressed(out/'PHYSICAL_PREFIX.npz',rotation=rotation,time_s=data['time_s'],
                        valid=data['valid'],levers=levers)
    (out/'RESULT.json').write_text(json.dumps(dict(completed=True,first=first,second=second,
        lever_audit=lever_audit,elapsed_seconds=time.monotonic()-started,
        output_sha256=sha(out/'PHYSICAL_PREFIX.npz'),calibration_accepted=False,
        interpretation='conditional prefix physical consistency, not pose accuracy or final calibration'),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args();torch.set_num_threads(1);main(a.source,a.out)
