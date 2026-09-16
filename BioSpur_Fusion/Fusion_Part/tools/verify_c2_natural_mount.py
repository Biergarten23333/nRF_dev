"""Report measured bends at both user-identified frames after mounting repair."""
import argparse
import json
from pathlib import Path
import numpy as np
from biospur_fusion.c2_coupled_progressive.renderer import render_triptych


def bends(pose, names):
    result = {}
    for side in ('left', 'right'):
        for joint, chain in [('elbow', ('shoulder','elbow','wrist')),
                             ('knee', ('hip','knee','ankle'))]:
            a,b,c = [pose[..., names.index(k+'_'+side), :] for k in chain]
            u,v = b-a,c-b
            value = np.degrees(np.arccos(np.clip(np.sum(u*v,axis=-1)/
                     np.linalg.norm(u,axis=-1)/np.linalg.norm(v,axis=-1),-1,1)))
            result[joint+'_'+side] = value
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('run',type=Path)
    ap.add_argument('--contact-subdir',default='contact70'); args=ap.parse_args()
    with np.load(args.run/args.contact_subdir/'PROBE.npz') as data:
        times=data['time_s']; pose=data['pose_candidate']; names=data['joint_names'].tolist()
        assert np.isfinite(pose).all() and np.all(np.diff(times)>0)
        rows={}
        for seconds,label in [(16.847,'NATURAL_STILL'),(65.444,'TPOSE')]:
            i=int(np.argmin(abs(times-times[0]-seconds)))
            rows[label]={k:float(v) for k,v in bends(pose[i],names).items()}
            render_triptych(dict(zip(names,pose[i])),args.run/(label+'.png'),
                            f'{label} actual contact output {seconds}s')
        # These are regression checks, not physiological ground truth.
        assert max(rows['TPOSE']['elbow_'+s] for s in ('left','right'))<10
        assert min(rows['NATURAL_STILL']['elbow_'+s] for s in ('left','right'))>1
        rows['scope']='70s numerical regression, not full anatomical validation'
        (args.run/'FRAME_VERIFICATION.json').write_text(json.dumps(rows,indent=2))
        print(json.dumps(rows,indent=2))


if __name__=='__main__':main()
