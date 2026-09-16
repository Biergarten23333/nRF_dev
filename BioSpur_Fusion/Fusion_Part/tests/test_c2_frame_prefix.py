import copy

import numpy as np
import pytest

from test_c2_forearm_mount_robustness import fixture
from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_sparse_nodes.calibration import fit_forearm_frame, matrices, calibrate_forearm_mounts
from biospur_fusion.c2_five_calibration.progressive.frame_prefix import FramePrefixSession


def delivered(episodes, action):
    rows={}
    for node in NODES:
        a=episodes.get(action,{}).get(node,episodes['00_initial_still'][node])['imu'].copy()
        a[:,0]=a[:,0]-a[0,0]+40*EPISODES.index(action)
        rows[node]=a
    return rows


def test_single_limb_primitive_matches_batch_without_opposite_limb():
    episodes,calibration,_,_=fixture()
    initial=copy.deepcopy(calibration)
    batch=calibrate_forearm_mounts(episodes,calibration)
    for index,action in ((1,'06_elbow_left'),(2,'07_elbow_right')):
        node=NODES[index]
        mount,yaw,audit=fit_forearm_frame(episodes[action][node]['imu'],
            matrices(episodes['02_t_pose'][node]['imu']).as_matrix(),
            initial['initial_sensor_rotations'][index],left=index==1)
        np.testing.assert_allclose(mount,batch['segment_axes_in_sensor'][index],atol=1e-12)
        assert abs(yaw-batch['functional_yaw_rad'][index])<1e-12
        np.testing.assert_allclose(audit['axis_sensor_long'],batch['forearm_mount_calibration'][node]['axis_sensor_long'])


def test_prefix_has_no_future_limb_or_pelvis_frame_and_snapshots_are_immutable():
    episodes,_,mounts,_=fixture();session=FramePrefixSession()
    for action in EPISODES[:5]:
        session.ingest(action,delivered(episodes,action))
    before=session.snapshots
    assert all(f['mount'] is None for f in before[-1]['frames'].values())
    s=session.ingest('06_elbow_left',delivered(episodes,'06_elbow_left'))
    np.testing.assert_allclose(s['frames'][NODES[1]]['mount'],mounts[0],atol=1e-12)
    assert s['frames'][NODES[2]]['mount'] is None
    assert s['frames'][NODES[0]]['mount'] is None
    assert session.snapshots[:5]==before
    s['frames'][NODES[1]]['mount'][0][0]=123.
    assert session.snapshots[-1]['frames'][NODES[1]]['mount'][0][0]!=123.


def test_failed_update_rolls_back_and_can_retry_correct_data():
    episodes,_,_,_=fixture();session=FramePrefixSession()
    for action in EPISODES[:5]:session.ingest(action,delivered(episodes,action))
    previous=session.snapshots
    broken=delivered(episodes,'06_elbow_left');broken[NODES[1]][:,8:11]=0.
    with pytest.raises(ValueError,match='insufficient'):session.ingest('06_elbow_left',broken)
    assert session.snapshots==previous
    session.ingest('06_elbow_left',delivered(episodes,'06_elbow_left'))
    with pytest.raises(ValueError,match='exact next'):session.ingest('06_elbow_left',broken)
