import numpy as np
import pytest
from biospur_fusion.c2_five_calibration.frontend import refined_initial_state


def test_exact_time_valid_proper_copy_only():
    c=dict(time_s=np.array([100.,100.05]),valid=np.ones(2,bool),rotation=np.tile(np.eye(3),(2,24,1,1)))
    initial=refined_initial_state(c,100.)
    initial[0,0,0]=2
    assert c['rotation'][0,0,0,0]==1
    with pytest.raises(ValueError,match='timestamp'):refined_initial_state(c,100.05)
    c['valid'][0]=False
    with pytest.raises(ValueError,match='timestamp'):refined_initial_state(c,100.)
    c['valid'][0]=True;c['rotation'][0,1,0,0]=-1
    with pytest.raises(ValueError,match='proper'):refined_initial_state(c,100.)
