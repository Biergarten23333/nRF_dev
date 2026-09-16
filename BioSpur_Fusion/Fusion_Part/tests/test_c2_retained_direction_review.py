import numpy as np
from scipy.spatial.transform import Rotation
from build_c2_progressive_candidate_review import retained_direction_error
from c2_five_geometry_review import C2_TO_SMPL_BODY


def test_retained_directions_ignore_independent_world_gauges():
    axes=np.array([[1.,0,0],[-1.,0,0],[0,-1.,0],[0,-1.,0]])
    offsets=np.zeros((24,3));offsets[[20,21,7,8]]=axes
    geometry={'rest_offsets_m':offsets}
    observed=Rotation.from_rotvec(np.arange(15).reshape(5,3)*.07).as_matrix()[None]
    root=Rotation.from_rotvec([.6,-.9,.4]).as_matrix()
    ref={'pelvis':root[None]}
    names=['forearm_left','forearm_right','shank_left','shank_right']
    for i,n in enumerate(names):
        target=C2_TO_SMPL_BODY.T@observed[0,0].T@observed[0,i+1]@axes[i]
        ref[n]=(root@Rotation.align_vectors(target[None],np.array([[0.,0.,-1.]]))[0].as_matrix())[None]
    assert retained_direction_error(observed,geometry,ref).max()<2e-6
    gauge=Rotation.from_rotvec([.7,.2,-.8]).as_matrix()
    ref={k:gauge@v for k,v in ref.items()}
    assert retained_direction_error(gauge@observed,geometry,ref).max()<2e-6
    ref['forearm_right']=ref['forearm_left'].copy()
    assert retained_direction_error(observed,geometry,ref)[0,1]>10
