"""Archive adapter for causal, conservative support-velocity fusion.

Reading the full archive is an offline transport convenience; each decision
uses only past/current samples and strictly pre-start fitted stillness.
"""
from dataclasses import asdict
from collections import deque
import json

import numpy as np

from .ankle_contact import AnkleContactConfig, AnkleContactDetector, fit_stillness_profiles
from .support_velocity import SupportVelocityConfig, update_support_velocity

SIDES = ('left', 'right')
NODES = ('BSF6C53', 'BSF8BC4')


class ContinuousSupportVelocity:
    def __init__(self, frontend, pose, origin_s, protocol_regions=None, contact_lifecycle=False,
                 supervised_contact=False, support_points=False, calibration_phase_prior=False,
                 restart_stationary_episode=False, soft_support_position_guard=False,
                 rotation_position_bridge=False,navigation_position_handoff=False,
                 calibration_moving_support=False, world_support_policy='legacy'):
        if world_support_policy not in ('legacy','protocol','unavailable'):
            raise ValueError('invalid world support policy')
        if world_support_policy!='legacy' and protocol_regions is None:
            raise ValueError('explicit world support policy requires protocol contact adapter')
        self.world_support_policy=world_support_policy
        self.world_authority=None
        if world_support_policy!='legacy':
            from .world_support_authority import WorldSupportAuthority,calibration_protocol_grants
            grants=calibration_protocol_grants(json.loads(protocol_regions.read_text()),origin_s) if world_support_policy=='protocol' else ()
            self.world_authority=WorldSupportAuthority(grants)
        if calibration_moving_support and not (supervised_contact and calibration_phase_prior):
            raise ValueError('moving support evidence requires supervised calibration phase')
        if contact_lifecycle and protocol_regions is None:
            raise ValueError('contact lifecycle requires frozen protocol model')
        self.contact_lifecycle = bool(contact_lifecycle)
        if soft_support_position_guard and (not support_points or protocol_regions is None):
            raise ValueError('soft support routing requires protocol position evidence and support points')
        self.soft_support_position_guard = bool(soft_support_position_guard)
        self.rotation_position_bridge=bool(rotation_position_bridge)
        self.origin_s = float(origin_s)
        self.config = SupportVelocityConfig()
        self.detector_config = AnkleContactConfig()
        self.samples, calibration, self.acc_reference = {}, {}, {}
        for side, node in zip(SIDES, NODES):
            with np.load(frontend / f'{node}.npz', allow_pickle=False) as archive:
                t = np.array(archive['common_global_ns']) * 1e-9 - origin_s
                acc, gyro = np.array(archive['acc_mps2']), np.array(archive['gyro_rads'])
            if not np.all(np.diff(t) > 0):
                raise ValueError('shank clock must increase')
            prior = t < 0
            if protocol_regions is None and np.count_nonzero(prior) < self.detector_config.window_samples * 2:
                raise ValueError('insufficient strictly pre-start stillness samples')
            if protocol_regions is None:
                calibration[side] = [(acc[prior], gyro[prior])]
                self.acc_reference[side] = float(np.median(np.linalg.norm(acc[prior], axis=1)))
            self.samples[side] = t, acc, gyro
        self.profiles = fit_stillness_profiles(calibration, self.detector_config) if protocol_regions is None else {}
        self.detectors = {side: AnkleContactDetector(self.profiles, self.detector_config,
                          stationary_no_flight_prior=False) for side in SIDES} if protocol_regions is None else {}
        self.last_index = {side: -1 for side in SIDES}
        names = list(map(str, pose['joint_names']))
        self.offsets = pose['joints_relative'][:, [names.index('ankle_' + s) for s in SIDES]]
        self.times = pose['time_s'] - origin_s
        self.velocity = np.zeros_like(self.offsets)
        self.velocity[1:] = np.diff(self.offsets, axis=0) / np.diff(self.times)[:, None, None]
        # Nanosecond source timestamps incur sub-nanosecond float subtraction
        # roundoff. This tolerance is not a future-sample buffer.
        self.indices = {s: np.searchsorted(self.samples[s][0], self.times + 1e-9, side='right') - 1 for s in SIDES}
        self.support_valid = np.zeros(2, dtype=bool)
        self.support_audit = []
        self.support_history = deque(maxlen=8)
        self.audit = []
        self.protocol = None
        self.points=None
        if support_points:
            from .support_points import SupportPoints
            self.points=SupportPoints(restart_stationary_episode=restart_stationary_episode)
        self.protocol_consumed_epochs = np.full(2, -np.inf)
        self.point_consumed_epochs = np.full(2,-np.inf)
        self.point_mask_audit=[]
        if protocol_regions is not None:
            from .protocol_contact import ProtocolContactModel, ProtocolContactStream
            if supervised_contact:
                from .supervised_contact import SupervisedContactModel
                ProtocolContactModel=SupervisedContactModel
            model = ProtocolContactModel.fit(self.samples, self.times, self.offsets, protocol_regions, origin_s)
            model.moving_support_evidence=bool(calibration_moving_support)
            self.acc_reference = {s: model.report[s]['gravity_reference_mps2'] for s in SIDES}
            self.protocol = ProtocolContactStream(model, self.samples, self.config.maximum_sample_age_s,
                                                  lifecycle=self.contact_lifecycle,
                                                  calibration_phase_prior=calibration_phase_prior,
                                                  rotation_position_bridge=rotation_position_bridge,
                                                  navigation_position_handoff=navigation_position_handoff)

    def advance_actual_samples(self, query_time_s):
        if self.protocol is not None:
            self.protocol.advance(query_time_s)

    def world_evidence(self, time_s, points=None):
        """World measurements require separate authority; classifier stays intact."""
        valid,confidence,classes=self.protocol.evidence(time_s)
        if hasattr(self.protocol.model,'support_context'):
            point_valid,point_confidence,moving=self.protocol.position_evidence(time_s)
        else:
            point_valid,point_confidence,moving=valid.copy(),confidence.copy(),np.zeros(2,bool)
        authority=getattr(self,'world_authority',None)
        if authority is not None:
            valid,point_valid=authority.apply(self.points if points is None else points,time_s,valid,point_valid)
        return valid,confidence,classes,point_valid,point_confidence,moving

    def _update_protocol(self, state, index):
        valid,confidence,classes,point_valid,point_confidence,moving=self.world_evidence(state.time_s)
        dt = self.times[index] - self.times[index-1] if index else 0.
        eligible = valid.copy()
        if not 0 < dt <= self.config.maximum_sample_age_s:
            eligible[:] = False
        if self.contact_lifecycle:
            sample_epochs = self.protocol.sample_epochs(state.time_s)
            eligible &= sample_epochs > self.protocol_consumed_epochs
        before = state
        innovation, noise = np.zeros(3), np.zeros((3, 3))
        if np.any(eligible):
            state, innovation, noise = update_support_velocity(state,
                -self.velocity[index, eligible], confidence[eligible], dt, self.config,
                consider_position=self.contact_lifecycle,
                transition_observer=self.points.root_transition if getattr(self,'points',None) is not None else None)
            if self.contact_lifecycle:
                self.protocol_consumed_epochs[eligible] = sample_epochs[eligible]
        if getattr(self,'points',None) is not None:
            epochs=self.protocol.sample_epochs(state.time_s)
            point_eligible=point_valid&(epochs>self.point_consumed_epochs)&(0<dt<=self.config.maximum_sample_age_s)
            state=self.points.update(state,self.offsets[index],point_valid,point_eligible,point_confidence,dt,moving)
            self.point_consumed_epochs[point_eligible]=epochs[point_eligible]
            self.point_mask_audit.append((*point_valid,*moving,*point_eligible))
        self.support_valid = valid
        self.support_audit.append(valid.copy())
        self.audit.append((state.time_s, *eligible, *classes, *innovation, *noise.ravel(),
                           *(state.vector-before.vector)[:9], *np.diag(state.covariance)[:9]))
        return state

    def update(self, state, index):
        if self.protocol is not None:
            self.advance_actual_samples(state.time_s)
            return self._update_protocol(state, index)
        eligible = np.zeros(2, dtype=bool)
        reasons = np.zeros(2, dtype=int)
        targets, confidence = [], []
        dt = self.times[index] - self.times[index - 1] if index else 0.
        for k, side in enumerate(SIDES):
            j = int(self.indices[side][index])
            t, acc, gyro = self.samples[side]
            if j < 0 or not -1e-9 <= state.time_s - t[j] <= self.config.maximum_sample_age_s:
                reasons[k] = 1  # stale
                self.support_valid[k] = False
                continue
            if j == self.last_index[side]:
                reasons[k] = 2  # held samples do not add information
                continue
            if self.last_index[side] >= 0 and t[j] - t[self.last_index[side]] > self.config.maximum_sample_age_s:
                self.detectors[side] = AnkleContactDetector(self.profiles, self.detector_config,
                                                          stationary_no_flight_prior=False)
            self.last_index[side] = j
            height = float(self.offsets[index, k, 2] - np.min(self.offsets[index, :, 2]))
            evidence = self.detectors[side].update(side, time_s=state.time_s,
                acceleration_mps2=acc[j], gyro_rad_s=gyro[j], relative_height_m=height,
                relative_speed_mps=float(np.linalg.norm(self.velocity[index, k])),
                positive_swing=height >= self.detector_config.maximum_height_margin_m,
                swing_observable=True)
            profile = self.profiles[side]
            # Current-sample veto prevents a quiet history admitting pivot or
            # landing impact. Norm deviation includes an explicit 3-sigma
            # engineering margin relative to pre-start gravity magnitude.
            quiet = (evidence.gyro_rms_rad_s <= profile.gyro_rms_rad_s
                     and evidence.acceleration_std_mps2 <= profile.acceleration_std_mps2
                     and np.linalg.norm(gyro[j]) <= profile.gyro_rms_rad_s
                     and abs(np.linalg.norm(acc[j]) - self.acc_reference[side]) <= 3 * profile.acceleration_std_mps2)
            if not quiet:
                reasons[k] = 3
                self.support_valid[k] = False
                continue
            if not evidence.is_confirmed_stance or evidence.positive_swing or dt <= 0 or dt > self.config.maximum_sample_age_s:
                reasons[k] = 4
                self.support_valid[k] = False
                continue
            eligible[k] = True
            self.support_valid[k] = True
            targets.append(-self.velocity[index, k])
            confidence.append(evidence.confidence)
        before = state
        innovation, noise = np.zeros(3), np.zeros((3, 3))
        if targets:
            state, innovation, noise = update_support_velocity(state, targets, confidence, dt, self.config)
        self.audit.append((state.time_s, *eligible, *reasons, *innovation,
                           *noise.ravel(), *(state.vector - before.vector), *np.diag(state.covariance)))
        self.support_audit.append(self.support_valid.copy())
        sample_times = np.array([self.samples[s][0][self.last_index[s]] if self.last_index[s] >= 0 else -np.inf for s in SIDES])
        self.support_history.append((state.time_s, self.support_valid.copy(), sample_times))
        return state

    def position_is_considered(self, query_time_s):
        """Return strictly available support for a raw sweep's first link.

        Context expires; a held sample is not permanent support. No later
        pose decision can retroactively protect an earlier range epoch.
        """
        authority=getattr(self,'world_authority',None)
        side_mask=np.ones(2,bool) if authority is None else authority.mask_at(query_time_s)
        if self.protocol is not None:
            self.advance_actual_samples(query_time_s)
            stationary = bool(np.any(self.protocol.evidence(query_time_s)[0] & side_mask))
            if getattr(self,'rotation_position_bridge',False):
                return stationary or (self.protocol.rotation_position_evidence(query_time_s)
                    if authority is None else self.protocol.rotation_position_evidence(query_time_s,side_mask=side_mask))
            if getattr(self, 'soft_support_position_guard', False) and self.points is not None:
                # Position support does not imply a stationary ankle. Only
                # route the existing UWB gain; never add velocity evidence.
                return stationary or bool(np.any(self.protocol.position_evidence(query_time_s)[0] & side_mask))
            return stationary
        for epoch, valid, samples in reversed(self.support_history):
            if epoch <= query_time_s:
                age = query_time_s - samples
                return bool(query_time_s - epoch <= self.config.maximum_sample_age_s
                            and np.any(valid & side_mask & (age >= -1e-9) & (age <= self.config.maximum_sample_age_s)))
        return False

    def save(self, output):
        if self.points is not None:
            point_rows=np.asarray(self.points.audit).reshape(-1,14)
            masks=np.asarray(self.point_mask_audit).reshape(-1,6)
            np.savez_compressed(output/'SUPPORT_POINTS.npz',rows=point_rows,
                time_s=point_rows[:,0]+self.origin_s,
                valid=masks[:,:2].astype(bool),
                moving=masks[:,2:4].astype(bool),
                eligible=masks[:,4:6].astype(bool),
                stationary_episode_entries=np.asarray(self.points.stationary_entries,float).reshape(-1,2),
                final_base_anchor_cross=self.points.cross,
                final_anchor_covariance=self.points.anchor_covariance,
                final_anchor_means=self.points.means,
                columns=np.array(['time_s','active_points','used_points','innovation_norm_m',
                    'dp_x','dp_y','dp_z','dv_x','dv_y','dv_z','db_x','db_y','db_z','soft_points']))
        if self.protocol is not None and hasattr(self.protocol.model,'supervision'):
            phase=np.asarray([r[:-1] for r in self.protocol.phase_audit],dtype=float).reshape(-1,7)
            reasons=np.asarray([r[-1] for r in self.protocol.phase_audit])
            np.savez_compressed(output/'CALIBRATION_PHASE_PRIOR.npz',rows=phase,
                reasons=reasons,
                columns=np.array(['time_s_relative','side','source_index','protocol_context','raw_class',
                                  'effective_class','override']))
            phase_counts={}
            for side in SIDES:
                mask=phase[:,1]==SIDES.index(side)
                side_rows=phase[mask];side_reasons=reasons[mask]
                phase_counts[side]={}
                for reason in np.unique(side_reasons):
                    phase_counts[side][str(reason)]=int(np.count_nonzero(side_reasons==reason))
                ids=self.protocol.model.action_ids[side]
                action_ids=ids[side_rows[:,2].astype(int)]
                phase_counts[side]['per_action']={str(action):{
                    'samples':int(np.count_nonzero(action_ids==action)),
                    'overrides':int(np.count_nonzero((action_ids==action)&(side_rows[:,6]>0))),
                    'reasons':{str(reason):int(np.count_nonzero((action_ids==action)&(side_reasons==reason)))
                               for reason in np.unique(side_reasons)}}
                    for action in sorted(set(ids))}
            (output/'CALIBRATION_PHASE_COUNTS.json').write_text(json.dumps({
                'enabled':self.protocol.calibration_phase_prior,'semantics':'RETROSPECTIVE_FORMAL_CALIBRATION_ONLY',
                'counts':phase_counts},indent=2))
            for side,(labels,context) in self.protocol.model.supervision.items():
                np.savez_compressed(output/f'SUPERVISION_{side.upper()}.npz',
                    time_s=self.samples[side][0],labels=labels,context=context)
        rows = np.asarray(self.audit).reshape(-1,35)
        np.savez_compressed(output / 'CONTACT.npz', rows=rows, time_s=rows[:, 0] + self.origin_s,
                            eligible=rows[:, 1:3].astype(bool), accepted=np.any(rows[:, 1:3], axis=1),
                            reason=rows[:, 3:5].astype(int),
                            support_valid=np.asarray(self.support_audit).reshape(-1,2),
                            ankle_offsets=self.offsets[:len(self.audit)],
                            ankle_offset_velocity=self.velocity[:len(self.audit)])
        metadata = {'profiles': {s: asdict(p) for s, p in self.profiles.items()},
            'config': asdict(self.config), 'detector': asdict(self.detector_config),
            'acc_norm_reference': self.acc_reference,
            'calibration': 'strictly pre-start; stationary pre-roll assumption',
            'limitation': '3D low-rotation shank velocity proxy, not exact foot contact or a ground-height constraint; same-pose derivative uncertainty is engineering approximation',
            'columns': ['time_s', 'left_eligible', 'right_eligible', 'left_reason', 'right_reason',
                        'innovation_x', 'innovation_y', 'innovation_z'] +
                       ['R_' + str(i) for i in range(9)] +
                       ['delta_' + str(i) for i in range(9)] + ['P_' + str(i) for i in range(9)],
            'reasons': {'0': 'eligible', '1': 'stale', '2': 'duplicate', '3': 'motion_veto', '4': 'not_confirmed_or_time_invalid'}}
        (output / 'CONTACT_CONFIG.json').write_text(json.dumps(metadata, indent=2))
        if self.protocol is not None:
            metadata['calibration'] = 'OFFLINE_FULL_19_FORMAL_ACTION_WEAK_LABEL_FIT_THEN_FROZEN_RUNTIME_NO_ACTION_LOOKUP'
            metadata['profiles'] = self.protocol.model.report
            metadata['regions_source'] = self.protocol.model.regions_path
            metadata['regions_sha256'] = self.protocol.model.regions_sha256
            metadata['contact_lifecycle'] = self.contact_lifecycle
            metadata['contact_position_consider'] = self.contact_lifecycle
            if self.contact_lifecycle:
                metadata['lifecycle_policy'] = 'QUIET_OR_125MS_CONFIRMED_STATIONARY_ANKLE_WITH_FRESH_BILATERAL_SEATED_CONTEXT;UNKNOWN_BRIDGE_MAX125MS;MOTION_LIFT_ROLLING_SWING_STALE_RELEASE'
                metadata['lifecycle_limitation'] = 'Stationary-segment engineering proxy, not measured sole contact; no persistent world footpoint, joint state or pose-error covariance.'
                metadata['seated_proxy_confidence'] = .5
                metadata['lifecycle_confirmation_and_bridge_s'] = .125
                metadata['lifecycle_modes'] = {'0':'RELEASED','1':'DIRECT_QUIET','2':'BOUNDED_UNKNOWN_BRIDGE','3':'STATIONARY_ANKLE_SEATED_CONTEXT'}
                metadata['lifecycle_release_reasons'] = {'0':'ACTIVE','1':'STALE','2':'RAW_MOTION','3':'LIFT','4':'SWING','5':'ROLLING','6':'AWAIT_QUIET_CONFIRMATION','7':'UNKNOWN_NO_RECENT_DIRECT_EVIDENCE','8':'NO_SEATED_CONTEXT'}
            metadata['reasons'] = {'-1': 'UNKNOWN', '0': 'QUIET_ANKLE_SUPPORT_PROXY',
                '1': 'SWING_PROXY', '2': 'ROLLING_CONTACT_PROXY', '3': 'SEATED_PROXY'}
            if hasattr(self.protocol.model,'supervision'):
                from .supervised_contact import CLASSES
                metadata['reasons']={'-1':'UNKNOWN',**{str(i):name for i,name in enumerate(CLASSES)}}
                metadata['supervision']='ACTUAL_PROTOCOL_CONTEXT_AND_INDEPENDENT_RAW_MOTION_PHASES'
                metadata['supervision_time_basis']='SUPERVISION_NPZ_TIME_S_RELATIVE_TO_ORIGIN_GLOBAL_S'
            if self.points is not None:
                metadata['support_points']={'position_sigma_m':self.points.sigma,
                    'soft_position_sigma_m':self.points.soft_sigma,'soft_point_diffusion_m2_per_s':self.points.soft_diffusion,
                    'correlation_time_s':self.points.correlation,'uncertainty_provenance':'ENGINEERING_INCREMENTAL_KINEMATIC_PROXY_NOT_MEASURED',
                    'root_only_updates':'SCHMIDT_ANCHOR_MEAN_AND_PAA_FIXED_ACTUAL_RESIDUAL_MAP_CROSS_PROPAGATION',
                    'contact_update':'FULL_JOINT_JOSEPH','initialization':'SHARED_ROOT_COVARIANCE_NO_ENTRY_DOUBLE_ASSIMILATION'}
                metadata['lifecycle_limitation']='Stochastic ankle point proxy; FK/IMU observation crossnoise not modelled, no joint state or measured sole contact.'
            (output / 'CONTACT_CONFIG.json').write_text(json.dumps(metadata, indent=2))
            events = np.asarray(self.protocol.audit).reshape(-1,6)
            np.savez_compressed(output / 'CONTACT_SENSOR_EVENTS.npz', rows=events,
                time_s=events[:, 0] + self.origin_s,
                columns=np.array(['relative_time', 'side', 'actual_sample_index', 'class', 'confidence', 'pose_epoch']))
            if self.contact_lifecycle:
                rows = np.asarray(self.protocol.lifecycle_audit).reshape(-1,12)
                np.savez_compressed(output / 'CONTACT_LIFECYCLE_EVENTS.npz', rows=rows,
                    time_s=rows[:,0]+self.origin_s,
                    columns=np.array(['relative_time','side','actual_sample_index','raw_class','mode',
                        'effective_confidence','fresh','quiet_motion','lifted','seated_context',
                        'release_reason','last_direct_relative_s']))
