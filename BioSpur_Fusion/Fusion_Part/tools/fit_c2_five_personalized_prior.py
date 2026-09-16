#!/usr/bin/env python3
"""Freeze a five-only prior calibration before any H data is opened."""
import argparse
import json
from pathlib import Path

from biospur_fusion.c2_five_calibration.protocol_pose import build_bend_protocol
from biospur_fusion.c2_five_calibration.personalization import fit_flexion_prior
from biospur_fusion.c2_sparse_nodes.inputs import sha
from c2_five_continuous_review import load_continuous_calibration


def main(source, out):
    if out.exists():raise ValueError('new frozen personalization directory required')
    out.mkdir(parents=True)
    contracts,actions,_=load_continuous_calibration(source)
    geometry=json.loads((source/'GEOMETRY.json').read_text())
    protocol=build_bend_protocol(contracts,actions)
    result=fit_flexion_prior(actions,geometry,protocol)
    result['source_run']=str(source.resolve())
    result['source_binding']={n:sha(source/n) for n in (
        'C2_PRIOR.npz','C2_PRIOR.json','FRONTEND.json','GEOMETRY.json','SHARED_CALIBRATION.json')}
    result['implementation_binding']={str(p):sha(p) for p in [Path(__file__),
        Path('src/biospur_fusion/c2_five_calibration/personalization.py'),
        Path('src/biospur_fusion/c2_five_calibration/protocol_pose.py')]}
    (out/'PERSONALIZATION.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    main(args.source,args.out)
