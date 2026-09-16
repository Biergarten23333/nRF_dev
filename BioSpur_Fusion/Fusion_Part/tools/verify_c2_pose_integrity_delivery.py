"""Verify exported coordinates and render the user's exact T-pose frame."""
import json
import re
import subprocess
from pathlib import Path
import numpy as np
from biospur_fusion.c2_coupled_progressive.renderer import render_triptych


def bend(pose, names, side):
    shoulder, elbow, wrist = [pose[names.index(k+'_'+side)] for k in ('shoulder', 'elbow', 'wrist')]
    a, b = elbow-shoulder, wrist-elbow
    return float(np.degrees(np.arccos(np.clip(a@b/np.linalg.norm(a)/np.linalg.norm(b), -1, 1))))


def main():
    root = Path('logs/c2_pose_integrity_20260914')
    with np.load(root/'continuous_v3/POSE.npz') as data:
        times, poses, names = data['time_s'], data['joints_relative'], data['joint_names'].tolist()
        assert np.all(np.isfinite(poses)) and np.all(np.diff(times)>0)
        i = int(np.argmin(abs(times-234897.63229673)))
        angles = {s:bend(poses[i],names,s) for s in ('left','right')}
        assert max(angles.values()) < 10, angles
        lengths = {}
        for side in ('left','right'):
            for a,b in (('shoulder','elbow'),('elbow','wrist'),('hip','knee'),('knee','ankle')):
                length = np.linalg.norm(poses[:,names.index(a+'_'+side)]-poses[:,names.index(b+'_'+side)],axis=1)
                lengths[a+'_'+b+'_'+side] = float(np.ptp(length))
        assert max(lengths.values()) < 1e-10
        render_triptych(dict(zip(names,poses[i])), root/'full_pose_delivery/TPOSE_EXPORTED.png',
                        'Exported continuous pose, screenshot time 65.444 s')
    html = (root/'full_pose_delivery/FULL_POSE_AB.html').read_text()
    scripts = re.findall(r'<script[^>]*>(.*?)</script>',html,re.S)
    assert len(scripts)==1
    subprocess.run(['node','--check'],input=scripts[0],text=True,check=True,timeout=15)
    report = dict(frames=len(times), screenshot_index=i, elbow_bend_deg=angles,
                  maximum_bone_length_variation_m=max(lengths.values()),
                  javascript_syntax_pass=True, browser_runtime_verified=False,
                  browser_blocker='Local file navigation rejected by browser URL security policy',
                  full_biomechanical_acceptance=False)
    contact = root/'contact70/PROBE.npz'
    if contact.exists():
        with np.load(contact) as data:
            j = int(np.argmin(abs(data['time_s']-234897.63229673)))
            pose = data['pose_candidate'][j]
            report['after_contact_elbow_bend_deg'] = {s:bend(pose,names,s) for s in ('left','right')}
            assert max(report['after_contact_elbow_bend_deg'].values()) < 10
            render_triptych(dict(zip(names,pose)),root/'contact70/TPOSE_AFTER_CONTACT.png',
                            'Recalibrated pose after contact solver, 65.444 s')
    (root/'full_pose_delivery/VERIFICATION.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
