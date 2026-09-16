"""Offline subject calibration, then frozen causal shank-support classification.

Labels are protocol-informed engineering proxies, not measured sole contact.
Action identity is used only by fit(), never by the runtime classifier.
"""
from collections import deque
import json
import hashlib

import numpy as np

CLASSES = ('QUIET_ANKLE_SUPPORT_PROXY', 'SWING_PROXY', 'ROLLING_CONTACT_PROXY', 'SEATED_PROXY')


def causal_features(t, acc, gyro, pose_times, offsets, side, gravity_reference):
    n = 25
    a = np.linalg.norm(acc, axis=1)
    g = np.linalg.norm(gyro, axis=1)
    count = np.minimum(np.arange(len(t)) + 1, n)
    def mean(x):
        c = np.r_[0., np.cumsum(x)]
        return (c[1:] - c[np.maximum(0, np.arange(len(x)) + 1 - n)]) / count
    std = np.sqrt(np.maximum(0., mean(a*a) - mean(a)**2))
    rms = np.sqrt(mean(g*g))
    ix = np.searchsorted(pose_times, t + 1e-9, side='right') - 1
    safe = np.clip(ix, 0, len(pose_times)-1)
    depth = -offsets[safe, side, 2]
    height = offsets[safe, side, 2] - np.min(offsets[safe, :, 2], axis=1)
    features = np.column_stack((np.log1p(rms/.01), np.log1p(g/.01),
        np.log1p(std/.01), np.log1p(abs(a-gravity_reference)/.01), height, depth))
    valid = (ix >= 0) & (t-pose_times[safe] >= -1e-9) & (t-pose_times[safe] <= .0075)
    valid &= count == n
    last_gap = np.maximum.accumulate(np.where(np.r_[True, np.diff(t) > .0075], np.arange(len(t)), 0))
    valid &= np.arange(len(t)) - last_gap >= n-1
    return features, valid, pose_times[safe], rms, std


class ProtocolContactModel:
    @classmethod
    def fit(cls, samples, times, offsets, regions_path, origin_s):
        obj = cls()
        regions = json.loads(regions_path.read_text())
        actions = [r for r in regions if r['kind'] == 'ACTION']
        expected = {'00_initial_still', '02_t_pose', '03_pelvis_hula_circle',
            '04_shoulder_left', '05_shoulder_right', '06_elbow_left', '07_elbow_right',
            '08_hip_left', '09_hip_right', '10_knee_left_seated', '11_knee_right_seated',
            '12_heel_raise_left', '13_heel_raise_right', '14_trunk_flex_extend',
            '15_trunk_axial_rotation', '16_squat', '17_final_still',
            '18_heel_to_butt_left', '19_heel_to_butt_right'}
        if len(actions) != 19 or {r['id'] for r in actions} != expected:
            raise ValueError('protocol fit requires exactly the 19 recorded calibration actions, no Hxx')
        obj.data, obj.parameters, obj.report = {}, {}, {}
        for k, side in enumerate(('left', 'right')):
            t, acc, gyro = samples[side]
            masks = {r['id']: (t >= r['start_s']-origin_s) & (t < r['end_s']-origin_s) for r in actions}
            still = masks['00_initial_still'] | masks['02_t_pose'] | masks['17_final_still']
            reference = float(np.median(np.linalg.norm(acc[still], axis=1)))
            f, valid, pose_epoch, rms, std = causal_features(t, acc, gyro, times, offsets, k, reference)
            g_limit = float(np.quantile(rms[still & valid], .95))
            a_limit = float(np.quantile(std[still & valid], .99))
            standing_depth = float(np.quantile(f[still & valid, 5], .05))
            quiet = (rms <= g_limit) & (std <= a_limit) & (f[:, 5] >= standing_depth-.05) & valid
            labels = np.full(len(t), -1, dtype=int)
            for name, mask in masks.items():
                if name.startswith(('00_', '02_', '04_', '05_', '06_', '07_', '17_')):
                    labels[mask & quiet] = 0
                if name.startswith(('08_', '09_', '18_', '19_')):
                    active = ('left' in name) == (side == 'left')
                    if active:
                        lifted = (f[:, 4] >= .075) | (f[:, 5] < standing_depth-.075)
                        labels[mask & valid & lifted] = 1
                    else:
                        labels[mask & quiet] = 0
                if name.startswith(('12_', '13_')) and (('left' in name) == (side == 'left')):
                    labels[mask & valid & (rms > g_limit)] = 2
                if name.startswith(('10_', '11_')):
                    labels[mask & valid] = 3
            fit = labels >= 0
            if any(np.count_nonzero(labels == c) < 25 for c in range(4)):
                raise ValueError('insufficient protocol evidence for one contact class')
            scale = np.maximum(np.quantile(f[fit], .95, axis=0)-np.quantile(f[fit], .05, axis=0),
                               np.array([.1, .1, .1, .1, .025, .025]))
            centers = np.stack([np.median(f[labels == c], axis=0) for c in range(4)])
            radii = np.array([np.quantile(np.linalg.norm((f[labels == c]-centers[c])/scale, axis=1), .95)
                              for c in range(4)])
            obj.parameters[side] = (centers, scale, radii)
            obj.data[side] = (f, valid, pose_epoch)
            obj.report[side] = {'gravity_reference_mps2': reference,
                'quiet_gyro_rms_limit': g_limit, 'quiet_acc_std_limit': a_limit,
                'centers': centers.tolist(), 'scale': scale.tolist(), 'radii': radii.tolist(),
                'class_counts': {CLASSES[c]: int(np.count_nonzero(labels == c)) for c in range(4)},
                'action_counts': {name: {str(c): int(np.count_nonzero(mask & (labels == c))) for c in (-1,0,1,2,3)} for name,mask in masks.items()}}
        obj.regions_path = str(regions_path)
        obj.regions_sha256 = hashlib.sha256(regions_path.read_bytes()).hexdigest()
        return obj

    def classify(self, side, index):
        f, valid, _ = self.data[side]
        return self.classify_feature(side, f[index], bool(valid[index]))

    def classify_feature(self, side, feature, valid=True):
        """Frozen-model prediction: no action metadata or future sample access."""
        feature = np.asarray(feature, dtype=float)
        if not valid or feature.shape != (6,) or not np.isfinite(feature).all():
            return -1, 0.
        centers, scale, radii = self.parameters[side]
        distance = np.linalg.norm((feature-centers)/scale, axis=1)
        order = np.argsort(distance)
        best = int(order[0])
        # Unknown is explicit; no always-nearest-class support assignment.
        margin = float(distance[order[1]]-distance[best])
        if distance[best] > radii[best] or margin < .15:
            return -1, 0.
        return best, min(1., max(.1, margin))


