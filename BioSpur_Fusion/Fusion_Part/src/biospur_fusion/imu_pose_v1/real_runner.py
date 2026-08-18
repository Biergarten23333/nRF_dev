from __future__ import annotations

from dataclasses import replace
import hashlib, json, time
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
import numpy as np

from . import so3
from .baselines import build_b0,build_b1
from .calibration import CalibrationBundle,SegmentCalibration
from .decoder import decode_imu_only
from .fk import bone_lengths
from .governance import Phase3RDatasetBroker
from .joints import JOINTS
from .mapping import FrozenOperatorMapping
from .official import run_official_vqf_all,run_qmt_heading,run_qmt_hinge_axis,run_qmt_reset_alignment
from .pipeline import group_samples,run_frontends,run_coupled
from .serialization import write_jsonl
from .visualization import render_triptych


def write_json(path:Path,value) -> None:path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n")
def sha(path:Path)->str:return hashlib.sha256(path.read_bytes()).hexdigest()


def quat_mean(q:np.ndarray)->np.ndarray:
    q=np.asarray(q,float);A=np.einsum('ni,nj->ij',q,q);_,v=np.linalg.eigh(A);out=v[:,-1]
    return so3.normalize(out if out[0]>=0 else -out)


def desired_tpose(segment:str)->np.ndarray:
    if segment.startswith(('upper_arm_left','forearm_left')):return so3.from_two_vectors([0,0,-1],[1,0,0])
    if segment.startswith(('upper_arm_right','forearm_right')):return so3.from_two_vectors([0,0,-1],[-1,0,0])
    return np.array([1.,0,0,0])


def common_raw(parent:list,child:list,rate_hz:float=100.0):
    start=max(parent[0].time_s,child[0].time_s);stop=min(parent[-1].time_s,child[-1].time_s);t=np.arange(start,stop,1/rate_hz)
    def interp(rows,field):
        ts=np.array([x.time_s for x in rows]);vals=np.vstack([getattr(x,field) for x in rows])
        return np.column_stack([np.interp(t,ts,vals[:,k]) for k in range(3)])
    return t,interp(parent,'gyro_rad_s'),interp(child,'gyro_rad_s'),interp(parent,'accel_m_s2'),interp(child,'accel_m_s2')


def vqf_grid(result,t):
    idx=np.searchsorted(result.time_s,t).clip(0,len(result.time_s)-1);return result.quaternion6D_W_I[idx]


