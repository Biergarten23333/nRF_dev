"""Subject kinematics used in the observation equation, not a display rescale."""
import numpy as np
from scipy.spatial.transform import Rotation
import torch

from biospur_fusion.c2_imucoco.body import LIMBS
from biospur_fusion.c2_imucoco.backend import VERTICES

OBSERVED = [0, 18, 19, 4, 5]
FREE = [1, 2, 3, 6, 9, 13, 14, 16, 17]
DISPLAY = [0,16,18,20,17,19,21,1,4,7,2,5,8]
DISTAL = [None,20,21,7,8]


def subject_geometry(body, surface, *, sensor_vertices=None):
    raw_j, raw_v = body.get_zero_pose_joint_and_vertex()
    joints, vertices = raw_j.numpy(), raw_v.numpy()
    parent = body.parent
    offsets = joints.copy()
    for i in range(1,24):
        offsets[i] -= joints[parent[i]]
    lookup = {r['measurement_id']:r for r in surface['measurements']}
    evidence = []
    for name, a, b in LIMBS:
        if parent[b] != a:
            raise ValueError('measured segment is not an SMPL bone')
        readings = [r['value_mm']/1000 for r in lookup[name]['observations'] if 'value_mm' in r]
        length = float(np.mean(readings))
        before = float(np.linalg.norm(offsets[b]))
        offsets[b] *= length/before
        evidence.append(dict(measurement=name, target_m=length, previous_m=before,
                             mapping_uncertainty_m=.02, use='sensor acceleration equation and pose kinematics'))
    rests = [np.zeros(3)]
    for i in range(1,24):
        rests.append(rests[parent[i]]+offsets[i])
    rests = np.array(rests)
    lever, correction = [], []
    sensor_vertices = VERTICES if sensor_vertices is None else sensor_vertices
    if len(sensor_vertices)!=5:
        raise ValueError('subject geometry requires five sensor placements')
    for k, (bone, vertex) in enumerate(zip(OBSERVED, sensor_vertices)):
        value = vertices[vertex]-joints[bone]
        fix = np.eye(3)
        if k:
            tip = DISTAL[k]
            old = joints[tip]-joints[bone]
            axis = old/np.linalg.norm(old)
            value += axis*(value@axis)*(np.linalg.norm(offsets[tip])/np.linalg.norm(old)-1.)
            ideal = np.array([1.,0.,0.]) if k==1 else np.array([-1.,0.,0.]) if k==2 else np.array([0.,-1.,0.])
            fix = Rotation.align_vectors(ideal[None], axis[None])[0].as_matrix()
        lever.append(value)
        correction.append(fix)
    result=dict(parent=parent, rest_joints_m=rests.tolist(), rest_offsets_m=offsets.tolist(),sensor_vertices=list(sensor_vertices),
        nominal_sensor_levers_m=np.array(lever).tolist(), bone_frame_correction=np.array(correction).tolist(),
        measured_dimensions=evidence, geometry_status='MEASURED_SURFACE_PROXY_WITH_DECLARED_MAPPING_UNCERTAINTY',
        unmeasured_geometry='SMPL mean torso and joint-centre breadth; not claimed measured',
        sensor_offset_source='published surface vertices, subject limb scaling; must be fitted and validated')
    from .body_feasibility import make_body_spec
    result['body_feasibility']=make_body_spec(result,vertices,surface)
    return result


def joints_from_global(rotation, geometry):
    offsets = torch.as_tensor(geometry['rest_offsets_m'], dtype=rotation.dtype,device=rotation.device)
    positions = [torch.zeros_like(rotation[...,0,:,0])]
    for i in range(1,24):
        p = geometry['parent'][i]
        positions.append(positions[p]+(rotation[...,p,:,:]@offsets[i][...,None]).squeeze(-1))
    return torch.stack(positions,dim=-2)


def sensor_positions(rotation, geometry, levers):
    joints = joints_from_global(rotation,geometry)
    lever = torch.as_tensor(levers,dtype=rotation.dtype,device=rotation.device)
    p = joints[...,OBSERVED,:]+(rotation[...,OBSERVED,:,:]@lever[...,None]).squeeze(-1)
    return p-p[...,:1,:]


def constrained_rotation(prior, observed, delta):
    """The five observed global rotations are boundary data, not soft priors."""
    result = prior.clone()
    x, y, z = delta.unbind(-1)
    zero = torch.zeros_like(x)
    skew = torch.stack((zero,-z,y,z,zero,-x,-y,x,zero),dim=-1).reshape(*delta.shape[:-1],3,3)
    result[:,FREE] = torch.matrix_exp(skew) @ prior[:,FREE]
    result[:,OBSERVED] = observed
    return result
