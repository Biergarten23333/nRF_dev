"""Five-node calibration uses every recorded C2 action; H never enters."""
import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_sparse_nodes.calibration import calibrate, matrices
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_imucoco.preprocessing import prepare_stream, encode
from .geometry import OBSERVED

FIT = {f'{i:02d}' for i in range(20) if i != 1}  # 01 was not recorded.


def fit_frontend(episodes, surface, *, prefix=False):
    if any(name.startswith('H') for name in episodes):
        raise ValueError('H-series data cannot enter five-node calibration')
    if any(set(episode) != set(NODES) for episode in episodes.values()):
        raise ValueError('calibration accepts exactly the five retained nodes')
    fit = {k:v for k,v in episodes.items() if k[:2] in FIT}
    from .phase_contract import recorded_prefix
    ordered=(recorded_prefix([k for k in episodes if k!='_continuous']) if prefix else sorted(fit))
    if not prefix and {k[:2] for k in fit}!=FIT:
        raise ValueError('required fit actions missing')
    if prefix and '14_trunk_flex_extend' not in fit:
        raise ValueError('retained functional frames incomplete before recorded action 14')
    c = calibrate(fit, surface, heading_closure=False)
    from .heading import fit_registered_heading
    heading = fit_registered_heading(fit, c, prefix=True) if prefix else fit_registered_heading(fit,c)
    c['heading_factors'] = heading['factors']
    c['frozen_heading_correction_rad'] = heading['frozen_correction_rad']
    c['heading_replay_mode'] = ('CURRENT_PREFIX_RETROSPECTIVE_REFERENCE' if prefix
                                else 'FROZEN_AFTER_ALL_RECORDED_C2')
    c['mature_c2_heading_reuse'] = heading
    c['acc_bias_sensor'] = []
    for node in NODES:
        rows = fit['00_initial_still'][node]['imu']
        quiet = np.linalg.norm(rows[:,8:11],axis=1) < np.quantile(np.linalg.norm(rows[:,8:11],axis=1),.6)
        expected = np.einsum('nji,j->ni',matrices(rows).as_matrix(),[0.,0.,9.80665])
        c['acc_bias_sensor'].append(np.median((rows[:,5:8]-expected)[quiet],axis=0).tolist())
    for key in ('lengths','hinge_axes','hinge_audit','torso_display_models_m','hip_display_half_width_models_m'):
        c.pop(key,None)
    c.update(calibration_accepted=False, calibration_status='FUNCTIONAL_FRONTEND_CANDIDATE',
        assumptions=['functional motion axes and signed instructions define mounting',
            'standing pelvis/shanks approximately vertical; upper arms hanging only an initial prior',
            'no final-to-initial heading closure; six-axis heading remains uncertain'],
        geometry_used_in_orientation_fit=False,
        bias_status='single-pose apparent bias; inseparable from orientation and gravity error',
        fit_actions=sorted(fit),validation_actions=[],
        calibration_protocol='ARRIVED_C2_PREFIX' if prefix else 'ALL_RECORDED_C2',
        prefix_last_action=ordered[-1],
        complete_recorded_C2=len(ordered)==len(FIT),
        C2_replay_role=('retrospective arrived-prefix check; H forbidden before final freeze' if prefix
                        else 'in-sample calibration check; H01/H02 use frozen parameters'))
    from .mount_information import mounting_axis_audit
    c['mount_axis_information']=mounting_axis_audit(fit,c,prefix=prefix)
    return c


def prepare(episode, calibration, geometry, *, include_bias_transport=False):
    data = prepare_stream(episode, calibration, include_bias_transport=include_bias_transport)
    data['orientation'] = data['orientation'] @ np.asarray(geometry['bone_frame_correction'])
    data['features'] = encode(data['orientation'], data['acceleration_mps2'])
    return data


def initial_state(standing, geometry):
    observed = np.stack([Rotation.from_matrix(standing['orientation'][:,i]).mean().as_matrix() for i in range(5)])
    root = observed[0]
    initial = np.repeat(root[None],24,axis=0)
    rests = np.asarray(geometry['rest_offsets_m'])
    # Natural hanging upper arms is an explicit weak starting assumption,
    # not a measured proximal orientation or a forced straight elbow.
    for joint, tip in ((16,18),(17,19)):
        axis = rests[tip]/np.linalg.norm(rests[tip])
        initial[joint] = root @ Rotation.align_vectors(np.array([[0.,-1.,0.]]),axis[None])[0].as_matrix()
    initial[OBSERVED] = observed
    return initial


def refined_initial_state(checkpoint, first_time_s):
    """Use a five-only C2 latent body state as a recurrent initial hypothesis.

    The caller owns full-C2 provenance. This is not an extra observation and
    cannot replace rerunning the continuous network after changing its state.
    Timestamp identity prevents a late calibration pose being used at t=0.
    """
    time=np.asarray(checkpoint['time_s'])
    rotation=np.asarray(checkpoint['rotation'])
    valid=np.asarray(checkpoint['valid'])
    if (time.ndim!=1 or not len(time) or not np.isfinite(time).all()
            or np.any(np.diff(time)<=0) or valid.dtype!=bool or valid.shape!=time.shape
            or not valid[0] or not np.isfinite(first_time_s)
            or abs(time[0]-first_time_s)>1e-6
            or rotation.shape!=(len(time),24,3,3)):
        raise ValueError('valid C2 checkpoint on the exact first input timestamp required')
    initial=rotation[0].copy()
    if (not np.isfinite(initial).all()
            or not np.allclose(initial.transpose(0,2,1)@initial,np.eye(3),atol=1e-6,rtol=0)
            or not np.allclose(np.linalg.det(initial),1.,atol=1e-6,rtol=0)):
        raise ValueError('proper SMPL global rotations required in initial hypothesis')
    return initial
