import numpy as np
import pytest
from c2_arm_smoke_fixture import fixture
from c2_arm_joint_smoke import prepare, fit, wear_branch
from biospur_fusion.c2_sparse_nodes.inputs import NODES


def test_only_five_nodes_and_separate_truth_are_exposed():
    actions,truth=fixture()
    assert all(set(q)==set(NODES) for q in actions.values())
    assert set(truth)=={'mounts','heading'}
    actions['H01_boxing']=actions.pop('02_t_pose')
    with pytest.raises(ValueError,match='no H'):prepare(actions,0)


def test_phase_mutation_changes_constraints_and_clock_mutation_fails():
    actions,_=fixture()
    a,_=prepare(actions,0);b,_=prepare(actions,0,True)
    np.testing.assert_allclose(abs(a[0]@b[1]),1.,atol=1e-10)
    assert abs(a[0]@b[0])<.01
    actions['02_t_pose'][NODES[1]]['imu'][10,0]+=.001
    with pytest.raises(ValueError,match='sample times'):prepare(actions,0)


def test_fit_does_not_mutate_input_or_consume_truth():
    actions,_=fixture()
    before={k:{n:v['imu'].copy() for n,v in q.items()} for k,q in actions.items()}
    result=fit(actions,0,np.zeros(4))
    assert result['success'] and np.isfinite(result['cost'])
    for k,q in actions.items():
        for n,v in q.items():np.testing.assert_array_equal(v['imu'],before[k][n])


def test_qualitative_wear_guard_rejects_axial_half_turn_without_exact_angle_target():
    from scipy.spatial.transform import Rotation
    _,truth=fixture()
    for limb,mount in enumerate(truth['mounts']):
        assert wear_branch(mount,limb)
        assert not wear_branch(mount@Rotation.from_euler('z',np.pi).as_matrix(),limb)
        assert wear_branch(mount@Rotation.from_euler('x',.3).as_matrix(),limb)
