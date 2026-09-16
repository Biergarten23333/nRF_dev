"""Interpolated gap poses cannot act as calibration or tracking evidence."""
import numpy as np
import pytest
import torch

from biospur_fusion.c2_five_calibration.geometry import OBSERVED
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from test_c2_joint_kinematics import geometry


def objective():
    count=90
    prior=np.tile(np.eye(3),(count,24,1,1))
    valid=np.ones(count,dtype=bool)
    valid[35:48]=False
    return PoseObjective(prior,prior[:,OBSERVED],np.zeros((count,5,3)),valid,
                         np.arange(count)/20,geometry())


def test_invalid_pose_changes_do_not_change_any_loss_or_valid_gradient():
    owner=objective()
    levers=torch.zeros(5,3,dtype=torch.float64)
    before=(owner.initial+.03).requires_grad_()
    after=before.detach().clone()
    after[~owner.valid]+=torch.linspace(-.9,.9,int((~owner.valid).sum()))[:,None]
    after.requires_grad_()
    rotation_a,terms_a=owner.evaluate(before,levers)
    rotation_b,terms_b=owner.evaluate(after,levers)
    assert not torch.equal(rotation_a[~owner.valid],rotation_b[~owner.valid])
    for key in terms_a:torch.testing.assert_close(terms_a[key],terms_b[key],atol=1e-12,rtol=0)
    grad_a=torch.autograd.grad(terms_a['loss'],before)[0]
    grad_b=torch.autograd.grad(terms_b['loss'],after)[0]
    torch.testing.assert_close(grad_a,grad_b,atol=1e-12,rtol=0)
    assert torch.count_nonzero(grad_a[~owner.valid])==0
    assert torch.count_nonzero(grad_a[owner.valid])>0


def test_valid_pose_changes_still_incur_a_loss():
    owner=objective()
    levers=torch.zeros(5,3,dtype=torch.float64)
    _,baseline=owner.evaluate(owner.initial,levers)
    changed=owner.initial.clone()
    changed[15:20,:3]+=.5
    _,result=owner.evaluate(changed,levers)
    assert result['loss']>baseline['loss']+.001


@pytest.mark.parametrize('mask',[np.ones(90),np.ones(89,dtype=bool)])
def test_bad_validity_cannot_be_coerced_into_support(mask):
    prior=np.tile(np.eye(3),(90,24,1,1))
    with pytest.raises(ValueError,match='boolean validity'):
        PoseObjective(prior,prior[:,OBSERVED],np.zeros((90,5,3)),mask,np.arange(90)/20,geometry())
