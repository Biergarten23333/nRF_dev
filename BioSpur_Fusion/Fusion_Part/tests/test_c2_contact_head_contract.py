"""Check semantics against the pinned author's label function, not a copy."""
import ast
from pathlib import Path
import torch
from biospur_fusion.c2_imucoco.contact_contract import contact_head_contract


def test_released_label_accepts_slow_moving_points_far_above_ground():
    source=Path(__file__).resolve().parents[1]/'third_party/imucoco/data_generation.py'
    tree=ast.parse(source.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_foot_ground_probs')
    module=ast.Module(body=[node],type_ignores=[])
    namespace={'torch':torch};exec(compile(module,str(source),'exec'),namespace)
    contract=contact_head_contract()
    joint=torch.zeros(4,24,3);joint[:,:,1]=2.
    joint[:,:,0]=torch.arange(4)[:,None]*(.2/contract['label_sample_rate_hz'])
    labels=namespace['_foot_ground_probs'](joint)
    assert torch.equal(labels[1:],torch.ones(3,2,dtype=labels.dtype))
    assert not contract['label_is_zero_velocity']
    assert not contract['label_tests_ground_height']
    assert not contract['shank_stationarity_implied']
    joint[:,:,0]=torch.arange(4)[:,None]*(.6/contract['label_sample_rate_hz'])
    labels=namespace['_foot_ground_probs'](joint)
    assert torch.count_nonzero(labels)==0
    for ratio,expected in ((.99,1),(1.01,0)):
        joint[:,:,0]=torch.arange(4)[:,None]*contract['label_displacement_threshold_m']*ratio
        labels=namespace['_foot_ground_probs'](joint)
        assert torch.all(labels[1:]==expected)