class ProtocolContactStream:
    """Consume every actual shank sample; classify once at its own epoch."""
    def __init__(self, model, samples, maximum_age_s=.0075, lifecycle=False, calibration_phase_prior=False,
                 rotation_position_bridge=False, navigation_position_handoff=False):
        self.model, self.samples, self.maximum_age_s = model, samples, maximum_age_s
        self.cursor = {s: int(np.searchsorted(samples[s][0], 0.)) - 1 for s in samples}
        self.latest = {s: (-np.inf, -np.inf, -1, 0.) for s in samples}
        self.history = deque(maxlen=32)
        self.audit = []
        self.calibration_phase_prior=calibration_phase_prior
        self.phase_audit=[]
        self.lifecycle = None
        self.rotation_bridge=None
        self.navigation_handoff=None
        if navigation_position_handoff:
            if not rotation_position_bridge:raise ValueError('handoff requires rotation position bridge')
            from .contact_lifecycle import NavigationPositionHandoff
            self.navigation_handoff=NavigationPositionHandoff()
        if rotation_position_bridge:
            if not lifecycle or not calibration_phase_prior:
                raise ValueError('rotation bridge requires formal supervised lifecycle')
            from .contact_lifecycle import RotationPositionBridge
            self.rotation_bridge={s:RotationPositionBridge(maximum_age_s=maximum_age_s) for s in samples}
        self.effective = {s: (False, 0.) for s in samples}
        self.lifecycle_audit = []
        self.point_latest={s:(-np.inf,-np.inf,False,0.,False) for s in samples}
        self.point_direct={s:None for s in samples}
        self.point_history=deque(maxlen=32)
        if lifecycle:
            from .contact_lifecycle import ContactLifecycle
            self.lifecycle = {s: ContactLifecycle(maximum_age_s=maximum_age_s) for s in samples}

    def advance(self, query):
        events = []
        for side, (t, _, _) in self.samples.items():
            end = int(np.searchsorted(t, query + 1e-9, side='right'))
            events.extend((float(t[j]), side, j) for j in range(self.cursor[side]+1, end))
        for epoch, side, j in sorted(events):
            cls, confidence = self.model.classify(side, j)
            raw_class,raw_confidence=cls,confidence
            prior_context=-1;phase_reason='FEATURES_ONLY'
            if self.calibration_phase_prior:
                prior_context,phase_class,phase_confidence,phase_reason=self.model.calibration_phase(side,j)
                if prior_context>=0:cls,confidence=phase_class,phase_confidence
            pose_epoch = float(self.model.data[side][2][j])
            self.latest[side] = epoch, pose_epoch, cls, confidence
            self.cursor[side] = j
            if self.lifecycle is not None:
                features, feature_valid, _ = self.model.data[side]
                f = features[j]
                profile = self.model.report[side]
                gyro_limit = np.log1p(profile['quiet_gyro_rms_limit']/.01)
                acc_limit = np.log1p(profile['quiet_acc_std_limit']/.01)
                departure_limit = np.log1p(3*profile['quiet_acc_std_limit']/.01)
                quiet = bool(f[0] <= gyro_limit and f[1] <= gyro_limit
                             and f[2] <= acc_limit and f[3] <= departure_limit)
                lifted = bool(f[4] >= .075)
                if prior_context >= 0 and hasattr(self.model,'phase_lift_evidence'):
                    lifted = self.model.phase_lift_evidence(side,j,prior_context)
                fresh = bool(feature_valid[j] and -1e-9 <= epoch-pose_epoch <= self.maximum_age_s)
                seated_classes=getattr(self.model,'seated_classes',(3,))
                seated = any(raw in seated_classes and -1e-9 <= epoch-sample <= self.maximum_age_s
                             and -1e-9 <= epoch-pose <= self.maximum_age_s
                             for sample, pose, raw, _ in self.latest.values())
                lifecycle_class=cls
                if hasattr(self.model,'stationary_classes'):
                    lifecycle_class=0 if cls in self.model.stationary_classes else (-1 if cls in (3,5) else cls)
                    if cls in (3,5):quiet=False # moving support is not airborne, nor stationary
                mode, effective_confidence = self.lifecycle[side].update(epoch, lifecycle_class, confidence,
                    fresh=fresh, quiet_motion=quiet, lifted=lifted, seated_context=seated)
                self.effective[side] = (mode, effective_confidence)
                if self.rotation_bridge is not None:
                    self.rotation_bridge[side].update(epoch,pose_epoch,fresh=fresh,
                        acceleration_quiet=bool(f[2]<=acc_limit and f[3]<=departure_limit),
                        lifted=lifted,direct_stationary=bool(mode in (1,3) and cls in (0,4)),
                        rotation_only=bool(cls==3 and phase_reason=='SUPPORTED_ARTICULATION'))
                self.lifecycle_audit.append((epoch, 0 if side == 'left' else 1, j, cls,
                    mode, effective_confidence, fresh, quiet, lifted, seated,
                    self.lifecycle[side].reason,
                    np.nan if self.lifecycle[side].last_direct is None else self.lifecycle[side].last_direct))
                if hasattr(self.model,'support_context'):
                    supported,point_confidence=self.model.support_context(side,j)
                    if prior_context>=0:
                        supported=cls in (0,3,4)
                        point_confidence=confidence
                    stationary=mode!=0
                    # In formal standing/return contexts the phase owner emits
                    # adjustment UNKNOWN only after lift, seated and forefoot
                    # checks. This is uncertainty, not affirmative liftoff.
                    adjustment_unknown=(cls==-1 and prior_context in (1,2)
                        and phase_reason=='ADJUSTMENT_ACCELERATION_PROXY')
                    veto=(not fresh or lifted or cls in (1,2,5)
                          or (prior_context>=0 and cls not in (0,3,4) and not adjustment_unknown))
                    if epoch-self.point_latest[side][0]>self.maximum_age_s:
                        self.point_direct[side]=None
                    point_valid=False;moving=False;point_deadline=-np.inf
                    if veto:
                        self.point_direct[side]=None
                    elif supported or (stationary and mode!=self.lifecycle[side].BRIDGED):
                        point_valid=True;moving=not stationary
                        point_confidence=effective_confidence if stationary else point_confidence
                        self.point_direct[side]=(epoch,point_confidence)
                        point_deadline=np.inf
                    elif cls==-1 and (prior_context<0 or adjustment_unknown) and self.point_direct[side] is not None:
                        previous,previous_confidence=self.point_direct[side]
                        if epoch-previous<.125:
                            point_valid=True;moving=True
                            point_confidence=previous_confidence*(1-(epoch-previous)/.125)
                            point_deadline=previous+.125
                        else:
                            self.point_direct[side]=None
                    self.point_latest[side]=(epoch,pose_epoch,point_valid,point_confidence if point_valid else 0.,moving,point_deadline)
                    self.point_history.append((epoch,dict(self.point_latest)))
            self.history.append((epoch, dict(self.latest), dict(self.effective)))
            if self.navigation_handoff is not None:
                deadline=epoch
                for s,(sample,pose,_,_) in self.latest.items():
                    expiry=min(sample,pose)+self.maximum_age_s
                    mode,_=self.effective[s]
                    if mode:
                        if mode==3:
                            seated_expiry=max([min(st,po)+self.maximum_age_s for st,po,cl,_ in self.latest.values()
                                if cl in getattr(self.model,'seated_classes',(3,))],default=-np.inf)
                            expiry=min(expiry,seated_expiry)
                        deadline=max(deadline,expiry)
                    bridge=self.rotation_bridge[s]
                    if bridge.history:
                        bs,bp,active,bd=bridge.history[-1]
                        if active:deadline=max(deadline,min(bs+self.maximum_age_s,bp+self.maximum_age_s,bd))
                self.navigation_handoff.advance(epoch,deadline)
            self.audit.append((epoch, 0 if side == 'left' else 1, j, raw_class, raw_confidence, pose_epoch))
            self.phase_audit.append((epoch,0 if side=='left' else 1,j,prior_context,raw_class,cls,
                                     int(prior_context>=0 and cls!=raw_class),phase_reason))

    def sample_epochs(self, query):
        """Actual sample identities in the same historical snapshot as evidence."""
        for epoch, snapshot, _ in reversed(self.history):
            if epoch <= query + 1e-9:
                return np.array([snapshot[side][0] for side in ('left', 'right')])
        return np.full(2, -np.inf)

    def position_evidence(self,query):
        """Soft point support is separate from stationary/zero-velocity evidence."""
        for epoch,snapshot in reversed(self.point_history):
            if epoch<=query+1e-9:
                valid=[];confidence=[];moving=[]
                for side in ('left','right'):
                    sample,pose,active,conf,soft,*deadline=snapshot[side]
                    fresh=(-1e-9<=query-sample<=self.maximum_age_s and -1e-9<=query-pose<=self.maximum_age_s)
                    fresh &= not deadline or query<deadline[0]
                    valid.append(active and fresh);confidence.append(conf if fresh else 0.);moving.append(soft)
                return np.array(valid),np.array(confidence),np.array(moving)
        return np.zeros(2,bool),np.zeros(2),np.zeros(2,bool)

    def evidence(self, query):
        # Rotation bridge is intentionally absent from velocity evidence.
        for epoch, snapshot, effective in reversed(self.history):
            if epoch <= query + 1e-9:
                valid, confidence, classes = [], [], []
                for side in ('left', 'right'):
                    sample, pose, cls, conf = snapshot[side]
                    fresh = (-1e-9 <= query-sample <= self.maximum_age_s
                             and -1e-9 <= query-pose <= self.maximum_age_s)
                    mode, effective_confidence = (int(cls == 0), conf) if self.lifecycle is None else effective[side]
                    active = mode != 0
                    if self.lifecycle is not None and mode == 3:
                        # The query may be later than this ankle sample. Do not
                        # extend bilateral posture context beyond its own age.
                        active &= any(raw in getattr(self.model,'seated_classes',(3,)) and -1e-9 <= query-sample_time <= self.maximum_age_s
                                      and -1e-9 <= query-pose_time <= self.maximum_age_s
                                      for sample_time, pose_time, raw, _ in snapshot.values())
                    valid.append(fresh and active)
                    confidence.append(effective_confidence if fresh else 0.)
                    classes.append(cls if fresh else -1)
                return np.array(valid), np.array(confidence), np.array(classes)
        return np.zeros(2, bool), np.zeros(2), np.full(2, -1)

    def rotation_position_evidence(self,query,side_mask=None):
        if side_mask is None:
            return self.rotation_bridge is not None and any(o.evidence(query) for o in self.rotation_bridge.values())
        return self.rotation_bridge is not None and any(
            side_mask[i] and side in self.rotation_bridge and self.rotation_bridge[side].evidence(query)
            for i,side in enumerate(('left','right')))

    def navigation_position_gain(self,query):
        return 1. if self.navigation_handoff is None else self.navigation_handoff.gain(query)