class RealSessionRunner:
    def __init__(self,repo:Path,dataset:Path,state:Path,evidence:Path):
        self.repo=repo;self.base=repo/'BioSpur_Fusion/Fusion_Part';self.dataset=dataset;self.state=state;self.evidence=evidence
        self.selection=json.loads((self.base/'config/fusion_v2/phase3r/PHASE3R_DATA_SELECTION.json').read_text())
        source=json.loads((self.base/'reports/fusion_v2/phase2r/phase2r_20260817T154655Z/OPERATOR_GROUND_TRUTH_MAPPING_BINDING.json').read_text())
        self.mapping=FrozenOperatorMapping.from_payload(source,capture_id='Capture_2_with_JOINT_LABEL',session_id='capture_2_with_joint_label',donning_id='capture_2_with_joint_label_donning_01')
        self.broker=Phase3RDatasetBroker.bootstrap(dataset,state/'DATA_ACCESS_LEDGER.jsonl','P3R-real-replay')
        self.broker.load_policy_addendum(dataset/'DATA_ACCESS_POLICY_ADDENDUM_003.json')
        plan=self.broker.read_bytes(dataset/'CAPTURE_PLAN_FINAL.json',purpose='rebind exact plan before numeric replay')
        tracked=json.loads((self.base/'config/fusion_v2/phase3/PHASE3_DATA_SELECTION_ALLOWLIST.json').read_text())
        self.broker.register_literal_selection(tracked,plan)
        self.rows={x['action_id']:x for x in (*self.selection['development_windows'],*self.selection['retrospective_diagnostics'])}
        self.cache={}

    def load(self,action_id:str):
        if action_id in self.cache:return self.cache[action_id]
        row=self.rows[action_id];payload=self.broker.read_bytes(Path(row['raw']),purpose=f'Phase3-R selective IMU read {action_id}')
        samples,audit=decode_imu_only(payload,include_start_s=0,include_stop_s=row['preparation_s']+row['formal_s']+row['recovery_s'])
        if action_id in ('00_initial_still','17_final_still'):
            # Operator notes explicitly say preparation/recovery need not be
            # relaxed.  Only the scheduled formal interval is independent
            # evidence for a gyro/observable-accel-bias pseudo-measurement.
            origin=min(x.time_s for x in samples)
            formal_start=origin+row['preparation_s']
            formal_stop=formal_start+row['formal_s']
            samples=[replace(x,rest_evidence=formal_start <= x.time_s < formal_stop) for x in samples]
        self.broker.record_consumption(Path(row['raw']),purpose=f'Phase3-R decoded IMU accounting {action_id}',numeric_measurements=audit.imu_numeric_scalars,arrays=1,factors=0)
        if audit.uwb_numeric_scalars or audit.uwb_arrays:raise RuntimeError('forbidden UWB numeric decode')
        grouped=group_samples(samples)
        if set(grouped)!=set(self.mapping.node_to_segment):raise RuntimeError(f'{action_id}: not 10 nodes')
        self.cache[action_id]=(grouped,audit);return grouped,audit

    def calibrate(self):
        needed=('00_initial_still','02_t_pose','06_elbow_left','07_elbow_right','10_knee_left_seated','11_knee_right_seated')
        data={a:self.load(a)[0] for a in needed};vqf={a:run_official_vqf_all(data[a]) for a in needed}
        nodes=sorted(self.mapping.node_to_segment)
        def aligned_stack(action):
            results=vqf[action];start=max(x.time_s[0] for x in results.values());stop=min(x.time_s[-1] for x in results.values());t=np.arange(start,stop,.005)
            return t,np.stack([vqf_grid(results[n],t) for n in nodes])
        tn,qn=aligned_stack('00_initial_still');tt,qt=aligned_stack('02_t_pose')
        in_idx=len(tn)//2;t_idx=len(tt)//2
        neutral_aligned=run_qmt_reset_alignment(qn,in_idx,np.tile([1.,0,0,0],(10,1)))
        desired=np.stack([desired_tpose(self.mapping.segment_for(n)) for n in nodes])
        tpose_aligned=run_qmt_reset_alignment(qt,t_idx,desired)
        rows={};reset_errors={}
        for k,node in enumerate(nodes):
            segment=self.mapping.segment_for(node);source_q=qt[k,t_idx] if segment.startswith(('upper_arm','forearm')) else qn[k,in_idx]
            target=desired[k] if segment.startswith(('upper_arm','forearm')) else np.array([1.,0,0,0])
            qIS=so3.mul(so3.inv(source_q),target)
            source_series=qt[k,max(0,t_idx-100):t_idx+101] if segment.startswith(('upper_arm','forearm')) else qn[k,max(0,in_idx-100):in_idx+101]
            candidates=so3.mul(so3.inv(source_series),np.tile(target,(len(source_series),1)));mean=quat_mean(candidates)
            err=so3.log(so3.mul(so3.inv(mean),candidates));cov=np.cov(err.T)+np.eye(3)*np.deg2rad(2.0)**2
            rows[node]=SegmentCalibration(node,segment,mean,cov,'T_POSE_CONDITIONED' if segment.startswith(('upper_arm','forearm')) else 'NEUTRAL_CONDITIONAL',
                ('qmt.resetAlignment','00_initial_still','02_t_pose'), 'C2CC_DISTINCT' if node=='BSFC2CC' else 'H9')
            aligned=tpose_aligned[k,t_idx] if segment.startswith(('upper_arm','forearm')) else neutral_aligned[k,in_idx]
            reset_errors[node]=float(so3.geodesic(aligned,target))
        calibration=CalibrationBundle(MappingProxyType(rows))
        pairs={
          'elbow_left':('06_elbow_left','upper_arm_left','forearm_left'),
          'elbow_right':('07_elbow_right','upper_arm_right','forearm_right'),
          'knee_left':('10_knee_left_seated','thigh_left','shank_left'),
          'knee_right':('11_knee_right_seated','thigh_right','shank_right')}
        segment_node={v:k for k,v in self.mapping.node_to_segment.items()};axes={};targets={};axis_confidence={};heading_confidence={};qmt_report={}
        for joint,(action,parent_seg,child_seg) in pairs.items():
            pn,cn=segment_node[parent_seg],segment_node[child_seg];t,gp,gc,ap,ac=common_raw(data[action][pn],data[action][cn])
            axis=run_qmt_hinge_axis(ap,ac,gp,gc);axes[joint]=so3.matrix(calibration.by_node[cn].q_I_S).T@axis.child_axis_sensor
            qp=vqf_grid(vqf[action][pn],t);qc=vqf_grid(vqf[action][cn],t)
            heading=run_qmt_heading(gp,gc,qp,qc,t,axis.child_axis_sensor)
            # qmt estimates a heading *offset*, not an absolute joint pose.
            # Store the constant correction as a pure-yaw quaternion.  The
            # estimator's causal heading reference is formed from the first
            # B1-corrected frame of each action below.
            offset=float(np.nanmedian(heading.filtered_offset_rad))
            targets[joint]=so3.exp(np.array([0.,0.,offset]))
            axis_confidence[joint]=float(axis.confidence)
            heading_confidence[joint]=float(heading.confidence)
            qmt_report[joint]={"action_id":action,"axis_parent_sensor":axis.parent_axis_sensor.tolist(),"axis_child_sensor":axis.child_axis_sensor.tolist(),
                "axis_child_segment":axes[joint].tolist(),"axis_confidence":axis.confidence,"heading_confidence":heading.confidence,
                "heading_offset_median_rad":offset,"axis_runtime_s":axis.runtime_s,"heading_runtime_s":heading.runtime_s,
                "official_qmt_executed":True}
        report={"schema":"biospur-phase3r-real-calibration-v1","qmt_reset_executed":{"neutral":True,"t_pose":True},
                "reset_error_rad":reset_errors,"nodes":{n:{"segment":c.segment,"q_I_S":c.q_I_S.tolist(),"covariance_rad2":c.covariance_rad2.tolist(),"status":c.twist_status,"layout":c.layout_class} for n,c in rows.items()},
                "qmt_functional":qmt_report,"H9_pool":sorted(set(nodes)-{'BSFC2CC'}),"C2CC_pooled":False,
                "external_accuracy_claim":False}
        write_json(self.evidence/'REAL_SENSOR_TO_SEGMENT_AND_QMT_CALIBRATION.json',report)
        self.qmt_source_action={joint:row[0] for joint,row in pairs.items()}
        return calibration,axes,targets,axis_confidence,heading_confidence,report

    def _persist_vqf(self,out:Path,vqf:Mapping[str,object]) -> dict:
        arrays={}
        for node,result in vqf.items():
            arrays.update({f'time_{node}':result.time_s,f'q6d_{node}':result.quaternion6D_W_I,
                           f'bias_{node}':result.gyro_bias_rad_s,f'bias_sigma_{node}':result.bias_sigma_rad_s,
                           f'rest_{node}':result.rest_detected})
        state_path=out/'vqf_full_state.npz';np.savez_compressed(state_path,**arrays)
        lineage_path=out/'vqf_lineage.jsonl'
        with lineage_path.open('w',encoding='utf-8') as stream:
            for node,result in sorted(vqf.items()):
                for index,(stamp,uids) in enumerate(zip(result.time_s,result.lineage_sample_uids)):
                    stream.write(json.dumps({'node_id':node,'uniform_index':index,'time_s':float(stamp),
                                             'source_sample_uids':list(uids)},sort_keys=True,separators=(',',':'))+'\n')
        manifest={'schema':'biospur-phase3r-vqf-state-v1','implementation':'official-vqf-updateBatchFullState',
                  'nodes':{node:{'samples':len(result.time_s),'runtime_s':result.runtime_s,
                                 'quaternion_shape':list(result.quaternion6D_W_I.shape),
                                 'bias_shape':list(result.gyro_bias_rad_s.shape),
                                 'bias_sigma_shape':list(result.bias_sigma_rad_s.shape),
                                 'rest_shape':list(result.rest_detected.shape),
                                 'lineage_records':len(result.lineage_sample_uids)} for node,result in sorted(vqf.items())},
                  'state_sha256':sha(state_path),'lineage_sha256':sha(lineage_path)}
        write_json(out/'VQF_MANIFEST.json',manifest);return manifest

    @staticmethod
    def _qmt_inputs_for_action(action_id,source_actions,axes,targets,axis_confidence,heading_confidence):
        excluded=sorted(j for j,source in source_actions.items() if source==action_id)
        keep=lambda values:{j:v for j,v in values.items() if j not in excluded}
        return excluded,keep(axes),keep(targets),keep(axis_confidence),keep(heading_confidence)

    def process(self,action_id,calibration,axes,targets,axis_confidence,heading_confidence,animate=False):
        started=time.perf_counter();grouped,audit=self.load(action_id);vqf=run_official_vqf_all(grouped);b0=build_b0(vqf,self.mapping,calibration)
        source_actions=getattr(self,'qmt_source_action',{})
        excluded,active_axes,active_targets,active_axis_confidence,active_heading_confidence=self._qmt_inputs_for_action(
            action_id,source_actions,axes,targets,axis_confidence,heading_confidence)
        baseline_confidence={j:min(axis_confidence.get(j,0),heading_confidence.get(j,0)) for j in targets}
        offsets={j:np.full(len(b0.time_s),float(so3.log(targets[j])[2])) for j in targets}
        b1=build_b1(b0,offsets,baseline_confidence,axes)
        initializer=b0 if excluded else b1
        initial={node:so3.mul(initializer.segment_quaternions[self.mapping.segment_for(node)][0],so3.inv(calibration.by_node[node].q_I_S)) for node in grouped}
        initial_heading_targets={j.name:so3.between(initializer.segment_quaternions[j.parent][0],initializer.segment_quaternions[j.child][0])
                                 for j in JOINTS if j.name in active_targets and active_heading_confidence.get(j.name,0)>=.25}
        front,front_report=run_frontends(grouped,initial_q_WI=initial)
        estimator_started=time.perf_counter()
        frames,est=run_coupled(front,self.mapping,calibration,hinge_axes=active_axes,hinge_confidence=active_axis_confidence,
                               heading_targets=initial_heading_targets,heading_confidence=active_heading_confidence)
        estimator_runtime=time.perf_counter()-estimator_started
        factor_count=sum(x['count'] for x in est.activation_report().values())+sum(sum(v['factor_counts'].values()) for v in front_report.values())
        self.broker.record_consumption(Path(self.rows[action_id]['raw']),purpose=f'Phase3-R estimator factor accounting {action_id}',numeric_measurements=0,arrays=0,factors=factor_count)
        out=self.evidence/'actions'/action_id;out.mkdir(parents=True,exist_ok=True);write_jsonl(out/'production_pose.jsonl',frames)
        vqf_manifest=self._persist_vqf(out,vqf)
        segs=sorted(b0.segment_quaternions);points=sorted(b0.normalized_positions[0]);
        np.savez_compressed(out/'pose_trajectories.npz',b0_time=b0.time_s,b1_time=b1.time_s,p_time=np.array([f.time_s for f in frames]),
            **{f'b0_q_{s}':b0.segment_quaternions[s] for s in segs},**{f'b1_q_{s}':b1.segment_quaternions[s] for s in segs},
            **{f'b0_joint_q_{j}':b0.joint_quaternions[j] for j in b0.joint_quaternions},
            **{f'b1_joint_q_{j}':b1.joint_quaternions[j] for j in b1.joint_quaternions},
            **{f'b0_tilt_sigma_{s}':b0.segment_tilt_sigma_rad[s] for s in segs},
            **{f'b1_tilt_sigma_{s}':b1.segment_tilt_sigma_rad[s] for s in segs},
            **{f'b0_joint_sigma_{j}':b0.joint_relative_sigma_rad[j] for j in b0.joint_relative_sigma_rad},
            **{f'b1_joint_sigma_{j}':b1.joint_relative_sigma_rad[j] for j in b1.joint_relative_sigma_rad},
            **{f'p_q_{s}':np.stack([f.segment_quaternions_W_S[s] for f in frames]) for s in segs},
            **{f'p_joint_q_{j.name}':np.stack([f.joint_quaternions_parent_child[j.name] for f in frames]) for j in JOINTS},
            **{f'p_tilt_sigma_{s}':np.array([f.segment_tilt_sigma_rad[s] for f in frames]) for s in segs},
            **{f'p_joint_sigma_{j.name}':np.array([f.joint_relative_sigma_rad[j.name] for f in frames]) for j in JOINTS},
            **{f'p_pos_{p}':np.stack([f.normalized_joint_positions[p] for f in frames]) for p in points})
        write_json(out/'BASELINE_MANIFEST.json',{'schema':'biospur-phase3r-baseline-manifest-v1','B0':{'name':b0.name,'quality':b0.quality,'metadata':b0.metadata},
                   'B1':{'name':b1.name,'quality':b1.quality,'metadata':b1.metadata},'root_world_position':'UNAVAILABLE',
                   'global_yaw':'GAUGE_ACTIVE','scale':'NORMALIZED_MODEL_SCALE','external_truth':False})
        ptime=np.array([f.time_s for f in frames]);i0=np.searchsorted(b0.time_s,ptime).clip(0,len(b0.time_s)-1);i1=np.searchsorted(b1.time_s,ptime).clip(0,len(b1.time_s)-1)
        def diff(a,b):return np.array([so3.geodesic(a[s][i],b[s][i]) for s in segs for i in range(min(len(a[s]),len(b[s])))])
        formal_start=min(x.time_s for rows in grouped.values() for x in rows)+self.rows[action_id]['preparation_s']
        formal_stop=formal_start+self.rows[action_id]['formal_s']
        b0_formal=(b0.time_s>=formal_start)&(b0.time_s<formal_stop)
        p_formal=(ptime>=formal_start)&(ptime<formal_stop)
        excursions={s:float(np.rad2deg(np.max(so3.geodesic(b0.segment_quaternions[s][0],b0.segment_quaternions[s])))) for s in segs}
        formal_excursions={s:float(np.rad2deg(np.max(so3.geodesic(b0.segment_quaternions[s][b0_formal][0],
                                   b0.segment_quaternions[s][b0_formal])))) for s in segs}
        production_excursions={s:float(np.rad2deg(np.max(so3.geodesic(frames[0].segment_quaternions_W_S[s],
                                      np.stack([f.segment_quaternions_W_S[s] for f in frames]))))) for s in segs}
        production_q={s:np.stack([f.segment_quaternions_W_S[s] for f in frames]) for s in segs}
        formal_production_excursions={s:float(np.rad2deg(np.max(so3.geodesic(production_q[s][p_formal][0],
                                              production_q[s][p_formal])))) for s in segs}
        production_steps={s:float(np.rad2deg(np.max(so3.geodesic(
                            np.stack([f.segment_quaternions_W_S[s] for f in frames[:-1]]),
                            np.stack([f.segment_quaternions_W_S[s] for f in frames[1:]]))))) for s in segs}
        discontinuity={s:float(np.rad2deg(np.max(so3.geodesic(b0.segment_quaternions[s][:-1],b0.segment_quaternions[s][1:])))) for s in segs}
        aligned_b0_steps={s:float(np.rad2deg(np.max(so3.geodesic(b0.segment_quaternions[s][i0][:-1],
                                      b0.segment_quaternions[s][i0][1:])))) for s in segs}
        aligned_b1_steps={s:float(np.rad2deg(np.max(so3.geodesic(b1.segment_quaternions[s][i1][:-1],
                                      b1.segment_quaternions[s][i1][1:])))) for s in segs}
        ages=np.array([max(0.0,f.time_s-f.cutoff_time_s) for f in frames])
        latency={'definition':'algorithmic measurement age; excludes sensor-to-host transport','p50_s':float(np.percentile(ages,50)),
                 'p95_s':float(np.percentile(ages,95)),'max_s':float(np.max(ages)),
                 'estimator_runtime_s':estimator_runtime,'runtime_per_frame_s':estimator_runtime/max(len(frames),1),
                 'throughput_frames_per_s':len(frames)/max(estimator_runtime,1e-12)}
        summary={"schema":"biospur-phase3r-action-result-v1","action_id":action_id,"classification":self.rows[action_id].get('classification','DEVELOPMENT'),
          "imu_samples":audit.imu_samples,"uwb_numeric":0,"vqf_official_runtime_s":sum(x.runtime_s for x in vqf.values()),"vqf_nodes":len(vqf),
          "b0_frames":len(b0.time_s),"b1_frames":len(b1.time_s),"production_frames":len(frames),"scheduled_coverage":1.0 if frames else 0,
          "whole_body_availability":float(np.mean([f.whole_body_available for f in frames])) if frames else 0,
          "segment_availability":{s:float(np.mean([f.segment_quality[s].startswith('USABLE') for f in frames])) for s in segs},
          "joint_availability":{j.name:float(np.mean([f.joint_quality[j.name].startswith('USABLE') for f in frames])) for j in JOINTS},
          "angular_excursion_deg":excursions,"formal_angular_excursion_deg":formal_excursions,
          "production_angular_excursion_deg":production_excursions,
          "formal_production_angular_excursion_deg":formal_production_excursions,
          "largest_motion_segment":max(formal_excursions,key=formal_excursions.get),"maximum_B0_step_deg":discontinuity,
          "maximum_B0_aligned_50hz_step_deg":aligned_b0_steps,"maximum_B1_aligned_50hz_step_deg":aligned_b1_steps,
          "maximum_production_step_deg":production_steps,
          "b0_to_b1_median_deg":float(np.rad2deg(np.median(diff(b0.segment_quaternions,b1.segment_quaternions)))),
          "b1_to_production_median_deg":float(np.rad2deg(np.median([so3.geodesic(b1.segment_quaternions[s][i1[k]],frames[k].segment_quaternions_W_S[s]) for s in segs for k in range(len(frames))]))),
          "bone_length_max_variation":float(np.max(np.ptp(np.vstack([bone_lengths(f.normalized_joint_positions) for f in frames]),axis=0))),
          "factor_activation":est.activation_report(),"frontend":front_report,"cross_state_covariance_norm":est.cross_state_norm(),
          "weak_mode_27d":est.weak_mode_report(),"qmt_self_derived_priors_excluded":excluded,
          "production_initializer":"B0" if excluded else "B1","latency":latency,"vqf_manifest":vqf_manifest,
          "runtime_s":time.perf_counter()-started,"external_truth":False,"accuracy_claim":False}
        write_json(out/'SUMMARY.json',summary)
        if animate and frames:
            b0pos=tuple(b0.normalized_positions[i] for i in i0);b1pos=tuple(b1.normalized_positions[i] for i in i1);ppos=tuple(f.normalized_joint_positions for f in frames)
            statuses={'B0':tuple('VQF comparator' for _ in frames),'B1':tuple('qmt conditional' for _ in frames),
                      'P':tuple('usable' if f.whole_body_available else '/'.join(f.degraded_reasons) for f in frames)}
            summary['animation']=render_triptych(out/'B0_B1_P.gif',ptime,{'B0':b0pos,'B1':b1pos,'P':ppos},statuses,max_frames=120)
            write_json(out/'SUMMARY.json',summary)
        self.cache.pop(action_id,None)
        return summary
