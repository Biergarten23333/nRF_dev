"""Matched fixed-calibration experiment; no ten-tag target, no H fitting."""
import json,time,copy
from pathlib import Path
import numpy as np
import torch
from biospur_fusion.c2_sparse_nodes.inputs import ROOT,sha
from biospur_fusion.c2_five_calibration.solver import solve_pose
from biospur_fusion.c2_five_calibration.workflow import fingerprint


def main(out):
    torch.set_num_threads(1)
    out=Path(out);base=ROOT/'logs/c2_body_feasibility_20260914_111213'
    def write(n,d): (out/n).write_text(json.dumps(d,indent=2,allow_nan=False))
    if (out/'CONTRACT.json').exists():raise ValueError('preserve existing experiment')
    bindings=json.loads((out/'BASELINE_BINDINGS.json').read_text())
    for p,h in bindings.items():
        if sha(Path(p))!=h:raise ValueError('baseline changed: '+p)
    g=json.loads((out/'BASE_GEOMETRY.json').read_text());trial=copy.deepcopy(g)
    trial['forearm_axial_envelope']=dict(limit_deg=100,scale_deg=20,weight=1.,
        status='experimental soft envelope; not personal ROM',
        convention='principal inverse TWIST; canonical test establishes negative forward axial angle',
        source='https://media.isbweb.org/images/documents/standards/isb_jcs_part_ii.pdf',
        numeric_limits_from_source=False)
    sources=fingerprint();write('GEOMETRY.json',trial)
    write('CONTRACT.json',dict(sources=sources,baseline=bindings,geometry_sha=sha(out/'GEOMETRY.json'),
        H_parameter_fit=False,ten_used=False,C2_iterations=30,H_iterations=80,wall_limit_s=600))
    reports={};started=time.monotonic()
    with np.load(base/'shared_candidate/CHECKPOINT.npz') as z:levers=z['levers'];c2warm=z['rotation']
    for stage in ('C2','H'):
        if stage=='C2':
            with np.load(base/'shared_candidate/ACCEPTED_PRIOR.npz') as z:q={k:z[k][::3] for k in z.files}
            warm=c2warm
        else:
            write('H_FROZEN.json',dict(C2=reports['C2'],sources=sources,geometry_sha=sha(out/'GEOMETRY.json')))
            path=base/'shared_holdout/H_REPLAY.npz'
            assert sha(path)==json.loads((base/'shared_holdout/RESULT.json').read_text())['output_sha256']
            with np.load(path) as z:q={k:z[k] for k in ('prior','observed','acceleration','valid','time_s')};warm=z['rotation']
        reports[stage]={}
        for name,geometry in [('control',g),('candidate',trial)]:
            rotation,a=solve_pose(**q,geometry=geometry,levers=levers,initial_rotation=warm,
                iterations=30 if stage=='C2' else 80,wall_limit_s=600-(time.monotonic()-started))
            np.savez_compressed(out/(stage+'_'+name+'.npz'),**q,rotation=rotation)
            reports[stage][name]=a;write(stage+'_REPORT.json',reports[stage]);print(stage,name,a['axial_excess_max_deg'],flush=True)
    for p,h in sources.items():
        if sha(ROOT/p)!=h:raise ValueError('producer changed')
    write('RESULT.json',dict(completed=True,elapsed_s=time.monotonic()-started,reports=reports,product_accepted=False))

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);main(p.parse_args().out)
