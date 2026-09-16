from types import SimpleNamespace
import pytest
import torch

from biospur_fusion.c2_five_calibration.protocol_step import bend_search_direction, search_bend_step


def test_direction_preserves_other_coordinates_and_time_boundaries():
    p=torch.zeros(101,21,dtype=torch.float64);p[:,3:7]=.2
    t=torch.linspace(0,10,101,dtype=p.dtype)
    protocol=SimpleNamespace(rows=[SimpleNamespace(action='06_elbow_left',limb=0,interval=(2.,8.))],nominal_bend_rad=1.)
    d=bend_search_direction(protocol,p,t)
    assert torch.count_nonzero(d[:,[i for i in range(21) if i!=3]])==0
    assert torch.count_nonzero(d[(t<=2)|(t>=8)])==0
    assert d[50,3]==.8
    assert torch.all((p+d)[:,3:7]>=0)
    protocol.rows[0].action='H01_boxing'
    with pytest.raises(ValueError,match='forbidden'):bend_search_direction(protocol,p,t)


def test_full_energy_selects_partial_step_not_intent_target():
    p=torch.zeros(2,9,dtype=torch.float64);d=p.clone();d[:,3]=1.
    def evaluate(q):return ((q[:,3]-.25)**2).sum(),0.
    result,audit=search_bend_step(evaluate,p,d,torch.ones(4)*2)
    assert torch.all(result[:,3]==.25)
    assert audit['selected_fraction']==.25
    assert not audit['calibration_accepted']


def test_lower_energy_penetration_rejected_and_no_improvement_keeps_input():
    p=torch.zeros(2,9,dtype=torch.float64);d=p.clone();d[:,3]=1.
    def evaluate(q):return -q[:,3].sum(),float(q[0,3])*.01
    result,audit=search_bend_step(evaluate,p,d,torch.ones(4)*2)
    assert torch.equal(result,p)
    assert audit['selected_fraction']==0
    with pytest.raises(ValueError,match='bounds'):
        search_bend_step(evaluate,p,-d,torch.ones(4)*2)
