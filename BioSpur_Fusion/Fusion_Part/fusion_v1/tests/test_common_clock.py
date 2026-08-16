import numpy as np
from fusion_v1.io.common_clock import ClockModel

def test_affine_clock_integer_ns():
    m=ClockModel("N",0,999.5,100.0,20.0,0,100)
    assert m.map_ns([2,3]).tolist()==[2099,3098]
def test_individual_range_time_uses_half_round_trip():
    m=ClockModel("N",0,1000.0,0.0,20.0,0,10000)
    local=1000.0+np.array([1000.0,2000.0])/2
    assert m.map_ns(local).tolist()==[1500000,2000000]
