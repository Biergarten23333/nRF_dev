import copy
import numpy as np
import torch
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_five_calibration.geometry import FREE, OBSERVED, constrained_rotation, joints_from_global, sensor_positions
from biospur_fusion.c2_five_calibration.solver import filtered, lever_system, fit_levers


def geometry():
    parent = [None,0,0,0,1,2,3,4,5,6,7,8,9,9,9,12,13,14,16,17,18,19,20,21]
    offsets = np.random.default_rng(6).normal(size=(24,3))*.2
    offsets[0]=0
    return dict(parent=parent,rest_offsets_m=offsets.tolist())


def test_observed_rotations_lengths_and_finite_gradient():
    prior=torch.eye(3,dtype=torch.float64).repeat(30,24,1,1)
    observed=torch.from_numpy(Rotation.random(150,random_state=7).as_matrix().reshape(30,5,3,3))
    delta=torch.zeros(30,len(FREE),3,dtype=torch.float64,requires_grad=True)
    r=constrained_rotation(prior,observed,delta)
    assert torch.equal(r[:,OBSERVED],observed)
    g=geometry(); j=joints_from_global(r,g)
    for i in range(1,24):
        assert torch.allclose((j[:,i]-j[:,g['parent'][i]]).norm(dim=-1),torch.tensor(np.linalg.norm(g['rest_offsets_m'][i])))
    sensor_positions(r,g,np.ones((5,3))*.03).square().sum().backward()
    assert torch.isfinite(delta.grad).all()
    assert delta.grad.abs().sum()>0


def test_dimension_enters_physical_equation():
    t=np.arange(100)/20
    r=torch.from_numpy(Rotation.from_euler('z',np.sin(t)[:,None]).as_matrix()).unsqueeze(1).repeat(1,24,1,1)
    g=geometry(); changed=copy.deepcopy(g)
    changed['rest_offsets_m'][18]=(np.array(g['rest_offsets_m'][18])*1.1).tolist()
    assert not torch.allclose(filtered(sensor_positions(r,g,np.zeros((5,3))),2),filtered(sensor_positions(r,changed,np.zeros((5,3))),2))


def test_synthetic_offset_recovery():
    t=np.arange(400)/20
    rotvec=np.stack([np.sin(t[:,None]*(np.arange(24)[None]+1)*.13+p)*.5 for p in (0,1,2)],axis=-1)
    r=Rotation.from_rotvec(rotvec.reshape(-1,3)).as_matrix().reshape(-1,24,3,3)
    g=geometry(); actual=np.random.default_rng(9).normal(size=(5,3))*.02
    # Construct target with the identical *linear derivative operator*, avoiding
    # a synthetic finite-difference versus smoothing mismatch.
    zeros=np.zeros((len(t),5,3)); valid=np.ones(len(t),bool)
    design,baseline=lever_system(r,zeros,valid,g)
    target=design@actual.ravel()
    fitted,audit=fit_levers([(design,target)],np.zeros((5,3)),radius=.1,regularization_sigma=1e6)
    assert audit['design_rank']==15
    assert np.max(abs(fitted-actual))<.002



def test_calibrated_initial_state_is_supplied_to_upstream(monkeypatch):
    from biospur_fusion.c2_imucoco import backend
    class Encoder:
        def __init__(self,**kw): self.model=type('M',(),{'mesh_positions':torch.zeros(6890,3)})()
        def set_placements(self,p): return torch.arange(24)
    class Poser:
        def init_hidden_states(self,p,r): self.received=r; return r
    monkeypatch.setattr(backend,'ChunkedFeatures',Encoder)
    initial=Rotation.random(24,random_state=8).as_matrix()
    poser=Poser()
    backend.ChunkedPoseStream(poser,initial_global_rotation=initial)
    expected=torch.tensor(initial,dtype=torch.float32)[None,...,:2].transpose(-1,-2).flatten(-2)
    assert torch.equal(poser.received,expected)
    assert not torch.equal(poser.received,torch.eye(3).expand(1,24,3,3)[...,:2].transpose(-1,-2).flatten(-2))


def test_frontend_rejects_holdout_and_removed_nodes():
    import pytest
    from biospur_fusion.c2_five_calibration.frontend import fit_frontend
    with pytest.raises(ValueError,match='H-series'):
        fit_frontend({'H01_boxing':{}},{})
    with pytest.raises(ValueError,match='exactly the five'):
        fit_frontend({'00_initial_still':{'removed':{}}},{})


def test_every_recorded_c2_action_enters_calibration(monkeypatch):
    from biospur_fusion.c2_five_calibration import frontend
    from biospur_fusion.c2_five_calibration import heading
    from biospur_fusion.c2_sparse_nodes.inputs import NODES
    expected = {f'{i:02d}' for i in range(20) if i != 1}
    assert frontend.FIT == expected
    rows = np.zeros((100,11))
    rows[:,0] = np.arange(100)*.005
    rows[:,1] = 1.
    rows[:,7] = 9.80665
    rows[:,8] = np.linspace(0., .001, 100)
    episodes = {i+'_action':{n:{'imu':rows} for n in NODES} for i in expected}
    episodes['00_initial_still'] = episodes.pop('00_action')
    seen = []

    def calibrate(all_actions, surface, *, heading_closure):
        assert not heading_closure
        seen.extend(all_actions)
        return {}

    monkeypatch.setattr(frontend, 'calibrate', calibrate)
    monkeypatch.setattr(heading, 'fit_registered_heading', lambda actions, c:
        {'factors':{}, 'frozen_correction_rad':{n:0. for n in NODES[1:]}})
    result = frontend.fit_frontend(episodes, {})
    assert {n[:2] for n in seen} == expected
    assert {n[:2] for n in result['fit_actions']} == expected
    assert result['validation_actions'] == []
    assert result['calibration_protocol'] == 'ALL_RECORDED_C2'


def test_physical_calibration_rejects_obsolete_internal_holdout_split(tmp_path, monkeypatch):
    import json
    import pytest
    from biospur_fusion.c2_five_calibration import workflow
    (tmp_path/'GEOMETRY.json').write_text('{}')
    (tmp_path/'TASK_CONTRACT.json').write_text(json.dumps({
        'fit_actions':['00','02'], 'validation_actions':['18','19']}))
    monkeypatch.setattr(workflow, 'action_data', lambda out: {})
    with pytest.raises(ValueError, match='all recorded C2'):
        workflow.physical(tmp_path)


def test_capture_placement_reaches_network_encoder(monkeypatch):
    from biospur_fusion.c2_imucoco import backend
    from biospur_fusion.c2_five_calibration.placement import C2_VERTICES
    coordinates = torch.arange(6890 * 3, dtype=torch.float32).reshape(6890, 3)

    class Encoder:
        def __init__(self, **kwargs):
            self.model = type('Model', (), {'mesh_positions': coordinates})()

        def set_placements(self, positions):
            self.received = positions.clone()
            return torch.zeros(24, dtype=torch.long)

    class Poser:
        def init_hidden_states(self, positions, rotations):
            return None

    monkeypatch.setattr(backend, 'ChunkedFeatures', Encoder)
    stream = backend.ChunkedPoseStream(Poser(), sensor_vertices=C2_VERTICES)
    torch.testing.assert_close(stream.encoder.received, coordinates[C2_VERTICES])
    assert not torch.equal(stream.encoder.received, coordinates[backend.VERTICES])
