#!/usr/bin/env python3
"""Bounded C2-only pilot; reference poses are never inputs to the solver."""
import argparse
import json
from pathlib import Path
import numpy as np

from biospur_fusion.c2_sparse_nodes.inputs import NODES,ROOT,sha
from biospur_fusion.c2_sparse_nodes.inertial import synchronized_inertial
from biospur_fusion.c2_sparse_nodes.inertial_replay import solve_stream


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--run',type=Path,required=True)
    ap.add_argument('--episode',default='06_elbow_left')
    ap.add_argument('--seconds',type=float,default=8.)
    ap.add_argument('--name',required=True)
    ap.add_argument('--calibration',default='CALIBRATION_ANATOMICAL_V3.json')
    args=ap.parse_args();out=args.run
    if args.episode.startswith('H'):raise ValueError('pilot is restricted to C2 calibration')
    if (out/(args.name+'.npz')).exists():raise ValueError('pilot artifact already exists')
    calibration=json.loads((out/args.calibration).read_text())
    with np.load(out/'CALIBRATION_CONTINUOUS_INPUT.npz',allow_pickle=False) as data:
        ep={n:{'imu':data[f'{args.episode}/{n}/imu']} for n in NODES}
    t,r,acc,valid=synchronized_inertial(ep,calibration)
    keep=t<=t[0]+args.seconds
    arr,audit=solve_stream(t[keep],r[keep],acc[keep],valid[keep],calibration,
        progress=lambda w:print(w['start'],w['success'],round(w['wall_s'],2),flush=True))
    np.savez_compressed(out/(args.name+'.npz'),**arr)
    audit.update(initialization='ISOLATED_NEUTRAL_PILOT_NOT_CONTINUOUS_CAPTURE_ACCEPTANCE',
        calibration_sha256=sha(out/args.calibration))
    (out/(args.name+'.json')).write_text(json.dumps(audit,indent=2)+'\n')


if __name__=='__main__':main()
