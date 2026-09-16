"""Incomplete or nonfinite comparisons must never count as successful replay."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('five_assessment',
    Path(__file__).resolve().parents[1]/'tools/assess_c2_five_calibration.py')
assessment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assessment)


@pytest.mark.parametrize('values', [[0.,0.,0.], [0.,float('nan'),0.,0.], [0.,0.,-1.,0.]])
def test_invalid_joint_results_fail_closed(values):
    with pytest.raises(ValueError, match='four finite'):
        assessment.angle_failures({'H01':{'compared_frames':100,'mae':values}}, [('mae',15.)])


def test_missing_comparison_frames_do_not_pass():
    with pytest.raises(ValueError, match='no valid frames'):
        assessment.angle_failures({'H01':{'compared_frames':0,'mae':[0.]*4}}, [('mae',15.)])


def test_failed_joint_is_retained_in_verdict():
    failures=assessment.angle_failures({'H01':{'compared_frames':100,'mae':[10.,27.5,4.,6.]}}, [('mae',15.)])
    assert failures==[dict(action='H01',joint=1,metric='mae',value=27.5,limit=15.)]
