import copy
import numpy as np
import torch
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_imucoco.preprocessing import prepare_stream
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_five_calibration.sensor_bias import bias_system
from biospur_fusion.c2_five_calibration.solver import acceleration_residual, valid_support
from biospur_fusion.c2_five_calibration.acceleration_metric import metric_residual
from test_c2_joint_kinematics import geometry


def fixture():
    rng=np.random.default_rng(42)
    c=dict(initial_sensor_rotations=np.tile(np.eye(3),(5,1,1)),pelvis_closure_rad=0.,
           functional_yaw_rad=[.1,-.2,.3,-.4,.5],
           segment_axes_in_sensor=Rotation.random(5,random_state=rng).as_matrix(),
           acc_bias_sensor=rng.normal(0,.1,(5,3)))
    episode={}
    for i,node in enumerate(NODES):
        t=100+np.arange(500)*.005+i*.0007
        if i==2:t=t[(t<101)|(t>101.05)]
        r=Rotation.from_rotvec(np.stack([.3*np.sin(t*2),.2*np.cos(t),.4*np.sin(t*3)],axis=1))
        rows=np.zeros((len(t),11));rows[:,0]=t
        rows[:,1:5]=r.as_quat()[:,[3,0,1,2]]
        rows[:,5:8]=rng.normal(size=(len(t),3))
        episode[node]={'imu':rows}
    return episode,c


def test_bias_transport_matches_raw_reprocessing_without_changing_orientation():
    episode,c=fixture(); old=prepare_stream(episode,c)
    base=prepare_stream(episode,c,include_bias_transport=True)
    for key in old:np.testing.assert_array_equal(base[key],old[key])
    delta=np.arange(15).reshape(5,3)*.013-.07
    new=copy.deepcopy(c);new['acc_bias_sensor']=c['acc_bias_sensor']+delta
    fresh=prepare_stream(episode,new)
    predicted=base['acceleration_mps2']-np.einsum('nsij,sj->nsi',base['sensor_bias_response'],delta)
    np.testing.assert_allclose(predicted,fresh['acceleration_mps2'],atol=1e-12)
    np.testing.assert_array_equal(base['orientation'],fresh['orientation'])
    np.testing.assert_array_equal(base['input_valid'],fresh['input_valid'])


def test_bias_system_sign_filter_and_shared_reference_metric():
    torch.set_num_threads(1)
    episode,c=fixture();q=prepare_stream(episode,c,include_bias_transport=True)
    r=torch.eye(3,dtype=torch.float64).repeat(len(q['time_s']),24,1,1)
    a=q['acceleration_mps2'];response=q['sensor_bias_response'];valid=q['input_valid']
    delta=np.arange(15).reshape(5,3)*.01-.1
    for metric in ('legacy_independent_differences','shared_reference_equal_node_variance_v1'):
        g=geometry();g['acceleration_observation_model']=metric
        A,b=bias_system(r,a,response,valid,g,np.zeros((5,3)))
        changed=torch.tensor(a-np.einsum('nsij,sj->nsi',response,delta))
        direct=metric_residual(acceleration_residual(r,changed,g,np.zeros((5,3))),g,node_axis=2)
        np.testing.assert_allclose(A@delta.ravel()-b,direct[valid_support(valid)].numpy().ravel(),atol=1e-11)
