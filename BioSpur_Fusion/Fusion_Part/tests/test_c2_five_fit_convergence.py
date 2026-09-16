"""Whole-C2 fitting must distinguish convergence, a bound, and a round limit."""
import json

import numpy as np
import pytest

from biospur_fusion.c2_five_calibration import workflow
from biospur_fusion.c2_five_calibration.frontend import FIT


@pytest.mark.parametrize('mode,expected_rounds,converged,reason', [
    ('converge',2,True,'converged'), ('bound',1,False,'offset_bound'),
    ('budget',3,False,'round_budget'),
])
def test_whole_action_fit_stopping_conditions(tmp_path, monkeypatch, mode, expected_rounds, converged, reason):
    (tmp_path/'GEOMETRY.json').write_text(json.dumps({'nominal_sensor_levers_m':np.zeros((5,3)).tolist()}))
    (tmp_path/'TASK_CONTRACT.json').write_text(json.dumps(dict(fit_actions=sorted(FIT),validation_actions=[],
        fit_convergence=dict(max_rounds=3,offset_tolerance_m=.001))))
    (tmp_path/'REPRESENTATIVE.json').write_text(json.dumps({str(i):{'observed_rotation_max_element_error':0.} for i in range(3)}))
    q=dict(prior=np.zeros(2),observed=np.zeros(2),acceleration=np.zeros(2),valid=np.ones(2,dtype=bool),time_s=np.arange(2)/20)
    monkeypatch.setattr(workflow,'action_data',lambda out:{name+'_action':q for name in sorted(FIT)})
    monkeypatch.setattr(workflow,'fingerprint',lambda:{})
    calls=[]
    def solve(*args,**kwargs):
        calls.append(1)
        return np.zeros((2,24,3,3)),dict(observed_rotation_max_element_error=0.)
    monkeypatch.setattr(workflow,'solve_pose',solve)
    monkeypatch.setattr(workflow,'lever_system',lambda *args:None)
    fits=[]
    def fit(systems,nominal):
        assert len(systems)==len(FIT)
        fits.append(1)
        value=(.01 if len(fits)==1 else .0105) if mode=='converge' else .005*len(fits)
        return np.full((5,3),value),dict(active_bounds=[1 if mode=='bound' else 0]*15)
    monkeypatch.setattr(workflow,'fit_levers',fit)
    workflow.physical(tmp_path)
    report=json.loads((tmp_path/'PHYSICAL_CALIBRATION.json').read_text())
    assert len(fits)==expected_rounds
    assert len(calls)==expected_rounds*len(FIT)
    assert report['offset_update_converged'] is converged
    assert report['fit_stop_reason']==reason
