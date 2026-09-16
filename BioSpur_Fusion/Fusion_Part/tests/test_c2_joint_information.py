import numpy as np
from scipy.sparse import csr_matrix
from biospur_fusion.c2_five_calibration.joint_information import assemble_joint_rows
from biospur_fusion.c2_five_calibration.marginalization import marginalize_linear_system


def test_all_owner_rows_stream_once_and_keep_calibration_cross_terms():
    rng=np.random.default_rng(32);frames=40;width=2;n=frames*width
    pose=dict(pose=csr_matrix(np.eye(n)),heading=csr_matrix(rng.normal(size=(n,1))),
              levers=csr_matrix(rng.normal(size=(n,15))),residual=rng.normal(size=n),
              start=np.repeat(np.arange(frames),width),stop=np.repeat(np.arange(frames)+1,width))
    A=np.zeros((2,n));A[0,20]=1;A[1,60]=1
    actions=dict(pose=csr_matrix(A),heading=csr_matrix(np.ones((2,1))),residual=np.ones(2),start=np.array([10,30]))
    joint=assemble_joint_rows(pose,actions,np.eye(16)*.1,np.zeros(16))
    state=joint.stream(np.zeros(n+16),pose_width=width,chunk_frames=20)
    direct=marginalize_linear_system(joint.matrix.toarray(),joint.residual,np.array(state.columns))
    for d in rng.normal(size=(3,len(state.columns))):
        np.testing.assert_allclose(state.factor.energy(d),direct.energy(d),atol=1e-9)
    assert set(range(n,n+16))<=set(state.columns)
