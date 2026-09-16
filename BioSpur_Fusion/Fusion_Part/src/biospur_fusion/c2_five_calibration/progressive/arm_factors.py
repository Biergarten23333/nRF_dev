"""Construct factors from one arrived phase only; no future episode lookup."""
import hashlib
import numpy as np
from biospur_fusion.c2_sparse_nodes.calibration import _gyro_axis, matrices
from biospur_fusion.c2_sparse_nodes.inputs import NODES

PHASES = (
    ('00_initial_still','full',30.),('02_t_pose','full',30.),('03_pelvis_hula_circle','full',30.),
    ('04_shoulder_left','full',30.),('05_shoulder_right','full',30.),
    ('06_elbow_left','flexion',15.),('06_elbow_left','pronation',15.),
    ('07_elbow_right','flexion',15.),('07_elbow_right','pronation',15.))
PHASE_IDS = tuple(a+':'+p for a,p,_ in PHASES)


def validate_rows(rows):
    if set(rows)!=set(NODES):
        raise ValueError('exactly five retained nodes required')
    root=np.asarray(rows[NODES[0]])
    if root.ndim!=2 or root.shape[1]!=11:
        raise ValueError('eleven-column orientation/gyro fixture required')
    time = root[:,0]
    if not len(time) or np.any(np.diff(time)<=0):
        raise ValueError('strictly increasing sample time required')
    for node in NODES:
        value = np.asarray(rows[node])
        if value.shape!=(len(time),11) or not np.isfinite(value).all():
            raise ValueError('finite eleven-column orientation/gyro fixture required')
        if not np.array_equal(value[:,0],time):
            raise ValueError('five-node sample times differ')
        if not np.allclose(np.linalg.norm(value[:,1:5],axis=1),1.,atol=1e-6,rtol=0):
            raise ValueError('normalized fixture quaternion required')
    return time


def rows_digest(rows):
    h = hashlib.sha256()
    for node in NODES:
        a = np.ascontiguousarray(rows[node],dtype='<f8')
        h.update(node.encode());h.update(str(a.shape).encode());h.update(a.tobytes())
    return h.hexdigest()


def build_phase(phase_id, start, stop, rows, *, policy='synthetic_ideal_pose'):
    if policy not in ('synthetic_ideal_pose', 'actual_c2_conditional'):
        raise ValueError('unknown arm phase policy')
    time = validate_rows(rows)
    if time[0]<start or time[-1]>=stop:
        raise ValueError('samples outside declared phase')
    selected = np.flatnonzero((time>=start+.1)&(time<stop-.1))[::40]
    if len(selected)<20:
        raise ValueError('insufficient phase coverage')
    source = rows_digest(rows)
    factors=[]; diagnostics=[]
    action,phase = phase_id.split(':')
    if action=='00_initial_still' and policy=='actual_c2_conditional':
        # Natural standing supplies no measured distal direction or elbow angle.
        # The phase remains in the input ledger; navigation/neutral frame use it.
        return [], [dict(phase=phase_id,status='NO_EXACT_INITIAL_ARM_DIRECTION',
                         source_sha256=source,max_evidence_time=float(time[-1]))]
    for limb,node in enumerate(NODES[1:3]):
        elbow = '06_elbow_left' if limb==0 else '07_elbow_right'
        shoulder = '04_shoulder_left' if limb==0 else '05_shoulder_right'
        spec = None
        if action=='00_initial_still':spec=('long',[0,0,-1],False)
        elif action=='02_t_pose':spec=('long',[0,1 if limb==0 else -1,0],False)
        elif action==shoulder:spec=('gyro',[1,0,0],True)
        elif action==elbow:spec=('gyro',[0,1,0],True) if phase=='flexion' else ('long',[1,0,0],False)
        if spec is None:continue
        axis = None
        if action in (elbow,shoulder):
            try:
                axis,audit = _gyro_axis(rows[node],0.,stop-start)
                diagnostics.append(dict(node=node,phase=phase_id,axis=audit))
            except ValueError as exc:
                if 'insufficient' not in str(exc):raise
                diagnostics.append(dict(node=node,phase=phase_id,status='NO_AXIS_EXCITATION'))
                if spec[0]=='gyro':continue
        base = dict(limb=limb,phase=phase_id,node=node,source_sha256=source,
                    max_evidence_time=float(time[-1]),dependency_interval=[float(time[0]),float(time[-1])],
                    related_evidence_group=phase_id+':'+node,confidence_kind='COMPOSITE_NOT_INDEPENDENT')
        if action==elbow and axis is not None:
            factors.append(dict(base,id=phase_id+':'+node+':axis',kind='sensor_axis',
                                column=1 if phase=='flexion' else 2,axis=axis.tolist()))
        kind,target,axial=spec
        factors.append(dict(base,id=phase_id+':'+node+':direction',kind='direction',
            observed=matrices(rows[node][selected]).as_matrix().tolist(),
            target=(matrices(rows[NODES[0]][selected]).as_matrix()@np.asarray(target)).tolist(),
            axis=axis.tolist() if kind=='gyro' else None,axial=axial))
        if policy=='actual_c2_conditional':
            factors[-1]['protocol_role']='CONDITIONAL_BODY_DIRECTION_NOT_MEASURED_POSE'
            # T-pose can sag. Pronation specifies a bent elbow, not a horizontal
            # forearm. Keep their approximate horizontal direction condition
            # separate from elevation; neither condition measures upper-arm pose.
            if kind=='long':
                factors[-1]['direction_mode']='azimuth'
    return factors,diagnostics
