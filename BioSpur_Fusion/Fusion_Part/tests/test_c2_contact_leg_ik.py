import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_uwb_calibration.contact_leg_ik import solve_contact_leg_ik
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS, corrected_proxy_points
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import project_hinge_corrections
from biospur_fusion.c2_articulated_biomechanics.model import HingeJoint
from test_c2_articulated_range import _geometry


def test_per_target_scale_scalar_equivalence_and_validation():
    b,z,p,project=setup()
    targets={n:p[n]+[0.,.008,0.] for n in ('ankle_left','ankle_right')}
    kwargs=dict(base_rotations_world=b,geometry=_geometry(),root_position_m=np.zeros(3),
        targets_world_m=targets,hinge_projector=project)
    scalar=solve_contact_leg_ik(**kwargs)
    mapped=solve_contact_leg_ik(**kwargs,point_sigma_m={n:.03 for n in targets})
    assert scalar.accepted==mapped.accepted
    for s in SEGMENTS:np.testing.assert_array_equal(scalar.corrections[s],mapped.corrections[s])
    assert scalar.final_cost==mapped.final_cost
    for invalid in ({'ankle_left':.03},{'ankle_left':0.,'ankle_right':.03},
                    {'ankle_left':np.nan,'ankle_right':.03},
                    {'ankle_left':.03,'ankle_right':np.inf}):
        with pytest.raises(ValueError):solve_contact_leg_ik(**kwargs,point_sigma_m=invalid)
    mixed=solve_contact_leg_ik(**kwargs,point_sigma_m={'ankle_left':.02,'ankle_right':.10})
    assert mixed.accepted
    left=np.linalg.norm(mixed.points['ankle_left']-targets['ankle_left'])
    right=np.linalg.norm(mixed.points['ankle_right']-targets['ankle_right'])
    assert left<right


def test_specialized_endpoint_jacobian_parity_and_signed_fd():
    from biospur_fusion.c2_uwb_calibration.contact_leg_ik import _leg_endpoints
    from biospur_fusion.c2_uwb_calibration.articulated_range import _corrected_proxy_point_jacobians
    b,z,_,_=setup(); rng=np.random.default_rng(3)
    b={s:Rotation.from_rotvec(rng.normal(size=3)*.2).as_matrix() for s in SEGMENTS}
    active=tuple(s for s in SEGMENTS if s.startswith(('thigh','shank')))
    c={s:(rng.normal(size=3)*.03 if s in active else np.zeros(3)) for s in SEGMENTS}
    names=('ankle_left','ankle_right'); g=np.diag([-1.,1.,1.])
    p,j=_leg_endpoints(b,c,_geometry(),active,names,g)
    full=corrected_proxy_points(b,c,_geometry())
    fullj=_corrected_proxy_point_jacobians(b,c,_geometry(),active)
    for n in names:
        np.testing.assert_allclose(p[n],g@full[n],atol=1e-14)
        np.testing.assert_allclose(j[n],g@fullj[n],atol=1e-14)
    for k,s in enumerate(active):
        for a in range(3):
            cp={s:v.copy() for s,v in c.items()}; cm={s:v.copy() for s,v in c.items()}
            cp[s][a]+=1e-6; cm[s][a]-=1e-6
            pp,_=_leg_endpoints(b,cp,_geometry(),active,names,g)
            pm,_=_leg_endpoints(b,cm,_geometry(),active,names,g)
            for n in names:np.testing.assert_allclose((pp[n]-pm[n])/2e-6,j[n][:,3*k+a],atol=1e-8)


def setup():
    base={s:np.eye(3) for s in SEGMENTS}; zero={s:np.zeros(3) for s in SEGMENTS}
    model={f'knee_{side}':HingeJoint(f'knee_{side}',f'thigh_{side}',f'shank_{side}',
        'fixture',(1.,0.,0.),(1.,0.,0.),(0.,0.,0.,1.),1.,0.,150.,1,1) for side in ('left','right')}
    projector=lambda b,c:project_hinge_corrections(b,c,model)
    p=corrected_proxy_points(base,zero,_geometry())
    return base,zero,p,projector


def test_contact_fit_bones_rom_fixed_root_and_native_reference():
    b,z,p,project=setup(); root=np.array([1.,2.,3.]); old=root.copy()
    target={'ankle_left':root+p['ankle_left']+[0.,.008,0.]}
    kwargs=dict(base_rotations_world=b,geometry=_geometry(),root_position_m=root,
        targets_world_m=target,hinge_projector=project)
    r=solve_contact_leg_ik(**kwargs)
    assert r.accepted and r.final_cost<r.initial_cost
    assert r.projection['post_projection_all_inside_rom']
    np.testing.assert_array_equal(root,old)
    for side in ('left','right'):
        np.testing.assert_allclose(np.linalg.norm(r.points['ankle_'+side]-r.points['knee_'+side]),.4)
    for _ in range(8):
        r=solve_contact_leg_ik(**kwargs,previous_correction=r.corrections)
        assert max(np.linalg.norm(v) for v in r.corrections.values())<np.sqrt(3)*.18
    np.testing.assert_array_equal(b['thigh_left'],np.eye(3))


def test_no_contact_and_unreachable_straight_knee_are_finite_not_clipped():
    b,z,p,project=setup()
    r=solve_contact_leg_ik(base_rotations_world=b,geometry=_geometry(),root_position_m=np.zeros(3),
        targets_world_m={},hinge_projector=project,previous_correction={s:np.ones(3)*.1 for s in SEGMENTS})
    assert r.reason=='NO_CONTACT'; np.testing.assert_array_equal(r.corrections['thigh_left'],z['thigh_left'])
    target=p['ankle_left']+[0.,0.,-10.]
    r=solve_contact_leg_ik(base_rotations_world=b,geometry=_geometry(),root_position_m=np.zeros(3),
        targets_world_m={'ankle_left':target},hinge_projector=project)
    assert np.isfinite(r.final_cost) and np.linalg.norm(r.points['ankle_left']-target)>9.


def test_post_projection_bad_bound_and_objective_rejected():
    b,z,p,_=setup()
    for amount in (.3, .19, .02):
        def bad(base,c):
            c={s:v.copy() for s,v in c.items()}; c['thigh_left']=np.array([amount,0.,0.])
            return c,{'post_projection_all_inside_rom':True}
        r=solve_contact_leg_ik(base_rotations_world=b,geometry=_geometry(),root_position_m=np.zeros(3),
            targets_world_m={'ankle_left':p['ankle_left']},hinge_projector=bad)
        assert not r.accepted
        np.testing.assert_array_equal(r.corrections['thigh_left'],z['thigh_left'])


def test_improper_embedding_signed_contact_direction():
    b,z,p,project=setup(); g=np.diag([-1.,1.,1.])
    r=solve_contact_leg_ik(base_rotations_world=b,geometry=_geometry(),root_position_m=np.zeros(3),
        targets_world_m={'ankle_left':g@p['ankle_left']+[.008,0.,0.]},hinge_projector=project,embedding=g)
    assert r.accepted and r.final_cost<r.initial_cost
    assert r.points['ankle_left'][0]>(g@p['ankle_left'])[0]
    expected=corrected_proxy_points(b,r.corrections,_geometry())
    np.testing.assert_allclose(r.points['ankle_left'],g@expected['ankle_left'])
