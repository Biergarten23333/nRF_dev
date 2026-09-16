"""Shared objective for the batch smoke and chronological arm prototype.

Composite conditional objective: gyro axes and orientations are correlated.
The Jacobian rank is a local diagnostic, never statistical confidence.
The qualitative branch mapping is qualified only in the synthetic standing
frame. Neither a missing parent IMU nor a known chest pose is provided.
"""
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

STARTS_DEG = ((0,0,0,0),(20,-20,15,25),(-20,20,-15,-25),(0,0,180,0))


def nominal_mount(limb):
    if limb not in (0,1):
        raise ValueError('left/right arm index required')
    return np.array([[1,0,0],[0,0,1],[0,-1,0]]) if limb==0 else np.array([[-1,0,0],[0,0,1],[0,1,0]])


def wear_branch(mount, limb):
    """Fixture-only gross hemisphere; no real-wear validation claimed."""
    return bool(mount[1,2]>0 and (-1 if limb==0 else 1)*mount[2,1]>0)


def initial_parameters(limb, perturbation):
    result = np.array(perturbation, dtype=float, copy=True)
    result[:3] = Rotation.from_matrix(nominal_mount(limb)@Rotation.from_rotvec(result[:3]).as_matrix()).as_rotvec()
    return result


def residual_terms(parameters, factors):
    mount = Rotation.from_rotvec(parameters[:3]).as_matrix()
    yaw = Rotation.from_euler('z',parameters[3]).as_matrix()
    terms = {}
    for f in factors:
        if f['kind']=='sensor_axis':
            value = np.cross(mount[:,f['column']],np.asarray(f['axis']))/.15
        else:
            observed,target = np.asarray(f['observed']),np.asarray(f['target'])
            local = -mount[:,2] if f['axis'] is None else np.asarray(f['axis'])
            direction = yaw@observed@local
            mode = f.get('direction_mode','spatial')
            if mode=='azimuth':
                a,b = direction[:,:2],target[:,:2]
                an,bn = np.linalg.norm(a,axis=1),np.linalg.norm(b,axis=1)
                # As in the existing arm_protocol owner, an optimizer cannot
                # shrink its information weight by rotating toward vertical.
                # A singular candidate has undefined heading, not zero error.
                if np.any(an<1e-8) or np.any(bn<1e-8):
                    raise ValueError('undefined horizontal protocol direction')
                error=a/an[:,None]-b/bn[:,None]
            elif mode=='spatial':
                error = np.cross(direction,target) if f['axial'] else direction-target
            else:
                raise ValueError('unknown direction mode')
            value = error.ravel()/(np.sqrt(len(error))*np.deg2rad(25))
        if f['id'] in terms:
            raise ValueError('duplicate evidence factor')
        terms[f['id']] = value
    return terms


def solve_one(factors, parameters):
    def residual(p):
        return np.concatenate(list(residual_terms(p,factors).values()))
    result = least_squares(residual,parameters,max_nfev=100,ftol=1e-10,xtol=1e-10,gtol=1e-10)
    singular = np.linalg.svd(result.jac,compute_uv=False)
    azimuth_support={}
    for f in factors:
        if f.get('direction_mode')=='azimuth':
            mount=Rotation.from_rotvec(result.x[:3]).as_matrix()
            local=-mount[:,2] if f['axis'] is None else np.asarray(f['axis'])
            direction=np.asarray(f['observed'])@local
            support=np.linalg.norm(direction[:,:2],axis=1)
            azimuth_support[f['id']]=dict(minimum=float(support.min()),median=float(np.median(support)),
                interpretation='predicted horizontal support; small support weakens condition, not evidence of success')
    return dict(parameters=result.x.tolist(),mount=Rotation.from_rotvec(result.x[:3]).as_matrix().tolist(),
        heading=float(result.x[3]),success=bool(result.success),cost=float(result.cost),
        evaluations=int(result.nfev),phase_cost={k:float(v@v/2) for k,v in residual_terms(result.x,factors).items()},
        azimuth_support=azimuth_support,
        jacobian_singular_values=singular.tolist(),
        local_composite_rank=int(np.sum(singular>1e-6*max(1.,singular.max()))))


def update_arm(factors, limb, previous=()):
    """Replace a prefix fit; previous parameters are starts, never extra data."""
    if not factors:
        return dict(status='NO_EVIDENCE',candidates=[],selected=None,statistical_confidence_claimed=False)
    seeds = [initial_parameters(limb,np.deg2rad(s)) for s in STARTS_DEG]
    # Retain both branch orientations; canonical starts permit recovery of
    # previously poor candidates. Fixed finite search is not global uniqueness.
    seeds += [np.asarray(r['parameters']) for r in previous[:4]]
    candidates = [solve_one(factors,p) for p in seeds]
    for r in candidates:
        r['wear_branch_eligible'] = wear_branch(np.asarray(r['mount']),limb)
    eligible = [r for r in candidates if r['success'] and r['wear_branch_eligible']]
    selected = min(eligible,key=lambda r:r['cost']) if eligible else None
    columns = {f['column'] for f in factors if f['kind']=='sensor_axis'}
    phases = {f['phase'] for f in factors}
    shoulder = '04_shoulder_left:full' if limb==0 else '05_shoulder_right:full'
    complete = columns=={1,2} and {'02_t_pose:full',shoulder}.issubset(phases)
    conflict = selected is None or selected['cost']>=2.
    status = ('CONFLICT' if conflict else 'CONDITIONAL_SUPPORT'
              if complete and selected['local_composite_rank']==4 else 'WAITING_FOR_COMPLEMENTARY_MOTION')
    return dict(status=status,candidates=candidates,selected=selected,
        factor_ids=[f['id'] for f in factors],available_axis_columns=sorted(columns),
        max_evidence_time=max(f['max_evidence_time'] for f in factors),
        statistical_confidence_claimed=False,product_ready=False,
        search_scope='four canonical starts plus at most four previous candidates')
