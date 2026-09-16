import numpy as np
import pytest
import torch

from biospur_fusion.c2_five_calibration.anatomy import JointModel, PROXIMAL
from biospur_fusion.c2_five_calibration.personalization import fit_flexion_prior, personalize_prior
from biospur_fusion.c2_five_calibration.protocol_pose import build_bend_protocol
from test_c2_bend_protocol import inputs
from test_c2_joint_kinematics import geometry


def tape(angle):
    contracts, actions = inputs()
    g = geometry(); model = JointModel(g)
    for q in actions.values():
        prior = torch.eye(3, dtype=torch.float64).repeat(601, 24, 1, 1)
        p = torch.zeros(601, 9, dtype=torch.float64)
        p[:, 3:7] = angle
        q['prior'] = model.rotation(prior, torch.tensor(q['observed']), p).numpy()
    return actions, g, build_bend_protocol(contracts, actions)


def test_favorable_compressed_prior_calibration_generalizes_without_changing_observations():
    actions, g, protocol = tape(np.pi/3)
    fit = fit_flexion_prior(actions, g, protocol)
    gains = np.array(fit['flexion_gain'])
    assert np.all((gains > 1) & (gains < 1.5))
    # Favorable known multiplicative compression; not a real-data accuracy claim.
    unseen, _, _ = tape(np.pi/6)
    q = next(iter(unseen.values())); before = q['prior'].copy()
    obs = q['observed'].copy()
    after, _ = personalize_prior(before, obs, g, fit)
    angle = JointModel(g).initial(torch.tensor(after), torch.tensor(obs))[:, 3:7].numpy()
    assert np.all(np.abs(angle-np.pi/4) < abs(np.pi/6-np.pi/4))
    other = [i for i in range(24) if i not in PROXIMAL]
    np.testing.assert_array_equal(after[:, other], before[:, other])
    np.testing.assert_array_equal(obs, q['observed'])


def test_unity_gain_preserves_representable_pose():
    actions, g, _ = tape(.7)
    q = next(iter(actions.values()))
    after, _ = personalize_prior(q['prior'], q['observed'], g, {'flexion_gain':[1]*4})
    np.testing.assert_allclose(after, q['prior'], atol=1e-12)


def test_H_or_invalid_gain_is_rejected():
    actions, g, protocol = tape(.7)
    actions['H01_boxing'] = actions.pop('00_fixture')
    with pytest.raises(ValueError, match='all recorded'):
        fit_flexion_prior(actions, g, protocol)
    q = next(iter(actions.values()))
    with pytest.raises(ValueError, match='finite positive'):
        personalize_prior(q['prior'], q['observed'], g, {'flexion_gain':[1,1,1,-1]})
