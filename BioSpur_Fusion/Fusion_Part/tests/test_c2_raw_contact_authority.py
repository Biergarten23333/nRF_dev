import numpy as np
from run_c2_continuous_archive_ab import protected_raw_contact_sides


class Protocol:
    def __init__(self,valid):self.valid=valid
    def position_evidence(self,query):
        assert query<1.
        return np.array(self.valid),None,None


def test_contact_authority_excludes_future_new_and_expired_episodes():
    history=[(.9,{0:2,1:4}),(1.,{0:3,1:5}),(1.1,{0:3,1:5})]
    assert protected_raw_contact_sides(Protocol([True,True]),history,{0:2,1:4},1.)==(0,1)
    assert protected_raw_contact_sides(Protocol([True,True]),history,{0:3,1:4},1.)==(1,)
    assert protected_raw_contact_sides(Protocol([False,False]),history,{0:2,1:4},1.)==()
    assert protected_raw_contact_sides(Protocol([True,True]),[(1.,{0:2})],{0:2},1.)==()
    assert protected_raw_contact_sides(Protocol([True,True]),history,{},1.)==()
