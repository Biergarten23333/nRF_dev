"""Independent H comparison after whole-session calibration and frozen replay."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from c2_five_geometry_review import five_fk_to_output, compare_display_geometry
from biospur_fusion.c2_five_calibration.geometry import DISPLAY, joints_from_global
from biospur_fusion.c2_sparse_nodes.articulated_reference import verify_reference
from biospur_fusion.c2_sparse_nodes.inputs import ROOT, sha


def heading_aligned(points):
    points=points-points[:,0:1]
    hip=points[:,7]-points[:,10]
    angle=np.arctan2(hip[:,1],hip[:,0]);c=np.cos(angle)[:,None];s=np.sin(angle)[:,None]
    return np.stack((c*points[:,:,0]+s*points[:,:,1],
                     -s*points[:,:,0]+c*points[:,:,1],points[:,:,2]),-1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True)
    args=parser.parse_args();out=args.candidate.resolve()
    producer=json.loads((out/'H_PRODUCER.json').read_text())
    if not producer['completed'] or producer['H_used_for_fit'] or producer['reference_used']:
        raise ValueError('completed independent H producer required')
    if producer['output_sha256']!=sha(out/'H_REPLAY.npz'):
        raise ValueError('H output changed')
    if any(sha(out/name)!=value for name,value in producer['bindings'].items()):
        raise ValueError('frozen candidate changed')
    reference=verify_reference()
    review=ROOT/'logs/c2_direction_chain_20260914_133819/review'
    metadata=json.loads((review/'REVIEW.json').read_text())
    if metadata['reference'] != reference:
        raise ValueError('comparison page does not bind the current frozen v4 reference')
    page=review/'C2_H_HEADING_REPAIR_REVIEW.html'
    data,_=json.JSONDecoder().raw_decode(page.read_text().split('const D=',1)[1])
    geometry=json.loads((out/'GEOMETRY.json').read_text())
    report=dict(evaluation_only=True,reference=reference,review_sha256=sha(page),
        normalization='own pelvis origin and heading; no geometry scaling or time fitting',
        results={},tracking_accepted=False)
    with np.load(out/'H_REPLAY.npz') as z:
        for name in ('H01_boxing','H02_golf'):
            episode=data['episodes'][name];t=np.asarray(episode['sample_time_s'])
            ids=np.array([np.argmin(abs(z['time_s']-v)) for v in t])
            if np.max(abs(z['time_s'][ids]-t))>1e-5:
                raise ValueError('reference and candidate sample times differ')
            valid=np.asarray(episode['valid']) & z['valid'][ids]
            five=heading_aligned(five_fk_to_output(joints_from_global(torch.tensor(z['rotation'][ids]),geometry).numpy()[:,DISPLAY],
                metadata['output_frame'],source_space='SMPL_FK'))
            ten=heading_aligned(np.asarray(episode['ten'])[:,0])
            previous=heading_aligned(np.asarray(episode['five'])[:,0])
            error=np.linalg.norm(five-ten,axis=-1)[valid]*1000
            report['results'][name]=dict(mean_mm=float(error.mean()),p95_mm=float(np.percentile(error,95)),
                previous_mean_mm=float(1000*np.linalg.norm(previous-ten,axis=-1)[valid].mean()),
                geometry=compare_display_geometry(five,ten,valid))
    report['improves_both']=all(v['mean_mm']<v['previous_mean_mm'] for v in report['results'].values())
    (out/'H_EVALUATION.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({n:{k:v[k] for k in ('mean_mm','previous_mean_mm','p95_mm')}
                      for n,v in report['results'].items()},indent=2))


if __name__=='__main__':main()
