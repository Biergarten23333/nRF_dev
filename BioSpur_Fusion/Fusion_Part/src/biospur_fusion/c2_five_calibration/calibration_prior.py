"""C2-only raw factor registration and disjoint heading/arm-pose priors.

Information is registered once before role selection. Conditional movement
planes depend on inferred thorax pose and never become scalar heading truth.
"""
import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import NODES
from .frontend import FIT
from .phase_contract import direction_target,phase_bounds,phase_key


class RegisteredHeadingPrior:
    """One raw factor contribution; never also use its filtered posterior."""
    def __init__(self, factors, baseline_heading, *, baseline_calibration=None):
        if set(factors)!=set(NODES[1:]) or set(baseline_heading)!=set(NODES[1:]):
            raise ValueError('registered heading requires exactly four retained limbs')
        self.baseline=torch.tensor([baseline_heading[n] for n in NODES[1:]],dtype=torch.float64)
        self.rows=[]
        self.information={}; self.all_information={}; self.conditional_information={}
        self.factor_actions={}; self.factor_specs={}
        self.arm_protocol=None
        if not torch.isfinite(self.baseline).all():
            raise ValueError('nonfinite baseline heading')
        for i,node in enumerate(NODES[1:]):
            seen=set(); intervals={}
            self.information[node]={}; self.all_information[node]={}; self.conditional_information[node]={}
            self.factor_actions[node]={}; self.factor_specs[node]={}
            for factor in factors[node]:
                action=factor['action']; kind=factor['source_role']
                key=phase_key(action,factor.get('phase_id')); bounds=phase_bounds(factor)
                if action[:2] not in FIT or key in seen:
                    raise ValueError('raw heading phase must be registered once per limb')
                seen.add(key)
                if any(max(bounds[0],lo)<min(bounds[1],hi) for lo,hi in intervals.get(action,())):
                    raise ValueError('overlapping phases would duplicate raw heading information')
                intervals.setdefault(action,[]).append(bounds)
                _,multiple=direction_target(kind,i)
                z,q,sigma=[float(factor[k]) for k in ('measurement_delta_rad','quality','base_sigma_deg')]
                if not np.isfinite([z,q,sigma]).all() or sigma<25.:
                    raise ValueError('heading factors require finite broad uncertainty >=25 degrees')
                information=0. if q<=0 else max(np.clip(q,0.,1.),.05)/max(np.deg2rad(sigma)**2,np.deg2rad(1.)**2)
                self.all_information[node][key]=float(information)
                self.factor_actions[node][key]=action
                self.factor_specs[node][key]=(bounds,multiple,kind)
                if factor.get('used_for_frozen_heading',True):
                    if kind=='directed_forward':
                        raise ValueError('late directed arm phase cannot be promoted to scalar heading truth')
                    if baseline_calibration is not None and node in baseline_calibration.get('temporal_heading_curves',{}):
                        from biospur_fusion.c2_sparse_nodes.heading_transport import temporal_heading
                        at_measurement=float(temporal_heading([factor['measurement_time_s']],node,baseline_calibration)[0])
                        z-=at_measurement-float(self.baseline[i])
                    self.rows.append((i,z,multiple,information))
                    self.information[node][key]=float(information)
                else:
                    if i>1 or kind=='directed_side':
                        raise ValueError('conditional arm direction requires a forearm protocol factor')
                    self.conditional_information[node][key]=float(information)

    def energy(self, delta, *, residual_blocks=None):
        total=delta.sum()*0.
        for row,(i,z,multiple,information) in enumerate(self.rows):
            difference=multiple*(self.baseline[i]+delta[i]-z)
            residual=torch.atan2(torch.sin(difference),torch.cos(difference))/multiple
            from .residual_blocks import record_weighted
            record_weighted(residual_blocks, f'heading/{row}', residual, information)
            total=total+information*residual.square()
        return total

    def bind_arm_protocol(self, tape, actions):
        """Require every conditional factor once, with no heading-anchor row."""
        expected={(key,i):information for i,node in enumerate(NODES[1:])
                  for key,information in self.conditional_information[node].items()}
        if tape is None:
            if expected:raise ValueError('conditional arm factors require a calibration protocol tape')
            return
        pairs=[(row.factor_key,row.limb) for row in tape.rows]
        if len(set(pairs))!=len(pairs) or set(pairs)!=set(expected):
            raise ValueError('protocol tape must contain each conditional factor once and no heading anchors')
        if not torch.equal(tape.baseline,self.baseline):
            raise ValueError('protocol tape and scalar heading require the same baseline')
        for row in tape.rows:
            information=expected[(row.factor_key,row.limb)]
            node=NODES[row.limb+1]
            bounds,multiple,kind=self.factor_specs[node][row.factor_key]
            if (row.action!=self.factor_actions[node][row.factor_key] or row.multiple!=multiple
                    or row.audit.get('registered_phase_interval_s')!=list(bounds)
                    or row.audit.get('source_role')!=kind):
                raise ValueError('protocol tape changed registered phase semantics')
            if row.action not in actions:
                raise ValueError('protocol action is absent from the C2 tape')
            if (len(row.index) and (min(row.index)<0 or max(row.index)>=len(actions[row.action]['time_s']))):
                raise ValueError('protocol row is outside its original time grid')
            window=np.asarray(row.audit.get('formal_interval_s'),dtype=float)
            times=np.asarray(actions[row.action]['time_s'])[row.index]
            if (window.shape!=(2,) or not np.isfinite(window).all() or window[0]>=window[1]
                    or np.any(np.diff(row.index)<=0)
                    or np.any(times<window[0]) or np.any(times>window[1])):
                raise ValueError('protocol row is outside its registered phase support')
            if (not np.isfinite(row.information).all() or np.any(row.information<0)
                    or not np.isclose(row.audit['original_information'],information,atol=1e-12,rtol=0)
                    or row.information.sum()>information+1e-12):
                raise ValueError('protocol tape changed registered information')
        self.arm_protocol=tape

    def energy_for_action(self, action, delta, rotation, *, residual_blocks=None):
        if self.arm_protocol is None:return rotation.sum()*0.+delta.sum()*0.
        return self.arm_protocol.energy_for_action(action,delta,rotation,reference='thorax',residual_blocks=residual_blocks)
