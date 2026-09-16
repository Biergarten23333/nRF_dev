import dis,json,math,pickle,time
from dataclasses import replace
import numpy as np
import pytest

import biospur_fusion.c2_uwb_root_world.owner_bound_async_worker as owner_worker_module

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock,DirectPoseSnapshot,INCIDENT_SEGMENTS_BY_NODE,direct_shadow_evidence
from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
 A_WEIGHT_POLICY,A_SIGMA_POLICY,B_SIGMA_POLICY,AsyncOwnerWorker,BShadowGeometryOwner,BShadowSnapshotOwner,BoundGroupPacket,
 CausalRobustSharedRootOwner,DirectOwnerSequence,
 CausalRawRangeDiagnosticJournal,DIAGNOSTIC_HORIZON_S,DIAGNOSTIC_IMU_CAPACITY,
 DIAGNOSTIC_ASSEMBLY_PERIOD_S,DIAGNOSTIC_GROUP_PERIOD_S,DIAGNOSTIC_MAXIMUM_IMU_GAP_S,
 DIAGNOSTIC_NATIVE200_PERIOD_S,DIAGNOSTIC_REQUIRED_IMU_CAPACITY,DIAGNOSTIC_STARTUP_PERIOD_S,
 PublishedResult,U3SigmaOwner,U5BSigmaOwner,
 _validate_diagnostic_horizon,decode_group,decode_static_owner,encode_group,encode_static_owner)
from biospur_fusion.c2_uwb_root_world.tight_range import (ExternalRangeInformationWeights,PersistentRangeBiasTracker,
 RangeBiasPriorSnapshot,RawRangeUpdateConfig,linearize_raw_range_factors,prepare_raw_range_update,update_raw_ranges)
from biospur_fusion.c2_uwb_root_world.root_worker_event_codec import decode_event
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import (BoundGroupPacket as ReferencePacket,
 OwnerBoundCoordinator,PoseTagLinkOwner,RangeInformationOwner)
from biospur_fusion.c2_timing_contract import canonical_clock_global_ns
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import canonical_group_availability_time_s
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import group_epoch_times_ns
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import execute_offline_root_group
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel
from biospur_fusion.root_r3.estimator import propagate_inertial
from biospur_fusion.root_r3.models import ImuSample,RootState
from test_c2_root_worker_owner_wiring import fixture


