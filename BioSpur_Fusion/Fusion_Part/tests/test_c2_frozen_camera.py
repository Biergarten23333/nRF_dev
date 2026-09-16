"""Compare the executed viewer projection with the frozen C2 implementation.

An asymmetric pose and all three basis vectors expose a screen reflection that
joint angles, proper world rotations, and point-transform determinants miss.
"""
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest

from biospur_fusion.c2_sparse_nodes.viewer import write_viewer


ROOT = Path(__file__).resolve().parents[1]


def test_projection_matches_frozen_baseline_across_world_yaw_and_viewpoints():
    baseline = (ROOT/'tools/build_c2_avatar_interactive.py').read_text()
    oracle = baseline.split('function project(x,y,z,w,h)', 1)[1].split('\n', 1)[0]
    oracle = 'function frozenProject(x,y,z,w,h)' + oracle.replace('{{', '{').replace('}}', '}')
    template = (ROOT/'src/biospur_fusion/c2_sparse_nodes/viewer_template.html').read_text()
    implementation = 'function cameraCoordinates(p)' + template.split(
        'function cameraCoordinates(p)', 1)[1].split('function project(p,w,h)', 1)[0]
    # Left forearm forward and right forearm down; identities are deliberately
    # unequal. These are already post-FK, post-reflection Cartesian points.
    points = [[.2, 0, .5], [.2, 0, .2], [.2, .3, .2],
              [-.2, 0, .5], [-.2, 0, .2], [-.2, 0, -.1],
              [1, 0, 0], [0, 1, 0], [0, 0, 1]]
    script = oracle + implementation + '''
const D={camera_convention:'C2_FROZEN'},camera={};
let yaw=0,pitch=0,zoom=1;const rows=[];
for(const delta of [0,-Math.PI/2,.7])for(yaw of [0,.4,Math.PI,2.1])
for(pitch of [-.6,0,.8])for(const p of POINTS){
  const c=Math.cos(delta),s=Math.sin(delta);
  const q=[c*p[0]-s*p[1],s*p[0]+c*p[1],p[2]];
  camera.yaw=Math.PI/2-yaw+delta;camera.pitch=-pitch;
  const frozen=frozenProject(...p,100,100);
  const expected=[(frozen[0]-50)/43,(72-frozen[1])/43,frozen[2]];
  rows.push({expected,actual:cameraCoordinates(q)});
}
process.stdout.write(JSON.stringify(rows));
'''
    result = subprocess.run(['node'], input='const POINTS='+json.dumps(points)+';\n'+script,
                            text=True, capture_output=True, check=True)
    rows = json.loads(result.stdout)
    np.testing.assert_allclose([r['actual'] for r in rows],
                               [r['expected'] for r in rows], atol=1e-14)


def test_unknown_camera_convention_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='camera convention'):
        write_viewer(tmp_path/'invalid.html', {'camera_convention': 'unregistered'})
