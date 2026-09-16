"""Inspect actual endpoint changes, not just unsigned joint angles."""
import argparse
import json
from pathlib import Path
import numpy as np
from biospur_fusion.c2_coupled_progressive.renderer import render_triptych


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--candidate',type=Path,required=True)
    ap.add_argument('--previous',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--fit',type=Path)
    args=ap.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
    with np.load(args.candidate/'POSE.npz') as z:
        t=z['time_s']; pose=z['joints_relative']; names=z['joint_names'].tolist()
    with np.load(args.previous/'POSE.npz') as z:
        np.testing.assert_array_equal(t,z['time_s']); old=z['joints_relative']
    assert np.isfinite(pose).all()
    report={}
    for seconds in (16.847,47.575,65.444):
        i=int(np.argmin(abs(t-t[0]-seconds)))
        report[str(seconds)]={name:float(np.linalg.norm(pose[i,j]-old[i,j]))
                             for j,name in enumerate(names)}
        for label,values in [('previous',old),('candidate',pose)]:
            render_triptych(dict(zip(names,values[i])),args.output/f'{seconds}_{label}.png',
                            f'{label} actual FK at {t[i]-t[0]:.3f}s')
    report['scope']='actual continuous posture; contact and absolute positioning not certified'
    if args.fit:
        from tools.verify_c2_natural_mount import bends
        with np.load(args.fit/'PRE_IK.npz') as fitted:
            episodes=sorted({k.split('/')[1] for k in fitted.files if k.startswith('trajectory/')})
            for episode in episodes:
                clock=fitted[f'trajectory/{episode}/pelvis/time_root_s']
                lo,hi=np.searchsorted(t,[clock[0],clock[-1]])
                hi=min(hi+1,len(t))
                chosen={str(p):lo+int((hi-lo-1)*p/100) for p in (25,50,75)}
                chosen.update({key+'_max':lo+int(np.argmax(value))
                               for key,value in bends(pose[lo:hi],names).items()})
                for label,i in chosen.items():
                    render_triptych(dict(zip(names,pose[i])),args.output/f'{episode}_{label}.png',
                                    f'action index {episode} {label}, actual {t[i]-t[0]:.3f}s')
    (args.output/'ENDPOINT_CHANGES.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
