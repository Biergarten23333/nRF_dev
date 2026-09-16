"""Actual-protocol weak supervision, independent of classifier predictions.

Contact context is exhaustive; stationary-ankle labels are a separate raw
motion/phase annotation. Runtime receives features only, never action IDs.
"""
import hashlib
import json
import numpy as np
from .protocol_contact import ProtocolContactModel,causal_features

CLASSES=('STATIONARY_STANDING','AIRBORNE','FOREFOOT_ROLLING',
         'SUPPORTED_MOVING','STATIONARY_SEATED','SEATED_MOVING')


def formal_action_indices(times,actions,origin_s):
    """Half-open accepted windows only; preparation and gaps stay unknown."""
    ids=np.full(len(times),-1,int)
    for action in actions:
        mask=(times>=action['start_s']-origin_s)&(times<action['end_s']-origin_s)
        if np.any(ids[mask]>=0):raise ValueError('overlapping formal action windows')
        ids[mask]=int(action['id'].split('_')[0])
    return ids


class SupervisedContactModel(ProtocolContactModel):
    stationary_classes=(0,4)
    seated_classes=(4,5)
    moving_support_evidence=False

    def phase_lift_evidence(self, side, index, context):
        """Relative ankle height cannot establish passive seated-leg motion.

        The opposite active leg changes that reference. This exception describes
        stationary-segment eligibility, not sole contact; freshness and actual
        motion checks still apply. Active legs and non-formal inference retain
        the original height veto.
        """
        return bool(context != 5 and self.data[side][0][index,4] >= .075)

    def calibration_phase(self, side, index):
        """Retrospective formal-window prior, separate from online classification.

        Acceleration/lift vetoes are engineering movement evidence, not measured
        footsteps. Rotation alone does not invalidate soft standing support.
        Returns context, effective class, confidence, reason; context -1 means
        no protocol override, including preparation and inter-action gaps.
        """
        context=int(self.supervision[side][1][index])
        if context < 0:return -1,-1,0.,'NO_FORMAL_PRIOR'
        f,valid,_=self.data[side];x=f[index];profile=self.report[side]
        if not valid[index]:return context,-1,0.,'INVALID_FEATURE'
        gl=np.log1p(profile['quiet_gyro_rms_limit']/.01)
        al=np.log1p(profile['quiet_acc_std_limit']/.01)
        dl=np.log1p(3*profile['quiet_acc_std_limit']/.01)
        quiet=bool(x[0]<=gl and x[1]<=gl and x[2]<=al and x[3]<=dl)
        lifted=self.phase_lift_evidence(side,index,context)
        moving=bool(x[2]>al or x[3]>dl)
        if lifted:return context,1,1.,'LIFT_EVIDENCE'
        if context in (4,5):
            return (context,4,1.,'SEATED_STATIONARY_PROXY') if quiet else (context,5,1.,'SEATED_MOVING')
        if context==3:
            return (context,0,1.,'FOREFOOT_QUIET_PROXY') if quiet else (context,2,1.,'FOREFOOT_ROLLING')
        if moving:
            # Stillness thresholds cannot establish loss of contact. Reuse
            # independently fitted feature-group evidence, never a UWB residual.
            # Active lift/return contexts remain conservative; this score is
            # weak model evidence, not a calibrated contact probability.
            if self.moving_support_evidence and context==1:
                supported,score=self.support_context(side,index)
                if supported:
                    return context,3,score,'SUPPORTED_MOVING_MODEL_EVIDENCE'
            return context,-1,0.,'ADJUSTMENT_ACCELERATION_PROXY'
        return (context,0,1.,'STATIONARY_PROXY') if quiet else (context,3,1.,'SUPPORTED_ARTICULATION')

    def support_context(self,side,index):
        """Support-group evidence; ambiguity within {quiet,moving} is retained.

        This is NOT stationary ankle evidence and must not feed a zero-velocity
        observation or a hard position-consider gate by itself.
        """
        f,valid,_=self.data[side]
        if not valid[index]:return False,0.
        centers,scale,radii=self.parameters[side]
        distance=np.linalg.norm((f[index]-centers)/scale,axis=1)
        best=int(np.argmin(distance))
        if best not in (0,3) or distance[best]>radii[best]:return False,0.
        margin=float(min(distance[[1,2,4,5]])-min(distance[[0,3]]))
        return margin>=.15,min(1.,max(0.,margin))

    @classmethod
    def fit(cls,samples,times,offsets,regions_path,origin_s):
        obj=cls();regions=json.loads(regions_path.read_text())
        actions=[r for r in regions if r['kind']=='ACTION']
        ids=[int(r['id'].split('_')[0]) for r in actions]
        if len(ids)!=19 or set(ids)!=set(range(20))-{1}:
            raise ValueError('requires all nineteen actual calibration actions')
        obj.data={};obj.parameters={};obj.report={};obj.supervision={};obj.action_ids={}
        for k,side in enumerate(('left','right')):
            t,acc,gyro=samples[side]
            obj.action_ids[side]=formal_action_indices(t,actions,origin_s)
            masks={r['id']:(t>=r['start_s']-origin_s)&(t<r['end_s']-origin_s) for r in actions}
            still=np.logical_or.reduce([m for name,m in masks.items() if name.startswith(('00_','02_','17_'))])
            reference=float(np.median(np.linalg.norm(acc[still],axis=1)))
            f,valid,pose_epoch,rms,std=causal_features(t,acc,gyro,times,offsets,k,reference)
            def upper(x):
                z=x[still&valid];med=np.median(z)
                return float(max(np.quantile(z,.95),med+6*1.4826*np.median(abs(z-med)),1e-4))
            glimit=upper(rms);alimit=upper(std)
            # This phase annotation reads raw motion, not any trained prediction.
            quiet=(rms<=glimit)&(np.linalg.norm(gyro,axis=1)<=glimit)&(std<=alimit)&valid
            depth=float(np.quantile(f[still&valid,5],.05))
            lifted=(f[:,4]>=.075)|(f[:,5]<depth-.075)
            labels=np.full(len(t),-1,int);context=np.full(len(t),-1,int)
            for name,mask in masks.items():
                number=int(name[:2]);selected=mask&valid
                active=('left' in name)==(side=='left')
                if number in (10,11):
                    context[mask]=4 if active else 5 # seated active/passive, neither sole-contact truth
                    labels[selected]=5
                    # A passive leg may be stationary even when sitting changes
                    # its height/depth. Active raised leg remains motion evidence.
                    passive_or_lower=(not active)|(~lifted)
                    labels[selected&quiet&passive_or_lower]=4
                elif number in (8,9,18,19) and active:
                    context[mask]=2 # active return/lift context
                    labels[selected&lifted]=1
                    labels[selected&~lifted]=3
                    labels[selected&~lifted&quiet]=0
                elif number in (12,13) and active:
                    context[mask]=3 # affirmative forefoot contact
                    labels[selected]=2
                    labels[selected&quiet&~lifted]=0
                else:
                    context[mask]=1 # standing/support, including03/14/15/16
                    labels[selected]=3
                    labels[selected&quiet&~lifted]=0
            fit=labels>=0
            if any(np.count_nonzero(labels==c)<25 for c in range(6)):
                raise ValueError('insufficient phase evidence for supervised class')
            scale=np.maximum(np.quantile(f[fit],.95,axis=0)-np.quantile(f[fit],.05,axis=0),[.1,.1,.1,.1,.025,.025])
            centers=np.stack([np.median(f[labels==c],axis=0) for c in range(6)])
            radii=np.array([np.quantile(np.linalg.norm((f[labels==c]-centers[c])/scale,axis=1),.95) for c in range(6)])
            obj.parameters[side]=(centers,scale,radii);obj.data[side]=(f,valid,pose_epoch)
            obj.supervision[side]=(labels,context)
            obj.report[side]={'gravity_reference_mps2':reference,'quiet_gyro_rms_limit':glimit,
                'quiet_acc_std_limit':alimit,'centers':centers.tolist(),'scale':scale.tolist(),'radii':radii.tolist(),
                'class_counts':{CLASSES[c]:int(np.count_nonzero(labels==c)) for c in range(6)},
                'action_counts':{name:{str(c):int(np.count_nonzero(mask&(labels==c))) for c in range(-1,6)} for name,mask in masks.items()},
                'context_counts':{name:{str(c):int(np.count_nonzero(mask&(context==c))) for c in range(1,6)} for name,mask in masks.items()},
                'supervision':'ACTUAL_PROTOCOL_CONTEXT_PLUS_RAW_MOTION_PHASES_NOT_OLD_CLASSIFIER',
                'stationary_label_is_weak_proxy_not_sole_contact':True}
        obj.regions_path=str(regions_path);obj.regions_sha256=hashlib.sha256(regions_path.read_bytes()).hexdigest()
        return obj
