"""Arrived-action owner for existing five-node functional frame primitives.

This state calibrates retained frames only. It records all actions but does
not claim their unimplemented pose/geometry factors have been used in fitting.
"""
import copy
import hashlib

import numpy as np

from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_sparse_nodes.calibration import matrices, robust_still, fit_forearm_frame
from biospur_fusion.c2_sparse_nodes.functional_frames import signed_functional_frame


class FramePrefixSession:
    def __init__(self):
        self._state=dict(ledger={},frames={},snapshots=[])
        self._initial=None
        self._tpose=None

    @property
    def snapshots(self):
        return copy.deepcopy(self._state['snapshots'])

    def ingest(self, action, rows):
        cursor=len(self._state['ledger'])
        if cursor>=len(EPISODES) or action!=EPISODES[cursor]:
            raise ValueError('exact next recorded C2 action required')
        if set(rows)!=set(NODES):
            raise ValueError('exactly five retained nodes required')
        hashes={}
        for node,a in rows.items():
            if a.ndim!=2 or a.shape[1]!=11 or len(a)<100 or not np.isfinite(a).all() or np.any(np.diff(a[:,0])<=0):
                raise ValueError('invalid physical IMU rows')
            matrices(a)  # Validate quaternion normalization before changing state.
            hashes[node]=hashlib.sha256(np.ascontiguousarray(a,dtype='<f8').tobytes()).hexdigest()
        earliest=min(float(a[0,0]) for a in rows.values())
        latest=max(float(a[-1,0]) for a in rows.values())
        if self._state['snapshots'] and earliest<=self._state['snapshots'][-1]['max_evidence_time']:
            raise ValueError('overlapping or backward action support')
        frames=copy.deepcopy(self._state['frames'])
        initial=self._initial
        tpose=self._tpose
        updated=[]
        if action=='00_initial_still':
            initial={n:robust_still(rows[n]) for n in NODES}
            for n in NODES:
                frames[n]=dict(status='INITIAL_REFERENCE_ONLY',mount=None,yaw=None,
                               initial_sensor_rotation=initial[n].tolist())
        elif action=='02_t_pose':
            tpose={n:matrices(rows[n]).as_matrix() for n in NODES[1:3]}
        elif action in ('06_elbow_left','07_elbow_right'):
            index=1 if action=='06_elbow_left' else 2
            node=NODES[index]
            mount,yaw,audit=fit_forearm_frame(rows[node],tpose[node],initial[node],left=index==1)
            # Preserve proper sign alternatives; conditional signs from the
            # old primitive are not a claim of unique observed anatomy.
            alternatives=[(mount@np.diag(sign)).tolist() for sign in
                          ((1,1,1),(-1,-1,1),(-1,1,-1),(1,-1,-1))]
            frames[node]=dict(status='CONDITIONAL_FUNCTIONAL_FRAME',mount=mount.tolist(),yaw=yaw,
                              alternatives=alternatives,audit=audit,source_action=action,
                              max_evidence_time=latest,prior_dependencies=['00_initial_still','02_t_pose'])
            updated.append(node)
        functional={'10_knee_left_seated':(3,-1.),'11_knee_right_seated':(4,-1.),
                    '14_trunk_flex_extend':(0,1.)}
        if action in functional:
            index,sign=functional[action];node=NODES[index]
            mount,yaw,audit=signed_functional_frame(rows[node],initial[node],sign)
            frames[node]=dict(status='CONDITIONAL_FUNCTIONAL_FRAME',mount=mount.tolist(),yaw=yaw,
                              audit=audit,source_action=action,max_evidence_time=latest,
                              prior_dependencies=['00_initial_still'])
            updated.append(node)
        ledger=copy.deepcopy(self._state['ledger'])
        ledger[action]=dict(source_sha256=hashes,earliest_time=earliest,max_evidence_time=latest,
                            frame_updates=updated,pose_factor_used=False)
        snapshot=dict(action=action,frames=frames,max_evidence_time=latest,
                      arrived_actions=list(ledger),updated_nodes=updated,
                      all_actions_arrived=cursor+1==len(EPISODES),
                      retained_frames_available=all(f['mount'] is not None for f in frames.values()),
                      full_pose_calibration_complete=False,calibration_accepted=False)
        # No externally visible partial state when a functional primitive fails.
        self._initial=initial;self._tpose=tpose
        self._state.update(ledger=ledger,frames=frames)
        self._state['snapshots'].append(copy.deepcopy(snapshot))
        return copy.deepcopy(snapshot)
