from collections import deque
import numpy as np
from biospur_fusion.c2_uwb_root_world.continuous_support import ContinuousSupportVelocity
from biospur_fusion.c2_uwb_root_world.protocol_contact import ProtocolContactStream

def owner(enabled=True):
    stream=ProtocolContactStream.__new__(ProtocolContactStream)
    stream.maximum_age_s=.0075;stream.lifecycle=None
    stream.history=deque([(1.,{'left':(1.,1.,3,.5),'right':(1.,1.,3,.5)},None)])
    stream.point_history=deque([(1.,{'left':(1.,1.,True,.5,True),'right':(1.,1.,False,0.,False)})])
    x=ContinuousSupportVelocity.__new__(ContinuousSupportVelocity)
    x.protocol=stream;x.points=object();x.soft_support_position_guard=enabled
    x.advance_actual_samples=lambda query:None
    return x

def test_fresh_soft_position_does_not_create_stationary_velocity_evidence():
    x=owner()
    assert not x.protocol.evidence(1.001)[0].any()
    assert x.position_is_considered(1.001)
    assert not x.protocol.evidence(1.001)[0].any()

def test_stale_future_pose_and_explicit_lift_release():
    x=owner()
    assert not x.position_is_considered(.999)
    assert not x.position_is_considered(1.008)
    x.protocol.point_history.append((1.004,{'left':(1.004,1.004,False,0.,False),'right':(1.004,1.004,False,0.,False)}))
    assert x.position_is_considered(1.003)
    assert not x.position_is_considered(1.005)
    x.protocol.point_history.append((1.010,{'left':(1.010,1.,True,.5,True),'right':(1.010,1.,False,0.,False)}))
    assert not x.position_is_considered(1.010)

def test_disabled_and_no_points_preserve_stationary_only_policy():
    x=owner(False)
    assert not x.position_is_considered(1.001)
    x.soft_support_position_guard=True;x.points=None
    assert not x.position_is_considered(1.001)
    x.protocol.history.append((1.002,{'left':(1.002,1.002,0,1.),'right':(1.002,1.002,3,.5)},None))
    assert x.position_is_considered(1.003)