def packets():
 owner,old=fixture();old_nodes=sorted(owner.clocks);new_nodes=tuple(INCIDENT_SEGMENTS_BY_NODE);rename=dict(zip(old_nodes,new_nodes))
 clocks={rename[k]:replace(v,node=rename[k]) for k,v in owner.clocks.items()};poses0=tuple(replace(x,node=rename[x.node]) for x in owner.pose_links)
 info=RangeInformationOwner(owner.range_information.nominal_sigma_m,owner.range_information.positive_nlos_cauchy_scale_m,
  {rename[k]:v for k,v in owner.range_information.weights_by_node.items()},owner.range_information.provenance)
 owner=replace(owner,root_config=replace(owner.root_config,fixed_lag_s=.10),clocks=clocks,pose_links=poses0,range_information=info,digest="")
 qualities=(25,40,55,70,85,100,65,35)
 rows1=tuple(replace(row,node=rename[row.node],quality=qualities) for row in old.rows);_,_,a1_raw=group_epoch_times_ns(rows1,clocks=owner.clocks);a1=canonical_clock_global_ns(a1_raw)
 a_sigma=U3SigmaOwner(.0564866166214546,.10,"SEALED_U3_LAYOUT_PLUS_FLOOR_FIXTURE")
 config=RawRangeUpdateConfig(nominal_sigma_m=owner.range_information.nominal_sigma_m,
  positive_nlos_cauchy_scale_m=owner.range_information.positive_nlos_cauchy_scale_m,
  uncertainty_provenance=owner.range_information.provenance)
 b_sigma=U5BSigmaOwner(config,owner.range_information.provenance)
 geometry=DisplayProxyGeometry(.55,.30,.40,{"upper_arm_left":.28,"forearm_left":.25,"upper_arm_right":.28,"forearm_right":.25,"thigh_left":.42,"shank_left":.40,"thigh_right":.42,"shank_right":.40})
 joints={"pelvis_center":np.array([0.,0.,0.]),"shoulder_mid":np.array([0.,0.,.55]),"shoulder_left":np.array([0.,.2,.55]),"shoulder_right":np.array([0.,-.2,.55]),"hip_left":np.array([0.,.15,0.]),"hip_right":np.array([0.,-.15,0.]),"elbow_left":np.array([.1,.42,.35]),"elbow_right":np.array([.1,-.42,.35]),"wrist_left":np.array([.2,.6,.2]),"wrist_right":np.array([.2,-.6,.2]),"knee_left":np.array([.02,.15,-.42]),"knee_right":np.array([.02,-.15,-.42]),"ankle_left":np.array([.05,.15,-.82]),"ankle_right":np.array([.05,-.15,-.82])}
 point_names=("pelvis_center","shoulder_mid","elbow_left","elbow_right","wrist_left","wrist_right","knee_left","knee_right","ankle_left","ankle_right")
 offsets={node:joints[point] for node,point in zip(new_nodes,point_names)};normals={node:np.array([1.,0.,0.]) for node in new_nodes}
 def shadow(rows,shift):
  js={k:v.copy() for k,v in joints.items()};js["wrist_left"]=js["wrist_left"]+np.array([0.,0.,shift]);snap=[]
  for row in rows:
   model=ClockModel(owner.clocks[row.node].boot_epoch,owner.clocks[row.node].a_ns_per_us,owner.clocks[row.node].b_ns,0.)
   query=min(model.seconds(row.strobe_us+.5*row.t_round_us[a])*1e9 for a in range(8));pose=int(query//5_000_000*5_000_000)
   snap.append(BShadowSnapshotOwner(row.node,"fixture",pose//5_000_000,pose,query,offsets,normals,js,"3"*64))
  return BShadowGeometryOwner(geometry,tuple(snap),"FROZEN_DISPLAY_PROXY_FIXTURE")
 first=BoundGroupPacket(owner.digest,RootWorkerEvent(10,a1*1e-9,"UWB",rows1),owner.pose_links,(),a_sigma,b_sigma,shadow(rows1,0.),availability_global_ns=a1)
 rows2=tuple(replace(row,sequence=2,sweep=2,strobe_us=row.strobe_us+120_048,frame_us=row.frame_us+120_048,
  ranges_mm=tuple(x+180 for x in row.ranges_mm)) for row in rows1)
 poses=[]
 for row in rows2:
  clock=owner.clocks[row.node];base=next(x for x in owner.pose_links if x.node==row.node and x.anchor==0)
  for anchor in range(8):
   query=clock.link_time_ns(event_boot_epoch=row.boot,strobe_us=row.strobe_us,t_round_us=row.t_round_us[anchor])
   pose_time=int(query//5_000_000*5_000_000);dt=(pose_time-base.pose_time_ns)*1e-9
   poses.append(PoseTagLinkOwner(row.node,anchor,query,pose_time,base.offset_world_m+dt*base.offset_velocity_world_mps,
    base.offset_velocity_world_mps,pose_time//5_000_000,18,"2"*64))
 _,_,a2_raw=group_epoch_times_ns(rows2,clocks=owner.clocks);a2=canonical_clock_global_ns(a2_raw)
 second=BoundGroupPacket(owner.digest,RootWorkerEvent(40,a2*1e-9,"UWB",rows2),tuple(poses),(),a_sigma,b_sigma,shadow(rows2,.2),availability_global_ns=a2)
 return owner,first,second


def _packet_shifted_to_availability(target_ns):
 owner,packet,_=packets();delta=target_ns-packet.availability_global_ns
 clocks={node:replace(clock,b_ns=clock.b_ns+delta) for node,clock in owner.clocks.items()}
 poses=tuple(replace(link,query_time_ns=link.query_time_ns+delta,pose_time_ns=link.pose_time_ns+delta) for link in packet.pose_links)
 initial=RootState(owner.initial_state.time_s+delta*1e-9,owner.initial_state.vector,owner.initial_state.covariance)
 owner=replace(owner,initial_state=initial,clocks=clocks,pose_links=poses,digest="")
 shadows=tuple(replace(value,pose_global_ns=value.pose_global_ns+delta,query_global_ns=value.query_global_ns+delta) for value in packet.b_shadow_owner.snapshots)
 shadow=replace(packet.b_shadow_owner,snapshots=shadows,digest="")
 event=replace(packet.event,availability_time_s=target_ns*1e-9)
 return owner,BoundGroupPacket(owner.digest,event,poses,(),packet.a_sigma_owner,packet.b_sigma_owner,shadow,availability_global_ns=target_ns)


def sequence():
 owner,first,second=packets();items=[];seq=0
 for value in np.arange(.055,.221,.005):
  items.append((value,RootWorkerEvent(seq,float(value),"IMU",ImuSample(float(value),float(value),np.array([0,0,9.80665]),np.eye(3),seq)))) ;seq+=1
 items.extend(((first.event.availability_time_s,first),(second.event.availability_time_s,second)))
 items.sort(key=lambda x:(x[0],0 if isinstance(x[1],RootWorkerEvent) else 1));return owner,[x[1] for x in items]


REFERENCE_GROUP_PERIOD_S = 0.120048
REFERENCE_MAXIMUM_IMU_GAP_S = 0.005005
REFERENCE_DIAGNOSTIC_HORIZON_S = (
 REFERENCE_GROUP_PERIOD_S + REFERENCE_GROUP_PERIOD_S + REFERENCE_MAXIMUM_IMU_GAP_S)
REFERENCE_DIAGNOSTIC_IMU_CAPACITY = 64


def _validate_reference_diagnostic_horizon(committed_time_s,availability_time_s):
 if (not math.isfinite(committed_time_s) or not math.isfinite(availability_time_s)
     or availability_time_s<committed_time_s):
  raise ValueError("reference diagnostic horizon domain invalid")
 if availability_time_s-committed_time_s>REFERENCE_DIAGNOSTIC_HORIZON_S:
  raise ValueError("reference diagnostic horizon exceeded")


class PublicReference:
 def __init__(self,owner):
  self.owner=owner;self.a=owner.make_root();self.state=RootState(owner.initial_state.time_s,owner.initial_state.vector,owner.initial_state.covariance)
  self.force=np.array([0.,0.,9.80665]);self.rotation=np.eye(3);self.buffer=[];self.last_arrival=owner.initial_state.time_s;self.trackers={x:PersistentRangeBiasTracker() for x in owner.clocks}
 def process(self,item):
  if isinstance(item,RootWorkerEvent):
   if len(self.buffer)>=REFERENCE_DIAGNOSTIC_IMU_CAPACITY:raise OverflowError("reference diagnostic IMU buffer full")
   assert item.availability_time_s>self.last_arrival and self.a.add_imu(item.payload);self.buffer.append(item);self.last_arrival=item.availability_time_s
   return PublishedResult("IMU",item.sequence,None,None,self.a.current_state.vector,self.a.current_state.covariance)
  wm={x.node:x for x in item.information_weights};info=RangeInformationOwner(self.owner.range_information.nominal_sigma_m,
   self.owner.range_information.positive_nlos_cauchy_scale_m,{x:np.ones(8) for x in self.owner.clocks},A_WEIGHT_POLICY)
  dynamic=replace(self.owner,pose_links=item.pose_links,range_information=info,digest="")
  poses={(x.node,x.anchor):x for x in item.pose_links};factors=[];decisions=[]
  rows=[]
  for row in item.event.payload:
   clock=dynamic.clocks[row.node];model=ClockModel(clock.boot_epoch,clock.a_ns_per_us,clock.b_ns,0.)
   slots=tuple(a for a in range(8) if row.valid_mask&(1<<a) and int(row.anchor_ids[a])==a
    and 0<row.ranges_mm[a]<0xffff and np.isfinite(row.t_round_us[a]))
   epochs=np.asarray([model.seconds(row.strobe_us+.5*row.t_round_us[a]) for a in slots])
   if len(epochs)<4 or not np.isfinite(epochs).all():raise ValueError("reference diagnostic link epochs invalid")
   rows.append((float(np.median(epochs)),row,slots))
  ordered=sorted(rows,key=lambda x:(x[0],x[1].node));times=[x[0] for x in ordered]
  if item.event.availability_time_s<self.last_arrival:raise ValueError("late reference diagnostic group")
  if times[0]<=self.state.time_s or times[-1]>item.event.availability_time_s:raise ValueError("reference diagnostic measurement time invalid")
  available=[x.payload.measurement_time_s for x in self.buffer if x.availability_time_s<=item.event.availability_time_s]
  checkpoints=[self.state.time_s,*available,item.event.availability_time_s]
  if any(b-a>DIAGNOSTIC_MAXIMUM_IMU_GAP_S+1e-12 for a,b in zip(checkpoints,checkpoints[1:])):raise ValueError("reference diagnostic IMU gap")
  _validate_reference_diagnostic_horizon(self.state.time_s,item.event.availability_time_s)
  ar=execute_offline_root_group(root=self.a,rows=item.event.payload,clocks=dynamic.clocks,strict_floor_offset=dynamic.pose,
   anchors_m=dynamic.anchors_m,anchor_delay_m=dynamic.anchor_delay_m,tag_delay_m=dynamic.tag_delay_m,
   sigma_for_quality=item.a_sigma_owner.sigma,nominal_envelope=dynamic.nominal_envelope,
   trust_config=dynamic.trust_config,source_sequence=item.event.sequence)
  node_states=[]
  for reference,row,slots in ordered:
   for event in self.buffer:
    if self.state.time_s<event.payload.measurement_time_s<=reference:
     self.state,_=propagate_inertial(self.state,event.payload.measurement_time_s,self.force,self.rotation,dynamic.root_config)
     self.force=event.payload.specific_force_sensor_mps2.copy();self.rotation=event.payload.rotation_world_from_sensor.copy()
   self.state,_=propagate_inertial(self.state,reference,self.force,self.rotation,dynamic.root_config);pre=self.state;tracker=self.trackers[row.node]
   if item.b_shadow_owner is not None:
    shadow=next(x for x in item.b_shadow_owner.snapshots if x.node==row.node);root=pre.position_m+(shadow.query_global_ns*1e-9-reference)*pre.velocity_mps;snapshot=shadow.snapshot(root)
    vector=np.ones(8)
    for anchor in slots:vector[anchor]=direct_shadow_evidence(node=row.node,anchor_position_world_m=dynamic.anchors_m[anchor],snapshot=snapshot,geometry=item.b_shadow_owner.geometry).b_combined_weight
    wm[row.node]=ExternalRangeInformationWeights(row.node,shadow.pose_global_ns*1e-9,vector,dynamic.range_information.provenance)
   prior=tracker.prior_snapshot(row.node,snapshot_time_s=wm[row.node].evidence_time_s);prior=RangeBiasPriorSnapshot(row.node,prior.snapshot_time_s,
    prior.mean_m+dynamic.anchor_delay_m+dynamic.tag_delay_m,prior.variance_m2,prior.last_accepted_time_s)
   geom=min((poses[(row.node,anchor)] for anchor in slots),key=lambda x:x.query_time_ns);clock=ClockModel(dynamic.clocks[row.node].boot_epoch,dynamic.clocks[row.node].a_ns_per_us,dynamic.clocks[row.node].b_ns,0.)
   cfg=item.b_sigma_owner.config
   f=linearize_raw_range_factors(self.state,row,anchors_m=dynamic.anchors_m,clock=clock,bias_prior=prior,information_weights=wm[row.node],
    tag_offset_world_m=geom.offset_world_m,config=cfg)
   updated,d=update_raw_ranges(self.state,row,anchors_m=dynamic.anchors_m,clock=clock,bias_prior=prior,information_weights=wm[row.node],
    tag_offset_world_m=geom.offset_world_m,config=cfg)
   self.state=updated
   if d.accepted:tracker.update(row.node,d)
   factors.append(f);decisions.append((row.node,d.accepted,d.reason));node_states.append((row.node,pre.vector,pre.covariance,updated.vector,updated.covariance))
  self.buffer=[x for x in self.buffer if x.payload.measurement_time_s>self.state.time_s]
  publication=RootState(self.state.time_s,self.state.vector,self.state.covariance);force=self.force.copy();rotation=self.rotation.copy()
  for event in self.buffer:
   if publication.time_s<event.payload.measurement_time_s<=item.event.availability_time_s:
    publication,_=propagate_inertial(publication,event.payload.measurement_time_s,force,rotation,dynamic.root_config)
    force=event.payload.specific_force_sensor_mps2.copy();rotation=event.payload.rotation_world_from_sensor.copy()
  publication,_=propagate_inertial(publication,item.event.availability_time_s,force,rotation,dynamic.root_config)
  latest=max(float(np.max(x.link_epochs_s)) for x in factors);query=float(np.nextafter(latest,np.inf));bias=[]
  for node in sorted(self.trackers):
   p=self.trackers[node].prior_snapshot(node,snapshot_time_s=query);bias.append((node,p.mean_m,p.variance_m2,p.last_accepted_time_s))
  identities=tuple((x.node,x.anchor) for x in __import__('biospur_fusion.c2_uwb_root_world.offline_unified_wiring',fromlist=['build_causal_links']).build_causal_links(
   item.event.payload,clocks=dynamic.clocks,strict_floor_offset=dynamic.pose,anchor_delay_m=dynamic.anchor_delay_m,
   tag_delay_m=dynamic.tag_delay_m,sigma_for_quality=dynamic.range_information.sigma)[0])
  return PublishedResult("UWB",item.event.sequence,ar.transaction.decision.reason.value,ar.transaction.root_decision_reason,
   self.a.current_state.vector,self.a.current_state.covariance,
   tuple(x.state_jacobian for x in factors),tuple(x.sensor_r_m2 for x in factors),tuple(x.r_prior_m2 for x in factors),tuple(x.s_prior_m2 for x in factors),
   tuple(x.prior_nis for x in factors),tuple(x.rank for x in factors),tuple(x.condition for x in factors),identities,len(ar.link_audit),ar.transaction_calls,
   diagnostic_state=publication.vector,diagnostic_covariance=publication.covariance,diagnostic_committed_state=self.state.vector,
   diagnostic_committed_covariance=self.state.covariance,diagnostic_committed_time_s=self.state.time_s,diagnostic_decisions=tuple(decisions),
   diagnostic_weights=tuple((row.node,wm[row.node].weights) for _,row,_ in ordered),bias_snapshots=tuple(bias),diagnostic_node_states=tuple(node_states),
   diagnostic_predicted=tuple(x.predicted_ranges_m for x in factors),authoritative_weights=tuple((node,anchor,1.) for node,anchor in identities))


def assert_same(a,b):
 assert (a.kind,a.sequence,a.decision,a.root_reason,a.link_identities,a.link_count,a.guard_calls)==(b.kind,b.sequence,b.decision,b.root_reason,b.link_identities,b.link_count,b.guard_calls)
 np.testing.assert_allclose(a.state,b.state,rtol=0,atol=1e-12);np.testing.assert_allclose(a.covariance,b.covariance,rtol=0,atol=1e-12)
 for field in ("h","sensor_r","total_r","s"):
  for x,y in zip(getattr(a,field),getattr(b,field)):np.testing.assert_allclose(x,y,rtol=0,atol=1e-12)
 np.testing.assert_allclose(a.nis,b.nis,rtol=0,atol=1e-12);np.testing.assert_allclose(a.condition,b.condition,rtol=0,atol=1e-12)
 for x,y in zip(a.diagnostic_predicted,b.diagnostic_predicted):np.testing.assert_allclose(x,y,rtol=0,atol=1e-12)
 assert a.authoritative_weights==b.authoritative_weights
 if a.diagnostic_state is not None:
  np.testing.assert_allclose(a.diagnostic_state,b.diagnostic_state,rtol=0,atol=1e-12);np.testing.assert_allclose(a.diagnostic_covariance,b.diagnostic_covariance,rtol=0,atol=1e-12)
  np.testing.assert_allclose(a.diagnostic_committed_state,b.diagnostic_committed_state,rtol=0,atol=1e-12);np.testing.assert_allclose(a.diagnostic_committed_covariance,b.diagnostic_committed_covariance,rtol=0,atol=1e-12)
  assert a.diagnostic_committed_time_s==b.diagnostic_committed_time_s
 assert a.diagnostic_decisions==b.diagnostic_decisions
 for (_,x),(_,y) in zip(a.diagnostic_weights,b.diagnostic_weights):np.testing.assert_allclose(x,y,rtol=0,atol=1e-12)
 for (nx,mx,vx,tx),(ny,my,vy,ty) in zip(a.bias_snapshots,b.bias_snapshots):
  assert nx==ny;np.testing.assert_allclose(mx,my,rtol=0,atol=1e-12);np.testing.assert_allclose(vx,vy,rtol=0,atol=1e-12);np.testing.assert_allclose(tx,ty,rtol=0,atol=1e-12,equal_nan=True)
 for left,right in zip(a.diagnostic_node_states,b.diagnostic_node_states):
  assert left[0]==right[0]
  for x,y in zip(left[1:],right[1:]):np.testing.assert_allclose(x,y,rtol=0,atol=1e-12)


def test_prepared_raw_range_update_reuses_owned_initial_factors_exactly():
 owner,first,_=packets();row=first.event.payload[0];clock_owner=owner.clocks[row.node]
 clock=ClockModel(clock_owner.boot_epoch,clock_owner.a_ns_per_us,clock_owner.b_ns,0.)
 epochs=[clock.seconds(row.strobe_us+.5*row.t_round_us[a]) for a in range(8)]
 state=RootState(float(np.median(epochs)),owner.initial_state.vector,owner.initial_state.covariance)
 weights=ExternalRangeInformationWeights(row.node,min(epochs)-.001,np.ones(8),owner.range_information.provenance)
 config=first.b_sigma_owner.config;offset=next(x.offset_world_m for x in first.pose_links if x.node==row.node)
 prepared=prepare_raw_range_update(state,row,anchors_m=owner.anchors_m,clock=clock,
  information_weights=weights,tag_offset_world_m=offset,config=config)
 direct=update_raw_ranges(state,row,anchors_m=owner.anchors_m,clock=clock,
  information_weights=weights,tag_offset_world_m=offset,config=config)
 reused=update_raw_ranges(state,row,anchors_m=owner.anchors_m,clock=clock,
  information_weights=weights,tag_offset_world_m=offset,config=config,prepared=prepared)
 np.testing.assert_allclose(reused[0].vector,direct[0].vector,rtol=0,atol=0)
 np.testing.assert_allclose(reused[0].covariance,direct[0].covariance,rtol=0,atol=0)
 assert reused[1].accepted==direct[1].accepted and reused[1].reason==direct[1].reason
 with pytest.raises(ValueError,match="prepared raw range update owner mismatch"):
  update_raw_ranges(state,row,anchors_m=owner.anchors_m.copy(),clock=clock,
   information_weights=weights,tag_offset_world_m=offset,config=config,prepared=prepared)


@pytest.mark.parametrize("mode",("forged","stale","wrong_packet","wrong_static"))
def test_prepared_dynamic_owner_token_rejects_mismatch_and_forgery_before_root_mutation(mode):
 import biospur_fusion.c2_uwb_root_world.owner_bound_async_worker as worker
 owner,first,second=packets();packet=first;prepared=worker._prepare_dynamic_owner(owner,first)
 if mode=="forged":prepared=replace(prepared,key=object())
 elif mode=="stale":prepared=replace(prepared,packet_digest="0"*64)
 elif mode=="wrong_packet":packet=second
 else:owner=replace(owner,tag_delay_m=owner.tag_delay_m+.001,digest="")
 root=owner.make_root();before=root.publication_token()
 with pytest.raises(ValueError,match="prepared dynamic owner mismatch"):
  worker._execute_group(owner,root,packet,prepared)
 after=root.publication_token()
 assert (after.revision,after.digest)==(before.revision,before.digest)
 np.testing.assert_array_equal(after.state.vector,before.state.vector)
 np.testing.assert_array_equal(after.state.covariance,before.state.covariance)


def test_static_and_group_codecs_roundtrip_exact_and_u7d_rejects_schema():
 owner,first,_=packets();static=encode_static_owner(owner);assert encode_static_owner(decode_static_owner(static))==static
 group=encode_group(first);decoded=decode_group(group);assert encode_group(decoded)==group
 assert decoded.availability_global_ns==first.availability_global_ns
 with pytest.raises(ValueError):decode_event(static)
 for blob in (static,group):
  doc=json.loads(blob);doc["extra"]=1
  with pytest.raises(ValueError):(decode_static_owner if blob is static else decode_group)(json.dumps(doc).encode())
 doc=json.loads(group);doc["sha256"]="0"*64
 with pytest.raises(ValueError):decode_group(json.dumps(doc).encode())


def test_packet_canonical_availability_mismatch_and_codec_tamper_reject():
 _,packet,_=packets()
 with pytest.raises(ValueError,match="float/ns mismatch"):
  replace(packet,availability_global_ns=packet.availability_global_ns+1,digest="")
 with pytest.raises(ValueError,match="float/ns mismatch"):
  replace(packet,event=replace(packet.event,availability_time_s=np.nextafter(packet.event.availability_time_s,np.inf)),digest="")
 tampered=replace(packet)
 object.__setattr__(tampered,"availability_global_ns",packet.availability_global_ns+1)
 with pytest.raises(ValueError,match="float/ns mismatch"):encode_group(tampered)
 doc=json.loads(encode_group(packet));doc["availability_global_ns"]-=1
 core={key:value for key,value in doc.items() if key!="sha256"}
 doc["sha256"]=__import__("hashlib").sha256(owner_worker_module._canonical(core)).hexdigest()
 with pytest.raises(ValueError,match="float/ns mismatch"):decode_group(json.dumps(doc,separators=(",",":"),sort_keys=True).encode())


def test_dual_sigma_owners_are_exact_separate_and_quality_strict():
 owner,first,_=packets();a=first.a_sigma_owner;b=first.b_sigma_owner
 for quality in (25,40,55,70,85,100):
  assert a.sigma(quality)==np.sqrt(a.layout_sigma_m**2*(100/quality)+a.floor_sigma_m**2)
  assert b.sigma(quality)==b.config.nominal_sigma_m*np.sqrt(100/quality)
 assert a.policy_id==A_SIGMA_POLICY and b.policy_id==B_SIGMA_POLICY
 assert a.sigma(25)/np.sqrt(4)!=a.sigma(100)
 for quality in (True,0,-1,2.5,"25"):
  with pytest.raises(ValueError):a.sigma(quality)
  with pytest.raises(ValueError):b.sigma(quality)
 with pytest.raises(ValueError):U3SigmaOwner(.05,.1,"p",policy_id=B_SIGMA_POLICY)
 with pytest.raises(ValueError):U5BSigmaOwner(b.config,"p",policy_id=A_SIGMA_POLICY)


@pytest.mark.parametrize("field",("a_policy","a_parameter","a_digest","b_policy","b_parameter","b_digest","missing"))
def test_sigma_owner_transport_tamper_rejects_before_both_channels(field):
 owner,first,_=packets();from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import DirectOwnerSequence
 engine=DirectOwnerSequence(owner);before=engine.root.publication_token();diagnostic=engine.diagnostic.state.vector.tobytes()
 bias=tuple(x.snapshot() for x in engine.diagnostic.trackers.values());blob=json.loads(encode_group(first))
 if field=="a_policy":blob["a_sigma_owner"]["policy_id"]=B_SIGMA_POLICY
 elif field=="a_parameter":blob["a_sigma_owner"]["layout_sigma_m"]*=2
 elif field=="a_digest":blob["a_sigma_owner"]["digest"]="0"*64
 elif field=="b_policy":blob["b_sigma_owner"]["policy_id"]=A_SIGMA_POLICY
 elif field=="b_parameter":blob["b_sigma_owner"]["config"]["nominal_sigma_m"]*=2
 elif field=="b_digest":blob["b_sigma_owner"]["digest"]="0"*64
 else:del blob["a_sigma_owner"]
 with pytest.raises(ValueError):decode_group(json.dumps(blob).encode())
 after=engine.root.publication_token();assert before.digest==after.digest and diagnostic==engine.diagnostic.state.vector.tobytes()
 assert bias==tuple(x.snapshot() for x in engine.diagnostic.trackers.values())


def test_packet_missing_weight_and_bias_state_extension_reject():
 owner,first,_=packets()
 with pytest.raises(ValueError,match="reconstruction owner"):
  BoundGroupPacket(owner.digest,first.event,first.pose_links,first.information_weights[:-1],first.a_sigma_owner,first.b_sigma_owner,availability_global_ns=first.availability_global_ns)
 with pytest.raises(ValueError,match="cannot be packet inputs"):
  replace(first,information_weights=(ExternalRangeInformationWeights(first.event.payload[0].node,.01,np.ones(8),owner.range_information.provenance),),digest="")
 doc=json.loads(encode_group(first));doc["bias_state"]={}
 with pytest.raises(ValueError,match="not canonical"):decode_group(json.dumps(doc).encode())
 for mode in ("null","missing"):
  doc=json.loads(encode_group(first))
  if mode=="null":doc["b_shadow_owner"]=None
  else:del doc["b_shadow_owner"]
  with pytest.raises((ValueError,TypeError)):decode_group(json.dumps(doc).encode())


def test_two_group_persistent_async_matches_direct_after_every_event():
 owner,items=sequence();direct=DirectOwnerSequence(owner);expected=[direct.process(x) for x in items]
 assert owner.inertial is True and owner.root_config.fixed_lag_s==.10
 worker=AsyncOwnerWorker(owner)
 for x in items:worker.submit(x)
 actual,final=worker.close_and_collect(len(items))
 assert len(actual)==len(expected)
 for a,b in zip(actual,expected):assert_same(a,b)
 groups=[x for x in actual if x.kind=="UWB"]
 assert len(groups)==2 and all(x.link_count==80 and x.guard_calls==1 for x in groups)
 assert all(len(x.link_identities)==80 and len(set(x.link_identities))==80 for x in groups)
 assert all(any(value<1.0 for _,_,value in x.authoritative_weights) for x in groups)
 assert not np.array_equal(groups[0].diagnostic_weights[0][1],groups[1].diagnostic_weights[0][1])
 assert any(np.any(value[1]>0) and np.all(np.isfinite(value[3])) for value in groups[1].bias_snapshots)
 assert final["sentinel"] and final["count"]==len(items) and final["qsize"]==0 and not final["alive"]


def test_robust_weights_change_the_accepted_authoritative_root_not_only_diagnostics():
 owner,items=sequence();robust=DirectOwnerSequence(owner);unit=PublicReference(owner)
 robust_result=unit_result=None
 for item in items:
  robust_result=robust.process(item);unit_result=unit.process(item)
  if robust_result.kind=="UWB":break
 assert robust_result.decision=="ACCEPT_NOMINAL" and unit_result.decision=="ACCEPT_NOMINAL"
 assert any(weight<1.0 for _,_,weight in robust_result.authoritative_weights)
 assert np.linalg.norm(robust_result.state-unit_result.state)>1e-7
 assert robust_result.authoritative_mode=="FULL_NODE_CONSTRAINED_CONSENSUS"
 assert len(robust_result.authoritative_nodes)==10


def test_robust_authority_supports_single_node_fallback_and_ten_of_ten_direct_case():
 owner,items=sequence();full=DirectOwnerSequence(owner);single=DirectOwnerSequence(owner)
 full_result=single_result=None
 for item in items:
  full_result=full.process(item)
  if isinstance(item,BoundGroupPacket):
   rows=list(item.event.payload)
   for index in range(1,len(rows)):rows[index]=replace(rows[index],valid_mask=0x07)
   item=replace(item,event=replace(item.event,payload=tuple(rows)),digest="")
  single_result=single.process(item)
  if full_result.kind=="UWB":break
 assert full_result.authoritative_mode=="FULL_NODE_CONSTRAINED_CONSENSUS"
 assert len(full_result.authoritative_nodes)==10 and len(full_result.link_identities)==80
 assert single_result.authoritative_mode=="SINGLE_NODE_ROOT_TRANSLATION"
 assert len(single_result.authoritative_nodes)==1 and len(single_result.link_identities)==8
 assert single_result.decision=="ACCEPT_NOMINAL"


def test_authoritative_robust_uncertainty_is_published_without_relabeling_prior_uncertainty():
 import biospur_fusion.c2_uwb_root_world.owner_bound_async_worker as module
 owner,items=sequence();engine=DirectOwnerSequence(owner)
 for item in items:
  if isinstance(item,BoundGroupPacket):
   prepared=module._prepare_dynamic_owner(owner,item)
   plan=engine.robust.prepare(owner,engine.root,item,prepared)
   result=engine.process(item);break
  engine.process(item)
 assert len(result.effective_r)==len(result.effective_s)==len(result.robust_nis)==10
 assert len(result.external_information_weights)==10
 assert len(result.robust_influence_weights)==len(result.irls_information_weights)==10
 assert result.cross_covariance_status==tuple("UNAVAILABLE_NOT_PROPAGATED" for _ in range(10))
 for index,factor in enumerate(plan.factors):
  expected_r=np.diag(1./factor.irls_information_weights)
  expected_s=factor.s_prior_m2-factor.r_prior_m2+expected_r
  expected_nis=float(factor.innovations_m@np.linalg.solve(expected_s,factor.innovations_m))
  np.testing.assert_array_equal(result.sensor_r[index],factor.sensor_r_m2)
  np.testing.assert_array_equal(result.total_r[index],factor.r_prior_m2)
  np.testing.assert_array_equal(result.s[index],factor.s_prior_m2)
  assert result.nis[index]==factor.prior_nis
  np.testing.assert_array_equal(result.effective_r[index],expected_r)
  np.testing.assert_array_equal(result.effective_s[index],expected_s)
  assert result.robust_nis[index]==expected_nis
  node,anchors,robust=result.robust_influence_weights[index]
  assert node==plan.factor_nodes[index] and anchors==factor.anchors
  np.testing.assert_array_equal(robust,factor.robust_weights)
  node,anchors,irls=result.irls_information_weights[index]
  assert node==plan.factor_nodes[index] and anchors==factor.anchors
  np.testing.assert_array_equal(irls,factor.irls_information_weights)
 assert any(not np.array_equal(prior,effective) for prior,effective in zip(result.total_r,result.effective_r))
 assert any(prior!=robust for prior,robust in zip(result.nis,result.robust_nis))
 assert all(not value.flags.writeable for value in (*result.effective_r,*result.effective_s))


def _four_of_ten_packet(packet,excluded_range_delta_mm=0):
 rows=list(packet.event.payload)
 for index in range(4,len(rows)):
  ranges=tuple(int(value)+excluded_range_delta_mm for value in rows[index].ranges_mm)
  rows[index]=replace(rows[index],valid_mask=0x07,ranges_mm=ranges)
 return replace(packet,event=replace(packet.event,payload=tuple(rows)),digest="")


def test_four_of_ten_partial_transaction_uses_only_trusted_contributors_for_root_and_bias(monkeypatch):
 owner,items=sequence();left=DirectOwnerSequence(owner);right=DirectOwnerSequence(owner)
 applied=[];real_apply=left.robust._apply_prevalidated_commit
 def counted_apply(ticket):applied.append(ticket);real_apply(ticket)
 monkeypatch.setattr(left.robust,"_apply_prevalidated_commit",counted_apply)
 left_result=right_result=None
 for item in items:
  if isinstance(item,BoundGroupPacket):
   before_revision=left.root.publication_token().revision
   left_result=left.process(_four_of_ten_packet(item))
   right_result=right.process(_four_of_ten_packet(item,20_000))
   break
  left.process(item);right.process(item)
 trusted=set(left_result.authoritative_nodes)
 assert left_result.authoritative_mode=="PARTIAL_NODE_FK_PROPAGATION"
 assert len(trusted)==4 and trusted==set(right_result.authoritative_nodes)
 assert len(left_result.link_identities)==32 and left_result.guard_calls==1
 assert left.root.publication_token().revision==before_revision+1
 assert left.robust.revision==1 and len(applied)==1
 np.testing.assert_array_equal(left_result.state,right_result.state)
 np.testing.assert_array_equal(left_result.covariance,right_result.covariance)
 for left_row,right_row in zip(left_result.bias_snapshots,right_result.bias_snapshots):
  assert left_row[0]==right_row[0]
  for left_value,right_value in zip(left_row[1:],right_row[1:]):
   np.testing.assert_array_equal(left_value,right_value)
  if left_row[0] not in trusted:
   np.testing.assert_array_equal(left_row[1],np.zeros(8));assert np.isnan(left_row[3]).all()
 assert any(node in trusted and np.any(mean>0) for node,mean,_,_ in left_result.bias_snapshots)


def _owned_transaction_bytes(engine,packet):
 root_state={key:value for key,value in engine.root.__dict__.items() if key!="_apply_prevalidated_position"}
 journal=engine.diagnostic
 return pickle.dumps((root_state,engine.robust.snapshot(),journal.state,journal.force,journal.rotation,
  journal.buffer,journal.last_arrival,journal.trackers,packet.event.activity,packet.event.consensus,packet.event.contact),protocol=5)


def test_typed_transport_hostile_inputs_reject_before_any_owner_mutation():
 import biospur_fusion.c2_uwb_root_world.owner_bound_async_worker as module
 owner,items=sequence();packet=next(x for x in items if isinstance(x,BoundGroupPacket));imu=next(x for x in items if isinstance(x,RootWorkerEvent))
 engine=DirectOwnerSequence(owner);before=_owned_transaction_bytes(engine,packet)
 imu_blob=module.encode_imu(imu);group_blob=encode_group(packet)
 hostile=[]
 hostile.append((module.TRANSPORT_GROUP,imu_blob,"mismatch"))
 hostile.append((module.TRANSPORT_IMU,json.dumps(json.loads(imu_blob)).encode(),"not canonical"))
 outer=json.loads(group_blob);outer["sha256"]="0"*64
 hostile.append((module.TRANSPORT_GROUP,module._canonical(outer),"transport digest"))
 packet_digest=json.loads(group_blob);packet_digest["packet_digest"]="0"*64
 core={key:value for key,value in packet_digest.items() if key!="sha256"}
 packet_digest["sha256"]=__import__("hashlib").sha256(module._canonical(core)).hexdigest()
 hostile.append((module.TRANSPORT_GROUP,module._canonical(packet_digest),"bound group digest"))
 for discriminator,blob,message in hostile:
  with pytest.raises(ValueError,match=message):module._decode_and_process(engine,discriminator,blob)
  assert _owned_transaction_bytes(engine,packet)==before


def test_assignment_only_sidecar_rolls_back_when_root_apply_fails_after_sidecar_apply(monkeypatch):
 import biospur_fusion.c2_uwb_root_world.owner_bound_async_worker as module
 owner,items=sequence();engine=DirectOwnerSequence(owner)
 for item in items:
  if isinstance(item,BoundGroupPacket):packet=item;break
  engine.process(item)
 applied=[];real_apply=engine.robust._apply_prevalidated_commit
 def record_apply(ticket):real_apply(ticket);applied.append(ticket)
 monkeypatch.setattr(engine.robust,"_apply_prevalidated_commit",record_apply)
 def fail_root_apply(self,bundle):raise RuntimeError("INJECTED_ROOT_APPLY_FAILURE")
 monkeypatch.setattr(type(engine.root),"_apply_prevalidated_position",fail_root_apply)
 before=_owned_transaction_bytes(engine,packet)
 with pytest.raises(RuntimeError,match="INJECTED_ROOT_APPLY_FAILURE"):engine.process(packet)
 assert len(applied)==1
 assert _owned_transaction_bytes(engine,packet)==before
 for method in (module.CausalRobustSharedRootOwner._apply_prevalidated_commit,
                module.CausalRobustSharedRootOwner._rollback_prevalidated_commit):
  instructions=tuple(dis.get_instructions(method))
  assert [value.argval for value in instructions if value.opname=="STORE_ATTR"]==["trackers","revision"]
 assert not any(value.opname.startswith(("CALL","BUILD","MAKE","LIST","DICT","SET")) for value in instructions)


def test_rollback_ticket_exists_before_sidecar_apply_so_apply_then_throw_cannot_half_commit(monkeypatch):
 owner,items=sequence();engine=DirectOwnerSequence(owner)
 for item in items:
  if isinstance(item,BoundGroupPacket):packet=item;break
  engine.process(item)
 before=_owned_transaction_bytes(engine,packet);real_apply=engine.robust._apply_prevalidated_commit
 def apply_then_throw(ticket):
  real_apply(ticket)
  raise RuntimeError("INJECTED_AFTER_SIDECAR_ASSIGNMENTS")
 monkeypatch.setattr(engine.robust,"_apply_prevalidated_commit",apply_then_throw)
 with pytest.raises(RuntimeError,match="INJECTED_AFTER_SIDECAR_ASSIGNMENTS"):engine.process(packet)
 assert _owned_transaction_bytes(engine,packet)==before


def test_impossible_transition_rejection_preserves_root_bias_and_legacy_journal_atomically(monkeypatch):
 from biospur_fusion.c2_uwb_root_world.causal_update_guard import TransitionDecision,TransitionDisposition,TransitionReason
 import biospur_fusion.c2_uwb_root_world.owner_bound_async_worker as module
 owner,items=sequence();engine=DirectOwnerSequence(owner)
 def reject(proposal,**kwargs):
  return TransitionDecision(TransitionDisposition.REJECT_USE_IMU_PREDICTION,
   TransitionReason.REJECT_UNREACHABLE_TRANSITION,("fixture impossible jump",),proposal.imu_prediction,None,{})
 monkeypatch.setattr(module.tx,"evaluate_candidate_transition",reject)
 for item in items:
  if isinstance(item,BoundGroupPacket):
   before_root=engine.root.publication_token();before_bias=engine.robust.snapshot()
   before_journal=(engine.diagnostic.state.vector.tobytes(),engine.diagnostic.state.covariance.tobytes(),tuple(engine.diagnostic.buffer))
   result=engine.process(item);break
  engine.process(item)
 after_root=engine.root.publication_token()
 assert result.decision=="REJECT_UNREACHABLE_TRANSITION" and result.guard_calls==1
 assert after_root.revision==before_root.revision+1
 np.testing.assert_array_equal(after_root.state.vector,before_root.state.vector)
 np.testing.assert_array_equal(after_root.state.covariance,before_root.state.covariance)
 assert engine.robust.snapshot()==before_bias
 assert (engine.diagnostic.state.vector.tobytes(),engine.diagnostic.state.covariance.tobytes(),tuple(engine.diagnostic.buffer))==before_journal
 assert all(np.array_equal(mean,np.zeros(8)) and np.isnan(last).all() for _,mean,_,last in result.bias_snapshots)


def test_injected_robust_commit_prevalidation_failure_occurs_before_root_or_bias_mutation(monkeypatch):
 owner,items=sequence();engine=DirectOwnerSequence(owner)
 for item in items:
  if isinstance(item,BoundGroupPacket):
   before_root=engine.root.publication_token();before_bias=engine.robust.snapshot()
   def fail(*args,**kwargs):raise RuntimeError("INJECTED_ROBUST_COMMIT_FAILURE")
   monkeypatch.setattr(engine.robust,"prevalidate_commit",fail)
   with pytest.raises(RuntimeError,match="INJECTED_ROBUST_COMMIT_FAILURE"):engine.process(item)
   break
  engine.process(item)
 after_root=engine.root.publication_token()
 assert (after_root.revision,after_root.digest)==(before_root.revision,before_root.digest)
 assert engine.robust.snapshot()==before_bias


@pytest.mark.parametrize("field",("owner_revision","owner_digest","root_revision","packet_digest"))
def test_robust_commit_ticket_rejects_every_stale_identity_before_mutation(field):
 import biospur_fusion.c2_uwb_root_world.owner_bound_async_worker as module
 owner,items=sequence();engine=DirectOwnerSequence(owner);packet=None
 for item in items:
  if isinstance(item,BoundGroupPacket):packet=item;break
  engine.process(item)
 prepared=module._prepare_dynamic_owner(owner,packet);plan=engine.robust.prepare(owner,engine.root,packet,prepared)
 if field=="owner_revision":plan=replace(plan,owner_revision=plan.owner_revision+1)
 elif field=="owner_digest":plan=replace(plan,owner_digest="0"*64)
 elif field=="root_revision":plan=replace(plan,root_revision=plan.root_revision+1)
 else:plan=replace(plan,packet_digest="0"*64)
 before_root=engine.root.publication_token();before_bias=engine.robust.snapshot()
 with pytest.raises(RuntimeError,match="STALE_ROBUST_SHARED_ROOT_PLAN"):
  engine.robust.prevalidate_commit(plan,engine.root,packet)
 after_root=engine.root.publication_token()
 assert (after_root.revision,after_root.digest)==(before_root.revision,before_root.digest)
 assert engine.robust.snapshot()==before_bias
 if field=="owner_revision":
  fresh=module.CausalRobustSharedRootOwner(owner)
  fresh_plan=fresh.prepare(owner,engine.root,packet)
  fresh_ticket=fresh.prevalidate_commit(fresh_plan,engine.root,packet)
  foreign=module.CausalRobustSharedRootOwner(owner)
  with pytest.raises(RuntimeError,match="STALE_ROBUST_SHARED_ROOT_PLAN"):
   module._validate_robust_sidecar(foreign,fresh_ticket,foreign.revision)
  module._validate_robust_sidecar(fresh,fresh_ticket,fresh.revision)
  module._apply_robust_sidecar(fresh,fresh_ticket)
  with pytest.raises(RuntimeError,match="STALE_ROBUST_SHARED_ROOT_PLAN"):
   module._validate_robust_sidecar(fresh,fresh_ticket,0)


def test_excluded_nodes_do_not_learn_bias_from_a_root_they_did_not_support():
 owner,items=sequence();engine=DirectOwnerSequence(owner);result=None
 for item in items:
  if isinstance(item,BoundGroupPacket):
   rows=list(item.event.payload)
   for index in range(1,len(rows)):rows[index]=replace(rows[index],valid_mask=0x07)
   item=replace(item,event=replace(item.event,payload=tuple(rows)),digest="")
  result=engine.process(item)
  if result.kind=="UWB":break
 assert result.authoritative_mode=="SINGLE_NODE_ROOT_TRANSLATION"
 trusted=set(result.authoritative_nodes);assert len(trusted)==1
 for node,mean,_,last in result.bias_snapshots:
  if node in trusted:
   assert np.any(mean>0) and np.all(np.isfinite(last))
  else:
   np.testing.assert_array_equal(mean,np.zeros(8));assert np.isnan(last).all()


@pytest.mark.parametrize("final_node_missing", (False, True))
def test_public_reference_owns_each_rows_heterogeneous_valid_slots(final_node_missing):
 owner,items=sequence();first_index=next(i for i,x in enumerate(items) if isinstance(x,BoundGroupPacket))
 packet=items[first_index];rows=list(packet.event.payload);index=-1 if final_node_missing else 0
 rows[index]=replace(rows[index],valid_mask=int(rows[index].valid_mask)&~(1<<7))
 packet=replace(packet,event=replace(packet.event,payload=tuple(rows)),digest="")
 items=list(items[:first_index])+[packet]
 scalar=DirectOwnerSequence(owner);journal=DirectOwnerSequence(owner)
 expected=actual=None
 for item in items:
  expected=scalar.process(item);actual=journal.process(item)
 assert_same(actual,expected)
 missing_node=rows[index].node
 assert (missing_node,7) not in actual.link_identities
 assert len(actual.link_identities)==79


def test_journal_rejection_advances_measurement_time_but_not_bias(monkeypatch):
 owner,first,_=packets();journal=CausalRawRangeDiagnosticJournal(owner)
 for _,item in zip(range(10),sequence()[1]):
  if isinstance(item,RootWorkerEvent):journal.add_imu(item)
  else:break
 import biospur_fusion.c2_uwb_root_world.owner_bound_async_worker as module
 real=module.update_raw_ranges
 def reject(state,*args,**kwargs):
  _,decision=real(state,*args,**kwargs);return state,replace(decision,accepted=False,reason="FIXTURE_REJECT")
 monkeypatch.setattr(module,"update_raw_ranges",reject);before=journal.state.time_s
 _,decisions,_,bias,_,_=journal.process_group(first)
 assert journal.state.time_s>before and all(not accepted and reason=="FIXTURE_REJECT" for _,accepted,reason in decisions)
 assert all(np.array_equal(mean,np.zeros(8)) and np.isnan(last).all() for _,mean,_,last in bias)


def test_journal_late_and_overflow_fail_before_mutation():
 owner,first,_=packets();from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import DirectOwnerSequence
 engine=DirectOwnerSequence(owner)
 for item in sequence()[1]:
  if isinstance(item,RootWorkerEvent) and item.availability_time_s<=first.event.availability_time_s:engine.process(item)
 engine.process(first);root=engine.root.publication_token();state=engine.diagnostic.state.vector.tobytes();bias=tuple(x.snapshot() for x in engine.diagnostic.trackers.values())
 with pytest.raises(ValueError):engine.process(first)
 assert engine.root.publication_token().digest==root.digest and engine.diagnostic.state.vector.tobytes()==state and tuple(x.snapshot() for x in engine.diagnostic.trackers.values())==bias
 journal=CausalRawRangeDiagnosticJournal(owner);last=owner.initial_state.time_s
 for index in range(64):
  last+=.001;journal.add_imu(RootWorkerEvent(index,last,"IMU",ImuSample(last,last,np.array([0,0,9.80665]),np.eye(3),index)))
 with pytest.raises(OverflowError):journal.add_imu(RootWorkerEvent(65,last+.001,"IMU",ImuSample(last+.001,last+.001,np.array([0,0,9.80665]),np.eye(3),65)))
 assert len(journal.buffer)==64 and journal.last_arrival==last


def test_frozen_prospective_horizon_and_native200_capacity_are_exact():
 assert DIAGNOSTIC_GROUP_PERIOD_S==0.120048
 assert DIAGNOSTIC_STARTUP_PERIOD_S==DIAGNOSTIC_GROUP_PERIOD_S
 assert DIAGNOSTIC_ASSEMBLY_PERIOD_S==DIAGNOSTIC_GROUP_PERIOD_S
 assert DIAGNOSTIC_MAXIMUM_IMU_GAP_S==0.005005
 assert DIAGNOSTIC_HORIZON_S==0.245101
 assert DIAGNOSTIC_REQUIRED_IMU_CAPACITY==math.ceil(DIAGNOSTIC_HORIZON_S/DIAGNOSTIC_NATIVE200_PERIOD_S)==50
 assert DIAGNOSTIC_IMU_CAPACITY==64>=DIAGNOSTIC_REQUIRED_IMU_CAPACITY
 assert REFERENCE_DIAGNOSTIC_HORIZON_S==DIAGNOSTIC_HORIZON_S
 assert REFERENCE_DIAGNOSTIC_IMU_CAPACITY==DIAGNOSTIC_IMU_CAPACITY
 _validate_diagnostic_horizon(1.,1.+DIAGNOSTIC_HORIZON_S)
 _validate_reference_diagnostic_horizon(1.,1.+REFERENCE_DIAGNOSTIC_HORIZON_S)
 with pytest.raises(ValueError,match="horizon exceeded"):
  _validate_diagnostic_horizon(1.,np.nextafter(1.+DIAGNOSTIC_HORIZON_S,np.inf))
 with pytest.raises(ValueError,match="horizon exceeded"):
  _validate_reference_diagnostic_horizon(1.,np.nextafter(1.+REFERENCE_DIAGNOSTIC_HORIZON_S,np.inf))


def test_packet_before_frame_availability_rejects_before_any_sequence_state_mutation():
 owner,first,_=packets();engine=DirectOwnerSequence(owner)
 final_imu=owner.initial_state.time_s
 for index in range(DIAGNOSTIC_REQUIRED_IMU_CAPACITY-1):
  final_imu+=DIAGNOSTIC_NATIVE200_PERIOD_S
  sample=ImuSample(final_imu,final_imu,np.array([0,0,9.80665]),np.eye(3),index)
  engine.process(RootWorkerEvent(index,final_imu,"IMU",sample))
 availability_ns=first.availability_global_ns-1;availability=availability_ns*1e-9
 over_bound=replace(first,event=replace(first.event,availability_time_s=availability),availability_global_ns=availability_ns,digest="")
 root=engine.root.publication_token();state=engine.diagnostic.state.vector.tobytes()
 covariance=engine.diagnostic.state.covariance.tobytes();force=engine.diagnostic.force.tobytes()
 rotation=engine.diagnostic.rotation.tobytes();buffer=tuple(engine.diagnostic.buffer)
 trackers=tuple((node,tracker.snapshot()) for node,tracker in sorted(engine.diagnostic.trackers.items()))
 robust=engine.robust.snapshot()
 with pytest.raises(ValueError,match="frame-derived lower bound"):engine.process(over_bound)
 after=engine.root.publication_token()
 assert (after.revision,after.digest)==(root.revision,root.digest)
 assert engine.diagnostic.state.vector.tobytes()==state
 assert engine.diagnostic.state.covariance.tobytes()==covariance
 assert engine.diagnostic.force.tobytes()==force and engine.diagnostic.rotation.tobytes()==rotation
 assert tuple(engine.diagnostic.buffer)==buffer
 assert tuple((node,tracker.snapshot()) for node,tracker in sorted(engine.diagnostic.trackers.items()))==trackers
 assert engine.robust.snapshot()==robust


def test_large_canonical_packet_passes_materializer_and_robust_gate_but_one_ns_early_fails():
 target_ns=234_910_144_244_198
 owner,packet=_packet_shifted_to_availability(target_ns)
 assert canonical_group_availability_time_s(packet.event.payload,clocks=owner.clocks,availability_global_ns=packet.availability_global_ns)==target_ns*1e-9
 plan=CausalRobustSharedRootOwner(owner).prepare(owner,owner.make_root(),packet)
 assert plan.availability_s==target_ns*1e-9
 early_ns=target_ns-1
 early=replace(packet,event=replace(packet.event,availability_time_s=early_ns*1e-9),availability_global_ns=early_ns,digest="")
 with pytest.raises(ValueError,match="frame-derived lower bound"):
  CausalRobustSharedRootOwner(owner).prepare(owner,owner.make_root(),early)


def test_independent_reference_has_same_65th_event_fail_closed_boundary():
 owner,_,_=packets();reference=PublicReference(owner);last=owner.initial_state.time_s
 for index in range(REFERENCE_DIAGNOSTIC_IMU_CAPACITY):
  last+=.001;sample=ImuSample(last,last,np.array([0,0,9.80665]),np.eye(3),index)
  reference.process(RootWorkerEvent(index,last,"IMU",sample))
 root=reference.a.publication_token();buffer=tuple(reference.buffer);last_arrival=reference.last_arrival
 sample=ImuSample(last+.001,last+.001,np.array([0,0,9.80665]),np.eye(3),65)
 with pytest.raises(OverflowError,match="buffer full"):
  reference.process(RootWorkerEvent(65,last+.001,"IMU",sample))
 after=reference.a.publication_token()
 assert (after.revision,after.digest)==(root.revision,root.digest)
 assert tuple(reference.buffer)==buffer and reference.last_arrival==last_arrival


@pytest.mark.parametrize("mode",("stale_owner","stale_pose","future_pose","shadow_tamper","a_policy"))
def test_owner_pose_integrity_rejects_before_direct_root_mutation(mode):
 owner,first,_=packets();from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import DirectOwnerSequence
 engine=DirectOwnerSequence(owner);before=engine.root.publication_token();diag=engine.diagnostic.state.vector.tobytes()
 snapshots=tuple((x.state.time_s,x.state.vector.tobytes(),x.state.covariance.tobytes(),x.applied_constraint_cursor) for x in engine.root._snapshots)
 if mode=="stale_owner":first=replace(first,static_owner_digest="0"*64,digest="")
 elif mode=="a_policy":first=replace(first,a_weight_policy="B_DYNAMIC_WEIGHT",digest="")
 elif mode=="shadow_tamper":
  value=first.b_shadow_owner.snapshots[0];object.__setattr__(value,"query_global_ns",value.query_global_ns+1.)
 else:
  poses=list(first.pose_links);value=poses[0]
  if mode=="stale_pose":poses[0]=replace(value,source_revision=value.source_revision-1)
  else:object.__setattr__(poses[0],"pose_time_ns",int(value.query_time_ns+1))
  first=BoundGroupPacket(owner.digest,first.event,tuple(poses),first.information_weights,first.a_sigma_owner,first.b_sigma_owner,first.b_shadow_owner,availability_global_ns=first.availability_global_ns)
 with pytest.raises(ValueError):engine.process(first)
 after=engine.root.publication_token();assert before.revision==after.revision and before.digest==after.digest
 np.testing.assert_array_equal(before.state.vector,after.state.vector)
 assert diag==engine.diagnostic.state.vector.tobytes() and all(not tracker.snapshot() for tracker in engine.diagnostic.trackers.values())
 assert snapshots==tuple((x.state.time_s,x.state.vector.tobytes(),x.state.covariance.tobytes(),x.applied_constraint_cursor) for x in engine.root._snapshots)


def test_static_owner_tamper_fails_before_ready_and_child_is_clean():
 owner,_,_=packets();object.__setattr__(owner.initial_state,"vector",owner.initial_state.vector+1);started=time.monotonic()
 with pytest.raises(ValueError):AsyncOwnerWorker(owner)
 assert time.monotonic()-started<=2.


def test_child_processing_error_closes_queues_and_exits_within_two_seconds():
 owner,first,_=packets();poses=list(first.pose_links);poses[0]=replace(poses[0],source_revision=poses[0].source_revision-1)
 bad=BoundGroupPacket(owner.digest,first.event,tuple(poses),(),first.a_sigma_owner,first.b_sigma_owner,first.b_shadow_owner,availability_global_ns=first.availability_global_ns)
 worker=AsyncOwnerWorker(owner);worker.submit(bad);started=time.monotonic()
 with pytest.raises(RuntimeError):worker.close_and_collect(1,timeout=2)
 assert time.monotonic()-started<=2 and not worker._p.is_alive() and worker._closed
