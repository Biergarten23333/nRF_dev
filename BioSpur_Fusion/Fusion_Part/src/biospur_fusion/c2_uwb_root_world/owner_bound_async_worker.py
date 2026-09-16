"""U7E fixture-only async ROOT owner with explicit static/dynamic ownership transport."""
from __future__ import annotations

import base64,copy
from dataclasses import asdict,dataclass,field,replace
import hashlib,hmac,json,math,multiprocessing as mp,os,pickle,queue,time
from types import MappingProxyType
from typing import Any,Mapping,Sequence
import numpy as np

from biospur_fusion.c2_uwb_calibration.adaptive_nodes import AdaptiveNodeTrustConfig,adaptive_root_minimum_std_m,select_trusted_body_nodes
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectPoseSnapshot,direct_shadow_weights_batch
from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from biospur_fusion.c2_uwb_calibration.shared_root import (
    evaluate_shared_root_residuals,
    solve_shared_root,
)
from biospur_fusion.c2_uwb_root_world import causal_update_transaction as tx
from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent,THREAD_ENV
from biospur_fusion.c2_uwb_root_world.causal_update_guard import CandidateKind,ReachabilityClass,ReachabilityEnvelope
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import (
    build_causal_links,
    canonical_group_frame_lower_ns,
    group_epoch_times_ns,
)
from biospur_fusion.c2_uwb_root_world.root_worker_event_codec import decode_event,encode_event
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import PoseTagLinkOwner,RangeInformationOwner,ReferenceOwnerBundle
from biospur_fusion.c2_uwb_root_world.tight_range import (ExternalRangeInformationWeights,PersistentRangeBiasTracker,
    RangeBiasPriorSnapshot,RawRangeDecision,RawRangeUpdateConfig,linearize_raw_range_factors,
    prepare_raw_range_update,update_raw_ranges)
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel,UwbRow
from biospur_fusion.root_r3.estimator import RootFilterConfig,propagate_inertial
from biospur_fusion.root_r3.models import PositionObservation,RootState


STATIC_SCHEMA="biospur.c2.u7e.static-owner.v1";EVENT_SCHEMA="biospur.c2.u7e.event.v1"
GROUP_SCHEMA="biospur.c2.u7e.bound-group.v2"
TRANSPORT_IMU="IMU";TRANSPORT_GROUP="GROUP"
_ACTIVE_TIMINGS=None
A_WEIGHT_POLICY="UNIT_INFORMATION_WEIGHT_EXACT_U3"
A_SIGMA_POLICY="U3_LAYOUT_PLUS_FLOOR"
B_SIGMA_POLICY="U5B_QUALITY_ONLY"


def _canonical(x):return json.dumps(x,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def _parse_canonical_doc(blob):
    if not isinstance(blob,bytes):raise TypeError("transport blob must be bytes")
    try:doc=json.loads(blob.decode())
    except (UnicodeDecodeError,json.JSONDecodeError) as exc:raise ValueError("invalid transport JSON") from exc
    if not isinstance(doc,dict) or not hmac.compare_digest(blob,_canonical(doc)):
        raise ValueError("transport is not canonical")
    return doc
def _exact(x,keys):
    if not isinstance(x,dict) or set(x)!=set(keys):raise ValueError("canonical key set mismatch")
    return x
def _seal(core):return {**core,"sha256":hashlib.sha256(_canonical(core)).hexdigest()}
def _verify(doc,keys):
    _exact(doc,set(keys)|{"sha256"});core={k:doc[k] for k in keys};expected=hashlib.sha256(_canonical(core)).hexdigest()
    if not isinstance(doc["sha256"],str) or not hmac.compare_digest(doc["sha256"],expected):raise ValueError("transport digest mismatch")
    return core

def _timed(label,fn,*args,**kwargs):
    if _ACTIVE_TIMINGS is None:return fn(*args,**kwargs)
    started=time.perf_counter_ns()
    try:return fn(*args,**kwargs)
    finally:_ACTIVE_TIMINGS.setdefault(label,[]).append((time.perf_counter_ns()-started)*1e-6)


@dataclass(frozen=True)
class U3SigmaOwner:
    layout_sigma_m:float;floor_sigma_m:float;provenance:str
    policy_id:str=A_SIGMA_POLICY;version:int=1;digest:str=""
    def __post_init__(self):
        if self.policy_id!=A_SIGMA_POLICY or self.version!=1 or not self.provenance:raise ValueError("invalid U3 sigma owner")
        if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(float(x)) or float(x)<=0
               for x in (self.layout_sigma_m,self.floor_sigma_m)):raise ValueError("invalid U3 sigma parameter")
        value=hashlib.sha256(_canonical(self._manifest(False))).hexdigest()
        if self.digest and not hmac.compare_digest(self.digest,value):raise ValueError("U3 sigma digest mismatch")
        object.__setattr__(self,"digest",value)
    def _manifest(self,with_digest=True):
        value={"version":self.version,"policy_id":self.policy_id,"layout_sigma_m":self.layout_sigma_m,
          "floor_sigma_m":self.floor_sigma_m,"provenance":self.provenance}
        return {**value,"digest":self.digest} if with_digest else value
    def sigma(self,quality):
        if isinstance(quality,bool) or not isinstance(quality,(int,np.integer)) or int(quality)<1:raise ValueError("invalid U3 quality")
        return math.sqrt(self.layout_sigma_m**2*(100./int(quality))+self.floor_sigma_m**2)


@dataclass(frozen=True)
class U5BSigmaOwner:
    config:RawRangeUpdateConfig;provenance:str
    policy_id:str=B_SIGMA_POLICY;version:int=1;digest:str=""
    def __post_init__(self):
        if self.policy_id!=B_SIGMA_POLICY or self.version!=1 or not self.provenance or type(self.config) is not RawRangeUpdateConfig:
            raise ValueError("invalid U5B sigma owner")
        self.config.validate()
        if self.config.uncertainty_provenance!=self.provenance:raise ValueError("U5B sigma provenance mismatch")
        value=hashlib.sha256(_canonical(self._manifest(False))).hexdigest()
        if self.digest and not hmac.compare_digest(self.digest,value):raise ValueError("U5B sigma digest mismatch")
        object.__setattr__(self,"digest",value)
    def _manifest(self,with_digest=True):
        value={"version":self.version,"policy_id":self.policy_id,"config":asdict(self.config),"provenance":self.provenance}
        return {**value,"digest":self.digest} if with_digest else value
    def sigma(self,quality):
        if isinstance(quality,bool) or not isinstance(quality,(int,np.integer)) or int(quality)<1:raise ValueError("invalid U5B quality")
        return self.config.nominal_sigma_m*math.sqrt(100./int(quality))


def _u3_sigma_from(value):
    return U3SigmaOwner(**_exact(value,("version","policy_id","layout_sigma_m","floor_sigma_m","provenance","digest")))
def _u5b_sigma_from(value):
    value=_exact(value,("version","policy_id","config","provenance","digest"))
    return U5BSigmaOwner(RawRangeUpdateConfig(**value["config"]),value["provenance"],value["policy_id"],value["version"],value["digest"])


def _vectors(values):
    output={}
    for key,value in values.items():
        row=np.asarray(value,float).copy()
        if row.shape!=(3,) or not np.isfinite(row).all():raise ValueError("invalid B shadow vector")
        row.setflags(write=False);output[str(key)]=row
    return MappingProxyType(output)


@dataclass(frozen=True)
class BShadowSnapshotOwner:
    node:str;action:str;frame:int;pose_global_ns:int;query_global_ns:float
    offsets_world_m:Mapping[str,np.ndarray];normals_world:Mapping[str,np.ndarray]
    joints_relative_world_m:Mapping[str,np.ndarray];source_sha256:str
    def __post_init__(self):
        if not self.node or not self.action or not self.source_sha256 or not self.pose_global_ns<self.query_global_ns:raise ValueError("invalid B shadow snapshot owner")
        object.__setattr__(self,"offsets_world_m",_vectors(self.offsets_world_m));object.__setattr__(self,"normals_world",_vectors(self.normals_world));object.__setattr__(self,"joints_relative_world_m",_vectors(self.joints_relative_world_m))
    def manifest(self):
        vectors=lambda x:{k:v.tolist() for k,v in sorted(x.items())}
        return {"node":self.node,"action":self.action,"frame":self.frame,"pose_global_ns":self.pose_global_ns,
          "query_global_ns":self.query_global_ns,"offsets_world_m":vectors(self.offsets_world_m),"normals_world":vectors(self.normals_world),
          "joints_relative_world_m":vectors(self.joints_relative_world_m),"source_sha256":self.source_sha256}
    def snapshot(self,root):
        return DirectPoseSnapshot(self.action,self.frame,self.pose_global_ns,self.query_global_ns,
          self.query_global_ns-self.pose_global_ns,root,self.offsets_world_m,self.normals_world,self.joints_relative_world_m)


@dataclass(frozen=True)
class BShadowGeometryOwner:
    geometry:DisplayProxyGeometry;snapshots:tuple[BShadowSnapshotOwner,...];provenance:str;digest:str=""
    def __post_init__(self):
        if not self.provenance or type(self.geometry) is not DisplayProxyGeometry:raise ValueError("invalid B shadow geometry owner")
        snapshots=tuple(self.snapshots)
        if (not 1<=len(snapshots)<=10
                or len({x.node for x in snapshots})!=len(snapshots)):
            raise ValueError("B shadow snapshot inventory")
        object.__setattr__(self,"snapshots",snapshots);value=hashlib.sha256(_canonical(self._manifest(False))).hexdigest()
        if self.digest and not hmac.compare_digest(self.digest,value):raise ValueError("B shadow geometry digest mismatch")
        object.__setattr__(self,"digest",value)
    def _manifest(self,with_digest=True):
        g={"torso_height_m":self.geometry.torso_height_m,"hip_span_m":self.geometry.hip_span_m,
          "shoulder_span_m":self.geometry.shoulder_span_m,"segment_length_m":dict(self.geometry.segment_length_m),
          "scope":self.geometry.scope,"physical_joint_centre_geometry":self.geometry.physical_joint_centre_geometry,
          "uwb_antenna_prediction_geometry":self.geometry.uwb_antenna_prediction_geometry}
        value={"geometry":g,"snapshots":[x.manifest() for x in sorted(self.snapshots,key=lambda y:y.node)],"provenance":self.provenance}
        return {**value,"digest":self.digest} if with_digest else value


def _b_shadow_from(value):
    value=_exact(value,("geometry","snapshots","provenance","digest"));g=value["geometry"]
    geometry=DisplayProxyGeometry(**_exact(g,("torso_height_m","hip_span_m","shoulder_span_m","segment_length_m","scope","physical_joint_centre_geometry","uwb_antenna_prediction_geometry")))
    snapshots=tuple(BShadowSnapshotOwner(**_exact(x,("node","action","frame","pose_global_ns","query_global_ns","offsets_world_m","normals_world","joints_relative_world_m","source_sha256"))) for x in value["snapshots"])
    return BShadowGeometryOwner(geometry,snapshots,value["provenance"],value["digest"])


def encode_static_owner(owner:ReferenceOwnerBundle)->bytes:
    owner.validate_integrity();core={"schema":STATIC_SCHEMA,"type":"ReferenceOwnerBundle",
        "owner_digest":owner.digest,"payload":owner._manifest()};return _canonical(_seal(core))


def decode_static_owner(blob:bytes)->ReferenceOwnerBundle:
    doc=_parse_canonical_doc(blob);core=_verify(doc,("schema","type","owner_digest","payload"))
    if core["schema"]!=STATIC_SCHEMA or core["type"]!="ReferenceOwnerBundle":raise ValueError("unknown static schema")
    p=_exact(core["payload"],("schema","root_config","inertial","initial","anchors_m","clocks","anchor_delay_m",
        "tag_delay_m","pose_links","range_information","nominal_envelope","trust_config","provenance"))
    if p["schema"]!="biospur.c2.u7d1.owner.v1":raise ValueError("unknown owner manifest")
    initial=_exact(p["initial"],("time_s","vector","covariance"));prov=_exact(p["provenance"],
        ("root_config","initial_state","anchors","clocks","guard_policy"))
    clocks={x["node"]:DirectNodeLinkClock(**_exact(x,("node","a_ns_per_us","b_ns","boot_epoch","first_timer_us","last_timer_us"))) for x in p["clocks"]}
    poses=[]
    for x in p["pose_links"]:
        x=_exact(x,("node","anchor","query_time_ns","pose_time_ns","offset_world_m","offset_velocity_world_mps","source_epoch","source_revision","source_sha256"))
        poses.append(PoseTagLinkOwner(**x))
    ri=_exact(p["range_information"],("nominal_sigma_m","positive_nlos_cauchy_scale_m","weights_by_node","provenance"))
    info=RangeInformationOwner(ri["nominal_sigma_m"],ri["positive_nlos_cauchy_scale_m"],ri["weights_by_node"],ri["provenance"])
    env=dict(_exact(p["nominal_envelope"],("reachability_class","maximum_root_displacement_m","maximum_root_speed_change_mps",
        "maximum_root_implied_acceleration_mps2","maximum_joint_step_rad","maximum_joint_angular_velocity_rad_s",
        "maximum_joint_angular_acceleration_rad_s2","maximum_evidence_age_s","minimum_impulse_mps","minimum_angular_rate_rad_s",
        "minimum_activity_persistence_s","minimum_unique_nodes","maximum_node_root_spread_m","maximum_node_geometry_condition","provenance")))
    try:env["reachability_class"]=ReachabilityClass[env["reachability_class"]]
    except KeyError as exc:raise ValueError("unknown reachability enum") from exc
    owner=ReferenceOwnerBundle(RootFilterConfig(**p["root_config"]),p["inertial"],
        RootState(initial["time_s"],np.asarray(initial["vector"],float),np.asarray(initial["covariance"],float)),
        np.asarray(p["anchors_m"],float),clocks,np.asarray(p["anchor_delay_m"],float),p["tag_delay_m"],tuple(poses),info,
        ReachabilityEnvelope(**env),AdaptiveNodeTrustConfig(**p["trust_config"]),prov["root_config"],prov["initial_state"],
        prov["anchors"],prov["clocks"],prov["guard_policy"])
    if not hmac.compare_digest(owner.digest,core["owner_digest"]):raise ValueError("static owner digest mismatch")
    return owner


def _pose_dto(x):return {"node":x.node,"anchor":x.anchor,"query_time_ns":x.query_time_ns,"pose_time_ns":x.pose_time_ns,
    "offset_world_m":x.offset_world_m.tolist(),"offset_velocity_world_mps":x.offset_velocity_world_mps.tolist(),
    "source_epoch":x.source_epoch,"source_revision":x.source_revision,"source_sha256":x.source_sha256}
def _pose_from(x):return PoseTagLinkOwner(**_exact(x,("node","anchor","query_time_ns","pose_time_ns","offset_world_m",
    "offset_velocity_world_mps","source_epoch","source_revision","source_sha256")))


@dataclass(frozen=True)
class BoundGroupPacket:
    static_owner_digest:str;event:RootWorkerEvent;pose_links:tuple[PoseTagLinkOwner,...]
    information_weights:tuple[ExternalRangeInformationWeights,...];a_sigma_owner:U3SigmaOwner|None=None
    b_sigma_owner:U5BSigmaOwner|None=None;b_shadow_owner:BShadowGeometryOwner|None=None
    a_weight_policy:str=A_WEIGHT_POLICY;digest:str=""
    availability_global_ns:int=field(kw_only=True)
    def __post_init__(self):
        rows=tuple(self.event.payload);nodes={x.node for x in rows}
        expected={(node,anchor) for node in nodes for anchor in range(8)}
        identities={(x.node,x.anchor) for x in self.pose_links}
        if (self.event.kind!="UWB" or not 1<=len(rows)<=10
                or len(nodes)!=len(rows)
                or len(self.pose_links)!=8*len(rows)):
            raise ValueError("bound group requires one to ten unique UWB rows")
        if type(self.availability_global_ns) is not int or self.availability_global_ns<0:raise ValueError("invalid canonical packet availability")
        if self.event.availability_time_s!=self.availability_global_ns*1e-9:raise ValueError("packet availability float/ns mismatch")
        if identities!=expected or len(identities)!=len(self.pose_links):raise ValueError("pose identities incomplete")
        if type(self.b_shadow_owner) is not BShadowGeometryOwner:raise ValueError("B shadow reconstruction owner required")
        if {x.node for x in self.b_shadow_owner.snapshots}!=nodes:raise ValueError("B shadow node inventory mismatch")
        if self.information_weights:raise ValueError("reconstructed B weights cannot be packet inputs")
        if type(self.a_sigma_owner) is not U3SigmaOwner or type(self.b_sigma_owner) is not U5BSigmaOwner:raise ValueError("dual sigma owners required")
        value=hashlib.sha256(_canonical({"static_owner_digest":self.static_owner_digest,
            "availability_global_ns":self.availability_global_ns,
            "event_b64":base64.b64encode(encode_event(self.event)).decode(),"pose_links":[_pose_dto(x) for x in self.pose_links],
            "a_weight_policy":self.a_weight_policy,
            "a_sigma_owner":self.a_sigma_owner._manifest(),"b_sigma_owner":self.b_sigma_owner._manifest(),
            "b_shadow_owner":None if self.b_shadow_owner is None else self.b_shadow_owner._manifest(),
            "information_weights":[{"node":x.node,"evidence_time_s":x.evidence_time_s,"weights":x.weights.tolist(),
              "provenance":x.provenance} for x in sorted(self.information_weights,key=lambda y:y.node)]})).hexdigest()
        if self.digest and not hmac.compare_digest(self.digest,value):raise ValueError("bound group digest mismatch")
        object.__setattr__(self,"pose_links",tuple(self.pose_links));object.__setattr__(self,"information_weights",tuple(self.information_weights));object.__setattr__(self,"digest",value)


def encode_group(packet):
    BoundGroupPacket(packet.static_owner_digest,packet.event,packet.pose_links,packet.information_weights,
      packet.a_sigma_owner,packet.b_sigma_owner,packet.b_shadow_owner,packet.a_weight_policy,packet.digest,
      availability_global_ns=packet.availability_global_ns)
    core={"schema":GROUP_SCHEMA,"type":"BoundGroupPacket","static_owner_digest":packet.static_owner_digest,
      "availability_global_ns":packet.availability_global_ns,
      "event_b64":base64.b64encode(encode_event(packet.event)).decode(),"pose_links":[_pose_dto(x) for x in packet.pose_links],
      "a_weight_policy":packet.a_weight_policy,
      "a_sigma_owner":packet.a_sigma_owner._manifest(),"b_sigma_owner":packet.b_sigma_owner._manifest(),
      "b_shadow_owner":packet.b_shadow_owner._manifest(),
      "information_weights":[{"node":x.node,"evidence_time_s":x.evidence_time_s,"weights":x.weights.tolist(),"provenance":x.provenance}
        for x in sorted(packet.information_weights,key=lambda y:y.node)],
      "packet_digest":packet.digest};return _canonical(_seal(core))
def _decode_group_doc(doc):
    core=_verify(doc,("schema","type","static_owner_digest","availability_global_ns","event_b64","pose_links","a_weight_policy","a_sigma_owner","b_sigma_owner","b_shadow_owner","information_weights","packet_digest"))
    if core["schema"]!=GROUP_SCHEMA or core["type"]!="BoundGroupPacket":raise ValueError("unknown group schema")
    event=decode_event(base64.b64decode(core["event_b64"],validate=True));packet=BoundGroupPacket(core["static_owner_digest"],event,
        tuple(_pose_from(x) for x in core["pose_links"]),tuple(ExternalRangeInformationWeights(**_exact(x,("node","evidence_time_s","weights","provenance")))
          for x in core["information_weights"]),_u3_sigma_from(core["a_sigma_owner"]),_u5b_sigma_from(core["b_sigma_owner"]),
          _b_shadow_from(core["b_shadow_owner"]),core["a_weight_policy"],core["packet_digest"],
          availability_global_ns=core["availability_global_ns"])
    return packet
def decode_group(blob):return _decode_group_doc(_parse_canonical_doc(blob))
def encode_imu(event):
    if event.kind!="IMU":raise ValueError("IMU event required")
    core={"schema":EVENT_SCHEMA,"type":"ImuEvent","event_b64":base64.b64encode(encode_event(event)).decode()};return _canonical(_seal(core))
def _decode_imu_doc(doc):
    core=_verify(doc,("schema","type","event_b64"))
    if core["schema"]!=EVENT_SCHEMA or core["type"]!="ImuEvent":raise ValueError("unknown event schema")
    event=decode_event(base64.b64decode(core["event_b64"],validate=True))
    if event.kind!="IMU":raise ValueError("invalid IMU transport")
    return event
def decode_imu(blob):return _decode_imu_doc(_parse_canonical_doc(blob))

def _decode_typed_transport(discriminator,blob):
    doc=_parse_canonical_doc(blob)
    identity=(doc.get("schema"),doc.get("type"))
    if discriminator==TRANSPORT_IMU:
      if identity!=(EVENT_SCHEMA,"ImuEvent"):raise ValueError("transport discriminator/schema mismatch")
      return _decode_imu_doc(doc)
    if discriminator==TRANSPORT_GROUP:
      if identity!=(GROUP_SCHEMA,"BoundGroupPacket"):raise ValueError("transport discriminator/schema mismatch")
      return _decode_group_doc(doc)
    raise ValueError("unknown transport discriminator")


@dataclass(frozen=True)
class PublishedResult:
    kind:str;sequence:int;decision:str|None;root_reason:str|None;state:np.ndarray;covariance:np.ndarray
    h:tuple=();sensor_r:tuple=();total_r:tuple=();s:tuple=();nis:tuple=();rank:tuple=();condition:tuple=()
    link_identities:tuple[tuple[str,int],...]=();link_count:int=0;guard_calls:int=0;publication_lag_ms:float=0.
    diagnostic_state:np.ndarray|None=None;diagnostic_covariance:np.ndarray|None=None
    diagnostic_committed_state:np.ndarray|None=None;diagnostic_committed_covariance:np.ndarray|None=None;diagnostic_committed_time_s:float|None=None
    diagnostic_decisions:tuple=();diagnostic_weights:tuple=();bias_snapshots:tuple=();diagnostic_node_states:tuple=()
    diagnostic_predicted:tuple=();authoritative_weights:tuple=();service_ms:float=0.
    authoritative_mode:str|None=None;authoritative_nodes:tuple[str,...]=()
    effective_r:tuple=();effective_s:tuple=();robust_nis:tuple=()
    external_information_weights:tuple=();robust_influence_weights:tuple=();irls_information_weights:tuple=()
    cross_covariance_status:tuple=()
    def __post_init__(self):
        for name,shape in (("state",(9,)),("covariance",(9,9))):
            value=np.asarray(getattr(self,name),float).copy();value.setflags(write=False);object.__setattr__(self,name,value)
        for name in ("h","sensor_r","total_r","s","diagnostic_predicted","effective_r","effective_s"):
            values=[]
            for x in getattr(self,name):y=np.asarray(x,float).copy();y.setflags(write=False);values.append(y)
            object.__setattr__(self,name,tuple(values))
        for name,shape in (("diagnostic_state",(9,)),("diagnostic_covariance",(9,9)),
                           ("diagnostic_committed_state",(9,)),("diagnostic_committed_covariance",(9,9))):
            value=getattr(self,name)
            if value is not None:
                value=np.asarray(value,float).copy();value.setflags(write=False);object.__setattr__(self,name,value)
        object.__setattr__(self,"diagnostic_weights",tuple((node,_ro_weight(value)) for node,value in self.diagnostic_weights))
        object.__setattr__(self,"external_information_weights",tuple((node,_ro_weight(value)) for node,value in self.external_information_weights))
        object.__setattr__(self,"robust_influence_weights",tuple((node,tuple(anchors),_ro_state(value,(len(anchors),))) for node,anchors,value in self.robust_influence_weights))
        object.__setattr__(self,"irls_information_weights",tuple((node,tuple(anchors),_ro_state(value,(len(anchors),))) for node,anchors,value in self.irls_information_weights))
        object.__setattr__(self,"bias_snapshots",tuple((node,_ro_weight(mean),_ro_weight(var),_ro_weight(last,finite=False))
          for node,mean,var,last in self.bias_snapshots))
        rows=[]
        for node,pre,pre_cov,post,post_cov in self.diagnostic_node_states:
            rows.append((node,_ro_state(pre,(9,)),_ro_state(pre_cov,(9,9)),_ro_state(post,(9,)),_ro_state(post_cov,(9,9))))
        object.__setattr__(self,"diagnostic_node_states",tuple(rows))


def _ro_weight(value,finite=True):
    result=np.asarray(value,float).copy()
    if result.shape!=(8,) or (finite and not np.isfinite(result).all()) or (not finite and np.isinf(result).any()):raise ValueError("diagnostic vector invalid")
    result.setflags(write=False);return result


def _ro_state(value,shape):
    result=np.asarray(value,float).copy()
    if result.shape!=shape or not np.isfinite(result).all():raise ValueError("diagnostic state invalid")
    result.setflags(write=False);return result


def _dynamic_owner(static,packet):
    static.validate_integrity();BoundGroupPacket(packet.static_owner_digest,packet.event,packet.pose_links,packet.information_weights,
      packet.a_sigma_owner,packet.b_sigma_owner,packet.b_shadow_owner,packet.a_weight_policy,packet.digest,
      availability_global_ns=packet.availability_global_ns)
    if not hmac.compare_digest(static.digest,packet.static_owner_digest):raise ValueError("static owner mismatch")
    if packet.a_weight_policy!=A_WEIGHT_POLICY:raise ValueError("authoritative weight policy mismatch")
    if packet.a_sigma_owner.policy_id!=A_SIGMA_POLICY or packet.b_sigma_owner.policy_id!=B_SIGMA_POLICY:raise ValueError("sigma policy mismatch")
    if not math.isclose(packet.b_sigma_owner.config.nominal_sigma_m,static.range_information.nominal_sigma_m,rel_tol=0,abs_tol=0):raise ValueError("B sigma owner mismatch")
    expected_scale=static.range_information.positive_nlos_cauchy_scale_m
    if packet.b_sigma_owner.config.positive_nlos_cauchy_scale_m!=expected_scale:raise ValueError("B robust owner mismatch")
    nodes={row.node for row in packet.event.payload};weights={}
    if {x.node for x in packet.b_shadow_owner.snapshots}!=nodes:raise ValueError("B shadow node inventory mismatch")
    return _DynamicReferenceOwner(static,packet.pose_links),weights


@dataclass(frozen=True)
class _DynamicReferenceOwner:
    """Packet-scoped pose projection over the immutable ten-node static owner."""
    static:ReferenceOwnerBundle;pose_links:tuple[PoseTagLinkOwner,...]
    digest:str=field(init=False)
    def __post_init__(self):
      links=tuple(self.pose_links);nodes={x.node for x in links}
      if (not links or len(links)!=8*len(nodes)
          or {(x.node,x.anchor) for x in links}
             !={(node,anchor) for node in nodes for anchor in range(8)}
          or not nodes<=set(self.static.clocks)):
        raise ValueError("dynamic pose-link inventory mismatch")
      object.__setattr__(self,"pose_links",links)
      object.__setattr__(self,"digest",hashlib.sha256(_canonical({
        "static":self.static.digest,"pose_links":[_pose_dto(x) for x in links],
      })).hexdigest())
    @property
    def clocks(self):return self.static.clocks
    @property
    def anchors_m(self):return self.static.anchors_m
    @property
    def anchor_delay_m(self):return self.static.anchor_delay_m
    @property
    def tag_delay_m(self):return self.static.tag_delay_m
    @property
    def range_information(self):return self.static.range_information
    @property
    def trust_config(self):return self.static.trust_config
    @property
    def nominal_envelope(self):return self.static.nominal_envelope
    def pose(self,node,query):
      matches=[item for item in self.pose_links if item.node==node and item.query_time_ns==query]
      if len(matches)!=1:raise ValueError("missing/stale exact pose link owner")
      item=matches[0]
      from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import StrictFloorOffset
      return StrictFloorOffset(item.offset_world_m,item.pose_time_ns,query,
        query-item.pose_time_ns,item.source_epoch)


_PREPARED_DYNAMIC_OWNER_KEY=object()


@dataclass(frozen=True)
class _PreparedDynamicOwner:
    static_digest:str;packet_digest:str;owner_digest:str;owner:ReferenceOwnerBundle
    weights:Mapping[str,ExternalRangeInformationWeights];key:object


def _prepare_dynamic_owner(static,packet):
    owner,weights=_dynamic_owner(static,packet)
    return _PreparedDynamicOwner(static.digest,packet.digest,owner.digest,owner,weights,_PREPARED_DYNAMIC_OWNER_KEY)


def _consume_prepared_dynamic_owner(static,packet,prepared):
    if (type(prepared) is not _PreparedDynamicOwner
            or prepared.key is not _PREPARED_DYNAMIC_OWNER_KEY
            or not hmac.compare_digest(prepared.static_digest,static.digest)
            or not hmac.compare_digest(prepared.packet_digest,packet.digest)
            or not hmac.compare_digest(prepared.owner.digest,prepared.owner_digest)):
      raise ValueError("prepared dynamic owner mismatch")
    return prepared.owner,prepared.weights


@dataclass(frozen=True)
class _RobustSharedRootPlan:
    owner_revision:int;owner_digest:str;root_revision:int;root_digest:str;packet_digest:str;measurement_s:float;availability_s:float
    links:tuple;selection:object;candidate:object;factors:tuple;factor_nodes:tuple[str,...]
    effective_r:tuple;effective_s:tuple;robust_nis:tuple
    weights:tuple[tuple[str,np.ndarray],...];staged_trackers:Mapping[str,PersistentRangeBiasTracker]
    committed_bias_snapshots:tuple;staged_bias_snapshots:tuple;source_rows:int;key:object


_ROBUST_PLAN_KEY=object();_ROBUST_COMMIT_KEY=object()


@dataclass(frozen=True)
class RobustCandidateMeasurementRejection:
    """Immutable nonfatal outcome for an unusable robust measurement."""
    packet_digest:str;root_revision:int;root_digest:str
    measurement_s:float;measurement_time_ns:int
    availability_global_ns:int;source_sequence:int
    source_rows:int;trusted_partition:tuple[str,...]
    trusted_link_identities:tuple[tuple[str,int],...]
    solver_reason:str;solver_success:bool;rank:int
    condition:float|str;nfev:int;cost:float|str
    def __post_init__(self):
      categorical={"NAN","POSITIVE_INFINITY","NEGATIVE_INFINITY"}
      condition_valid=(
        type(self.condition) is float and math.isfinite(self.condition)
        or type(self.condition) is str and self.condition in categorical
      )
      cost_valid=(
        type(self.cost) is float and math.isfinite(self.cost)
        or type(self.cost) is str and self.cost in categorical
      )
      condition_nonfinite=type(self.condition) is str
      invalid=(not self.solver_success or self.rank!=3 or condition_nonfinite)
      if (len(self.packet_digest)!=64 or len(self.root_digest)!=64
          or type(self.root_revision) is not int or self.root_revision<0
          or type(self.measurement_s) is not float
          or not math.isfinite(self.measurement_s) or self.measurement_s<0.0
          or type(self.measurement_time_ns) is not int
          or self.measurement_time_ns!=int(round(self.measurement_s*1e9))
          or type(self.availability_global_ns) is not int
          or self.measurement_time_ns>self.availability_global_ns
          or type(self.source_sequence) is not int or self.source_sequence<0
          or not 1<=self.source_rows<=10
          or type(self.trusted_partition) is not tuple
          or len(set(self.trusted_partition))!=len(self.trusted_partition)
          or type(self.trusted_link_identities) is not tuple
          or len(set(self.trusted_link_identities))!=len(self.trusted_link_identities)
          or any(node not in self.trusted_partition
                 for node,_anchor in self.trusted_link_identities)
          or not self.solver_reason or type(self.solver_success) is not bool
          or type(self.rank) is not int or self.rank<0
          or not condition_valid or not cost_valid or not invalid
          or type(self.nfev) is not int or self.nfev<0):
        raise ValueError("invalid robust-candidate measurement rejection")


@dataclass(frozen=True)
class _RobustCommitTicket:
    base_revision:int;new_revision:int;previous_trackers:dict[str,PersistentRangeBiasTracker]
    trackers:dict[str,PersistentRangeBiasTracker];owner_key:object;key:object


class CausalRobustSharedRootOwner:
    """Prepare one causal ten-node robust update and commit nuisance state once.

    The complete plan is derived from the committed pre-group root and bias
    states.  Candidate root output is never reused to choose its own weights.
    Bias evidence is evaluated at the solved root, but remains staged until
    the guarded shared-root transaction commits.
    """
    def __init__(self,owner):
      owner.validate_integrity();self.owner_digest=owner.digest;self.trackers={node:PersistentRangeBiasTracker() for node in owner.clocks};self.revision=0;self.__commit_owner_key=object()
      # Allocate the fixed inventory at construction, not while preparing an
      # update, so rejection can be byte-for-byte state preserving.
      for node,tracker in self.trackers.items():tracker._ensure(node)
    def snapshot(self):
      return (self.revision,tuple((node,tracker.snapshot()) for node,tracker in sorted(self.trackers.items())))
    def _bias_snapshots(self,trackers,query):
      rows=[]
      for node in sorted(trackers):
        value=trackers[node].prior_snapshot(node,snapshot_time_s=query)
        rows.append((node,value.mean_m,value.variance_m2,value.last_accepted_time_s))
      return tuple(rows)
    def prepare(self,static,root,packet,prepared_dynamic_owner=None):
      if prepared_dynamic_owner is None:prepared_dynamic_owner=_prepare_dynamic_owner(static,packet)
      ephemeral,_=_consume_prepared_dynamic_owner(static,packet,prepared_dynamic_owner)
      if not hmac.compare_digest(self.owner_digest,static.digest):raise ValueError("robust owner/static mismatch")
      token=root.publication_token();rows=tuple(packet.event.payload);base_trackers=copy.deepcopy(self.trackers)
      links,_,measurement,_frame_availability=build_causal_links(rows,clocks=ephemeral.clocks,strict_floor_offset=ephemeral.pose,
        anchor_delay_m=ephemeral.anchor_delay_m,tag_delay_m=ephemeral.tag_delay_m,sigma_for_quality=packet.b_sigma_owner.sigma)
      frame_lower_ns=canonical_group_frame_lower_ns(rows,clocks=ephemeral.clocks)
      if packet.availability_global_ns < frame_lower_ns:
        raise ValueError("packet availability precedes frame-derived lower bound")
      availability=packet.availability_global_ns*1e-9
      if token.time_s>availability+1e-12:raise ValueError("root publication is from the future")
      root_at_measurement=token.state.vector[:3]+(measurement-token.time_s)*token.state.vector[3:6]
      shadows={x.node:x for x in packet.b_shadow_owner.snapshots};poses={(x.node,x.anchor):x for x in packet.pose_links}
      row_by_node={row.node:row for row in rows};external={};priors={};factors=[]
      for node,row in sorted(row_by_node.items()):
        clock=ClockModel(ephemeral.clocks[node].boot_epoch,ephemeral.clocks[node].a_ns_per_us,ephemeral.clocks[node].b_ns,0.)
        valid=tuple(anchor for anchor in range(8) if row.valid_mask&(1<<anchor) and 0<int(row.ranges_mm[anchor])<0xffff)
        if not valid:continue
        epochs=np.asarray([clock.seconds(row.strobe_us+.5*row.t_round_us[a]) for a in valid],float)
        shadow=shadows[node];query_s=shadow.query_global_ns*1e-9
        predicted_query=root_at_measurement+(query_s-measurement)*token.state.vector[3:6]
        snapshot=shadow.snapshot(predicted_query)
        values=np.ones(8);body=_timed("prepare.shadow_weights",direct_shadow_weights_batch,node=node,anchor_positions_world_m=ephemeral.anchors_m,
          snapshot=snapshot,geometry=packet.b_shadow_owner.geometry)
        for anchor in valid:values[anchor]=body[anchor]
        weight=ExternalRangeInformationWeights(node,shadow.pose_global_ns*1e-9,values,ephemeral.range_information.provenance)
        if not weight.evidence_time_s<float(np.min(epochs)):raise ValueError("robust weights are not strictly pre-link")
        prior=base_trackers[node].prior_snapshot(node,snapshot_time_s=weight.evidence_time_s)
        external[node]=weight;priors[node]=prior
        if len(valid)>=4:
          reference=float(np.median(epochs));vector=token.state.vector.copy();vector[:3]+=token.state.vector[3:6]*(reference-token.time_s)
          predicted_state=RootState(reference,vector,token.state.covariance)
          augmented=RangeBiasPriorSnapshot(node,prior.snapshot_time_s,prior.mean_m+ephemeral.anchor_delay_m+ephemeral.tag_delay_m,
            prior.variance_m2,prior.last_accepted_time_s)
          geom=min((poses[(node,anchor)] for anchor in valid),key=lambda x:x.query_time_ns)
          factors.append((node,_timed("prepare.linearize",linearize_raw_range_factors,predicted_state,row,anchors_m=ephemeral.anchors_m,clock=clock,
            bias_prior=augmented,information_weights=weight,tag_offset_world_m=geom.offset_world_m,
            config=packet.b_sigma_owner.config,_enforce_geometry=False)))
      robust_links=[]
      factor_by_node={node:value for node,value in factors}
      local_by_node={node:{anchor:i for i,anchor in enumerate(value.anchors)} for node,value in factors}
      for link in links:
        prior=priors[link.node];local=local_by_node.get(link.node,{}).get(link.anchor)
        if local is None:continue
        factor=factor_by_node[link.node];variance=float(factor.quality_sigma_m[local]**2+prior.variance_m2[link.anchor])
        robust_links.append(replace(link,range_m=link.range_m-prior.mean_m[link.anchor],sigma_m=math.sqrt(variance),
          information_weight=float(external[link.node].weights[link.anchor]*factor.robust_weights[local])))
      selection=_timed("prepare.select_nodes",select_trusted_body_nodes,tuple(robust_links),anchors_m=ephemeral.anchors_m,initial_root_m=root_at_measurement,
        root_velocity_mps=token.state.vector[3:6],total_nodes=10,config=ephemeral.trust_config)
      if not selection.trusted_links:
        return RobustCandidateMeasurementRejection(
          packet.digest,token.revision,token.digest,measurement,
          int(round(measurement*1e9)),
          packet.availability_global_ns,packet.event.sequence,len(rows),(),(),
          "NO_TRUSTED_NODE",False,0,"NAN",0,"NAN")
      candidate=_timed("prepare.solve_root",solve_shared_root,selection.trusted_links,anchors_m=ephemeral.anchors_m,initial_root_m=root_at_measurement,
        root_velocity_mps=token.state.vector[3:6])
      if (not candidate.success or candidate.rank!=3
          or not math.isfinite(candidate.condition)):
        label=lambda value:(float(value) if math.isfinite(float(value)) else
          ("NAN" if math.isnan(float(value)) else
           ("POSITIVE_INFINITY" if float(value)>0 else "NEGATIVE_INFINITY")))
        return RobustCandidateMeasurementRejection(
          packet.digest,token.revision,token.digest,measurement,
          int(round(measurement*1e9)),
          packet.availability_global_ns,packet.event.sequence,len(rows),
          tuple(selection.trusted_nodes),
          tuple((link.node,link.anchor) for link in selection.trusted_links),
          candidate.reason,bool(candidate.success),int(candidate.rank),
          label(candidate.condition),int(candidate.nfev),label(candidate.cost))
      staged=copy.deepcopy(base_trackers)
      by_node={node:[] for node in row_by_node}
      for link in robust_links:by_node[link.node].append(link)
      trusted=set(selection.trusted_nodes)
      for node,node_links in sorted(by_node.items()):
        # Bias learns only from links that explicitly participated in the
        # accepted shared-root candidate. Excluded nodes retain their prior.
        if not node_links or node not in trusted:continue
        prior=priors[node];node_links=sorted(node_links,key=lambda x:x.anchor);ids=tuple(x.anchor for x in node_links)
        epochs=np.asarray([measurement+x.link_dt_s for x in node_links]);predicted=np.asarray([
          np.linalg.norm(candidate.root_position_m+x.tag_offset_world_m+x.link_dt_s*token.state.vector[3:6]-ephemeral.anchors_m[x.anchor]) for x in node_links])
        measured=np.asarray([x.range_m+prior.mean_m[x.anchor] for x in node_links]);innovation=measured-prior.mean_m[list(ids)]-predicted
        sigma=np.asarray([x.sigma_m/math.sqrt(max(x.information_weight,1e-300)) for x in node_links]);sensor=np.asarray([
          math.sqrt(factor_by_node[node].sensor_r_m2[local_by_node[node][x.anchor],local_by_node[node][x.anchor]]) for x in node_links])
        decision=RawRangeDecision(True,"ACCEPTED",ids,epochs,float(np.median(epochs)),measured,predicted,innovation,
          innovation/sigma,np.asarray([factor_by_node[node].robust_weights[local_by_node[node][x.anchor]] for x in node_links]),sigma,
          3,float(candidate.condition),1,packet.b_sigma_owner.config.uncertainty_provenance,sensor)
        staged[node].update(node,decision)
      query=float(np.nextafter(max(measurement+x.link_dt_s for x in robust_links),np.inf))
      committed=self._bias_snapshots(copy.deepcopy(self.trackers),query)
      ordered_factors=tuple(value for _,value in factors)
      effective_r=tuple(np.diag(1./value.irls_information_weights) for value in ordered_factors)
      effective_s=tuple(value.s_prior_m2-value.r_prior_m2+r for value,r in zip(ordered_factors,effective_r))
      robust_nis=tuple(float(value.innovations_m@np.linalg.solve(s,value.innovations_m))
        for value,s in zip(ordered_factors,effective_s))
      return _RobustSharedRootPlan(self.revision,self.owner_digest,token.revision,token.digest,packet.digest,measurement,availability,
        tuple(robust_links),selection,candidate,ordered_factors,tuple(node for node,_ in factors),effective_r,effective_s,robust_nis,
        tuple((node,external[node].weights) for node in sorted(external)),MappingProxyType(staged),committed,
        self._bias_snapshots(staged,query),len(rows),_ROBUST_PLAN_KEY)
    def prevalidate_commit(self,plan,root,packet):
      token=root.publication_token()
      if (type(plan) is not _RobustSharedRootPlan or plan.key is not _ROBUST_PLAN_KEY
          or plan.owner_revision!=self.revision or not hmac.compare_digest(plan.owner_digest,self.owner_digest)
          or plan.root_revision!=token.revision or not hmac.compare_digest(plan.root_digest,token.digest)
          or not hmac.compare_digest(plan.packet_digest,packet.digest)):
        raise RuntimeError("STALE_ROBUST_SHARED_ROOT_PLAN")
      # Materialize and validate every staged array before the root owner can
      # mutate. Applying the returned ticket is assignment-only and no-throw.
      staged=dict(plan.staged_trackers);self._bias_snapshots(copy.deepcopy(staged),
        float(np.nextafter(max(plan.measurement_s+x.link_dt_s for x in plan.links),np.inf)))
      return _RobustCommitTicket(self.revision,self.revision+1,self.trackers,staged,self.__commit_owner_key,_ROBUST_COMMIT_KEY)
    def _apply_prevalidated_commit(self,ticket):
      # Exactly two reference/scalar assignments. No construction, validation,
      # hashing, callback, or allocation is permitted here.
      self.trackers=ticket.trackers;self.revision=ticket.new_revision
    def _rollback_prevalidated_commit(self,ticket):
      # Same assignment-only contract for a root-commit exception.
      self.trackers=ticket.previous_trackers;self.revision=ticket.base_revision


def _validate_robust_sidecar(owner,ticket,base_token):
    if (type(owner) is not CausalRobustSharedRootOwner
        or type(ticket) is not _RobustCommitTicket
        or ticket.key is not _ROBUST_COMMIT_KEY
        or ticket.owner_key is not owner._CausalRobustSharedRootOwner__commit_owner_key
        or type(base_token) is not int
        or owner.revision!=base_token
        or ticket.base_revision!=base_token
        or ticket.new_revision!=base_token+1):
      raise RuntimeError("STALE_ROBUST_SHARED_ROOT_PLAN")


def _apply_robust_sidecar(owner,ticket):
    owner._apply_prevalidated_commit(ticket)


def _rollback_robust_sidecar(owner,ticket):
    owner._rollback_prevalidated_commit(ticket)


@dataclass(frozen=True)
class PristineRobustBootstrapCandidate:
    """Pure public projection of the runtime robust plan for world bootstrap."""
    accepted:bool;reason:str;root_position_m:np.ndarray|None;covariance_m2:np.ndarray|None
    physical_residual_rms_m:float|None
    trusted_nodes:tuple[str,...];trusted_link_identities:tuple[tuple[str,int],...]
    external_information_weights:tuple[tuple[str,np.ndarray],...]
    robust_influence_weights:tuple[tuple[str,tuple[int,...],np.ndarray],...]
    irls_information_weights:tuple[tuple[str,tuple[int,...],np.ndarray],...]
    candidate:object|None=None
    def __post_init__(self):
      if type(self.accepted) is not bool or not self.reason:raise ValueError("invalid robust bootstrap outcome")
      def ro(value,shape=None):
        result=np.asarray(value,dtype=float).copy()
        if shape is not None:result=result.reshape(shape)
        if not np.isfinite(result).all():raise ValueError("nonfinite robust bootstrap value")
        result.setflags(write=False);return result
      if self.accepted:
        object.__setattr__(self,"root_position_m",ro(self.root_position_m,(3,)))
        object.__setattr__(self,"covariance_m2",ro(self.covariance_m2,(3,3)))
        if (not isinstance(self.physical_residual_rms_m,(int,float,np.integer,np.floating))
            or not math.isfinite(float(self.physical_residual_rms_m))
            or float(self.physical_residual_rms_m)<0):
          raise ValueError("invalid robust bootstrap physical residual RMS")
        object.__setattr__(self,"physical_residual_rms_m",float(self.physical_residual_rms_m))
      elif (self.root_position_m is not None or self.covariance_m2 is not None
            or self.physical_residual_rms_m is not None):
        raise ValueError("rejected robust bootstrap carries a state")
      object.__setattr__(self,"trusted_nodes",tuple(self.trusted_nodes))
      object.__setattr__(self,"trusted_link_identities",tuple(self.trusted_link_identities))
      object.__setattr__(self,"external_information_weights",tuple((node,ro(value,(8,))) for node,value in self.external_information_weights))
      object.__setattr__(self,"robust_influence_weights",tuple((node,tuple(anchors),ro(value,(len(anchors),))) for node,anchors,value in self.robust_influence_weights))
      object.__setattr__(self,"irls_information_weights",tuple((node,tuple(anchors),ro(value,(len(anchors),))) for node,anchors,value in self.irls_information_weights))


def prepare_pristine_robust_bootstrap_candidate(static,root,packet):
    """Run the exact runtime robust preparation without committing any owner."""
    owner=CausalRobustSharedRootOwner(static)
    try:
      plan=owner.prepare(static,root,packet)
    except RuntimeError as error:
      if str(error)!="NO_TRUSTED_NODE":
        raise
      return PristineRobustBootstrapCandidate(False,str(error),None,None,None,(),(),(),(),(),None)
    if isinstance(plan,RobustCandidateMeasurementRejection):
      return PristineRobustBootstrapCandidate(False,
        f"ROBUST_CANDIDATE_REJECTED:{plan.solver_reason}",None,None,None,
        plan.trusted_partition,plan.trusted_link_identities,(),(),(),None)
    physical_residuals=evaluate_shared_root_residuals(
      plan.selection.trusted_links,anchors_m=static.anchors_m,
      root_position_m=plan.candidate.root_position_m,
      root_velocity_mps=root.publication_token().state.vector[3:6])
    physical_residual_rms=float(np.sqrt(np.mean(np.square(physical_residuals))))
    if (not np.array_equal(physical_residuals,plan.candidate.residuals_m)
        or not math.isfinite(physical_residual_rms)):
      raise RuntimeError("robust bootstrap physical residual projection mismatch")
    information=np.zeros((3,3),dtype=float);velocity=root.publication_token().state.vector[3:6]
    for link in plan.selection.trusted_links:
      delta=(plan.candidate.root_position_m+link.tag_offset_world_m
             +link.link_dt_s*velocity-static.anchors_m[link.anchor])
      norm=float(np.linalg.norm(delta))
      if not math.isfinite(norm) or norm<=0:raise RuntimeError("robust bootstrap information geometry invalid")
      jacobian=delta/norm
      information+=np.outer(jacobian,jacobian)*(link.information_weight/link.sigma_m**2)
    if np.linalg.matrix_rank(information)!=3 or not np.isfinite(information).all():
      return PristineRobustBootstrapCandidate(False,"ROBUST_INFORMATION_RANK_REJECTED",None,None,None,(),(),(),(),(),None)
    floor=adaptive_root_minimum_std_m(len(plan.selection.trusted_nodes))
    covariance=np.linalg.inv(information)+np.eye(3)*floor**2
    factors={node:value for node,value in zip(plan.factor_nodes,plan.factors)}
    def frozen(value):
      result=np.asarray(value,dtype=float).copy();result.setflags(write=False);return result
    public_candidate=replace(
      plan.candidate,root_position_m=frozen(plan.candidate.root_position_m),
      residuals_m=frozen(plan.candidate.residuals_m),
      standardized_residuals=frozen(plan.candidate.standardized_residuals),
    )
    return PristineRobustBootstrapCandidate(
      True,"ACCEPTED",plan.candidate.root_position_m,covariance,
      physical_residual_rms,
      plan.selection.trusted_nodes,
      tuple((link.node,link.anchor) for link in plan.selection.trusted_links),
      plan.weights,
      tuple((node,value.anchors,value.robust_weights) for node,value in factors.items()),
      tuple((node,value.anchors,value.irls_information_weights) for node,value in factors.items()),
      public_candidate,
    )


def _execute_group(static,root,packet,prepared_dynamic_owner=None,robust_owner=None):
    if prepared_dynamic_owner is None:prepared_dynamic_owner=_timed("prepare.dynamic_owner",_prepare_dynamic_owner,static,packet)
    ephemeral,_=_consume_prepared_dynamic_owner(static,packet,prepared_dynamic_owner)
    baseline={(x.node,x.anchor):x for x in static.pose_links}
    if any(x.source_revision<baseline[(x.node,x.anchor)].source_revision
           or x.source_epoch<baseline[(x.node,x.anchor)].source_epoch for x in packet.pose_links):
        raise ValueError("stale pose record")
    event=packet.event;owner=robust_owner or CausalRobustSharedRootOwner(static);plan=_timed("prepare.total",owner.prepare,static,root,packet,prepared_dynamic_owner)
    std=adaptive_root_minimum_std_m(len(plan.selection.trusted_nodes));obs=PositionObservation(plan.measurement_s,plan.availability_s,plan.candidate.root_position_m,
      np.eye(3)*std**2,"C2_ROBUST_SHARED_ROOT_DIAGNOSTIC",plan.candidate.anchors_used,"ROBUST_NODE_COUNT_FLOOR_ONLY_NOT_CALIBRATED_R",source_sequence=event.sequence)
    calls=0;original=tx.evaluate_candidate_transition
    def counted(*a,**k):
        nonlocal calls;calls+=1;return original(*a,**k)
    tx.evaluate_candidate_transition=counted
    try:prepared_transaction=_timed("transaction.prepare",tx.prepare_causal_update_transaction,root=root,observation=obs,kind=CandidateKind.ROOT_POSITION,
      nominal_envelope=ephemeral.nominal_envelope,dynamic_envelope=event.dynamic_envelope,activity=event.activity,
      consensus=event.consensus,contact=event.contact)
    finally:tx.evaluate_candidate_transition=original
    sidecar=None
    if prepared_transaction.result.root_committed:
      ticket=owner.prevalidate_commit(plan,root,packet)
      sidecar=tx.PreparedCausalSidecarTicket(
        owner,ticket,owner.revision,
        _validate_robust_sidecar,_apply_robust_sidecar,_rollback_robust_sidecar)
    transaction=_timed("transaction.commit",tx.commit_causal_update_transaction,
      prepared_transaction,sidecar)
    factors=plan.factors
    return PublishedResult("UWB",event.sequence,transaction.decision.reason.value,transaction.root_decision_reason,
      root.current_state.vector,root.current_state.covariance,h=tuple(x.state_jacobian for x in factors),sensor_r=tuple(x.sensor_r_m2 for x in factors),
      total_r=tuple(x.r_prior_m2 for x in factors),s=tuple(x.s_prior_m2 for x in factors),nis=tuple(x.prior_nis for x in factors),
      rank=tuple(x.rank for x in factors),condition=tuple(x.condition for x in factors),
      link_identities=tuple((link.node,link.anchor) for link in plan.links),link_count=len(plan.links),guard_calls=calls,
      diagnostic_weights=plan.weights,bias_snapshots=(plan.staged_bias_snapshots if transaction.root_committed else plan.committed_bias_snapshots),
      diagnostic_predicted=tuple(x.predicted_ranges_m for x in factors),
      authoritative_weights=tuple((link.node,link.anchor,link.information_weight) for link in plan.links),
      authoritative_mode=plan.selection.mode,authoritative_nodes=plan.selection.trusted_nodes,
      effective_r=plan.effective_r,effective_s=plan.effective_s,robust_nis=plan.robust_nis,
      external_information_weights=plan.weights,
      robust_influence_weights=tuple((node,x.anchors,x.robust_weights) for node,x in zip(plan.factor_nodes,factors)),
      irls_information_weights=tuple((node,x.anchors,x.irls_information_weights) for node,x in zip(plan.factor_nodes,factors)),
      cross_covariance_status=tuple(x.cross_covariance_status for x in factors))


DIAGNOSTIC_GROUP_PERIOD_S = 0.120048
DIAGNOSTIC_STARTUP_PERIOD_S = DIAGNOSTIC_GROUP_PERIOD_S
DIAGNOSTIC_ASSEMBLY_PERIOD_S = DIAGNOSTIC_GROUP_PERIOD_S
DIAGNOSTIC_MAXIMUM_IMU_GAP_S = 0.005005
DIAGNOSTIC_HORIZON_S = (
    DIAGNOSTIC_STARTUP_PERIOD_S
    + DIAGNOSTIC_ASSEMBLY_PERIOD_S
    + DIAGNOSTIC_MAXIMUM_IMU_GAP_S
)
DIAGNOSTIC_NATIVE200_PERIOD_S = 0.005
DIAGNOSTIC_REQUIRED_IMU_CAPACITY = math.ceil(DIAGNOSTIC_HORIZON_S / DIAGNOSTIC_NATIVE200_PERIOD_S)
DIAGNOSTIC_IMU_CAPACITY = 64


def _validate_diagnostic_horizon(committed_time_s, availability_time_s):
    if (not math.isfinite(committed_time_s) or not math.isfinite(availability_time_s)
            or availability_time_s < committed_time_s):
      raise ValueError("diagnostic horizon domain invalid")
    if availability_time_s - committed_time_s > DIAGNOSTIC_HORIZON_S:
      raise ValueError("diagnostic horizon exceeded")


class CausalRawRangeDiagnosticJournal:
    """Fixed-capacity causal U5 journal; observation-only and never feeds A."""
    def __init__(self,owner,capacity=DIAGNOSTIC_IMU_CAPACITY,horizon_s=DIAGNOSTIC_HORIZON_S,maximum_imu_gap_s=DIAGNOSTIC_MAXIMUM_IMU_GAP_S):
      if (capacity!=DIAGNOSTIC_IMU_CAPACITY or horizon_s!=DIAGNOSTIC_HORIZON_S
              or maximum_imu_gap_s!=DIAGNOSTIC_MAXIMUM_IMU_GAP_S
              or DIAGNOSTIC_REQUIRED_IMU_CAPACITY!=50 or capacity<DIAGNOSTIC_REQUIRED_IMU_CAPACITY):
        raise ValueError("diagnostic journal owner mismatch")
      self.owner=owner;self.capacity=capacity;self.horizon_s=horizon_s;self.maximum_imu_gap_s=maximum_imu_gap_s
      self.state=RootState(owner.initial_state.time_s,owner.initial_state.vector,owner.initial_state.covariance)
      self.force=np.array([0.,0.,9.80665]);self.rotation=np.eye(3);self.buffer=[];self.last_arrival=owner.initial_state.time_s
      self.trackers={node:PersistentRangeBiasTracker() for node in owner.clocks}
    def add_imu(self,event):
      event.payload.validate()
      if event.kind!="IMU" or event.availability_time_s!=event.payload.availability_time_s or event.availability_time_s<=self.last_arrival:raise ValueError("diagnostic IMU order invalid")
      if len(self.buffer)>=self.capacity:raise OverflowError("diagnostic IMU buffer full")
      self.buffer.append(event);self.last_arrival=event.availability_time_s
    def _targets(self,packet):
      prepared_owner=_prepare_dynamic_owner(self.owner,packet);owner=prepared_owner.owner;weights=prepared_owner.weights;rows=[]
      if packet.event.availability_time_s<self.last_arrival:raise ValueError("late diagnostic group")
      for row in packet.event.payload:
        clock=owner.clocks[row.node];model=ClockModel(clock.boot_epoch,clock.a_ns_per_us,clock.b_ns,0.)
        slots=tuple(anchor for anchor in range(8) if row.valid_mask&(1<<anchor) and 0<int(row.ranges_mm[anchor])<0xffff)
        epochs=np.asarray([model.seconds(float(row.strobe_us)+.5*float(row.t_round_us[anchor])) for anchor in slots],float)
        if len(epochs)<4 or not np.isfinite(epochs).all():raise ValueError("diagnostic link epochs invalid")
        rows.append((float(np.median(epochs)),row,epochs))
      rows.sort(key=lambda x:(x[0],x[1].node));times=[x[0] for x in rows]
      if times[0]<=self.state.time_s or times[-1]>packet.event.availability_time_s:raise ValueError("diagnostic measurement time invalid")
      available=[x.payload.measurement_time_s for x in self.buffer if x.availability_time_s<=packet.event.availability_time_s]
      checkpoints=[self.state.time_s,*available,packet.event.availability_time_s]
      if any(b-a>self.maximum_imu_gap_s+1e-12 for a,b in zip(checkpoints,checkpoints[1:])):raise ValueError("diagnostic IMU gap")
      _validate_diagnostic_horizon(self.state.time_s,packet.event.availability_time_s)
      return prepared_owner,rows
    def _replay(self,state,force,rotation,target):
      used=[]
      for event in self.buffer:
        if state.time_s<event.payload.measurement_time_s<=target:
          state,_=propagate_inertial(state,event.payload.measurement_time_s,force,rotation,self.owner.root_config)
          force=event.payload.specific_force_sensor_mps2.copy();rotation=event.payload.rotation_world_from_sensor.copy();used.append(event)
      state,_=propagate_inertial(state,target,force,rotation,self.owner.root_config);return state,force,rotation,used
    def process_group(self,packet,prepared=None):
      prepared_owner,rows=self._targets(packet) if prepared is None else prepared
      owner,weights=prepared_owner.owner,prepared_owner.weights
      poses={(x.node,x.anchor):x for x in packet.pose_links};factors=[];decisions=[];node_states=[]
      shadows={} if packet.b_shadow_owner is None else {x.node:x for x in packet.b_shadow_owner.snapshots}
      for reference,row,epochs in rows:
        self.state,self.force,self.rotation,replayed_events=self._replay(self.state,self.force,self.rotation,reference);pre=self.state
        valid=[anchor for anchor in range(8) if row.valid_mask&(1<<anchor) and 0<row.ranges_mm[anchor]<0xffff]
        if packet.b_shadow_owner is not None:
          shadow=shadows[row.node];root=pre.position_m+(shadow.query_global_ns*1e-9-reference)*pre.velocity_mps;snapshot=shadow.snapshot(root)
          vector=np.ones(8);shadow_weights=direct_shadow_weights_batch(node=row.node,anchor_positions_world_m=owner.anchors_m,snapshot=snapshot,geometry=packet.b_shadow_owner.geometry)
          for anchor in valid:vector[anchor]=shadow_weights[anchor]
          weights[row.node]=ExternalRangeInformationWeights(row.node,shadow.pose_global_ns*1e-9,vector,owner.range_information.provenance)
          if not weights[row.node].evidence_time_s<float(np.min(epochs)):raise ValueError("reconstructed weights are not strictly pre-link")
        tracker=self.trackers[row.node];prior=tracker.prior_snapshot(row.node,snapshot_time_s=weights[row.node].evidence_time_s)
        prior=RangeBiasPriorSnapshot(row.node,prior.snapshot_time_s,prior.mean_m+owner.anchor_delay_m+owner.tag_delay_m,prior.variance_m2,prior.last_accepted_time_s)
        geom=min((poses[(row.node,anchor)] for anchor in valid),key=lambda x:x.query_time_ns);clock=ClockModel(owner.clocks[row.node].boot_epoch,owner.clocks[row.node].a_ns_per_us,owner.clocks[row.node].b_ns,0.)
        cfg=packet.b_sigma_owner.config
        prepared_update=prepare_raw_range_update(pre,row,anchors_m=owner.anchors_m,clock=clock,bias_prior=prior,information_weights=weights[row.node],
          tag_offset_world_m=geom.offset_world_m,config=cfg)
        factor=prepared_update.factors
        updated,decision=update_raw_ranges(pre,row,anchors_m=owner.anchors_m,clock=clock,bias_prior=prior,information_weights=weights[row.node],
          tag_offset_world_m=geom.offset_world_m,config=cfg,prepared=prepared_update)
        self.state=updated
        if decision.accepted:tracker.update(row.node,decision)
        factors.append(factor);decisions.append((row.node,decision.accepted,decision.reason));node_states.append((row.node,pre.vector,pre.covariance,updated.vector,updated.covariance))
      self.buffer=[x for x in self.buffer if x.payload.measurement_time_s>self.state.time_s]
      publication,_,_,_=self._replay(self.state,self.force,self.rotation,packet.event.availability_time_s)
      latest=max(float(np.max(x.link_epochs_s)) for x in factors);query=float(np.nextafter(latest,np.inf));snap=[]
      for node in sorted(self.trackers):
        value=self.trackers[node].prior_snapshot(node,snapshot_time_s=query);snap.append((node,value.mean_m,value.variance_m2,value.last_accepted_time_s))
      return factors,tuple(decisions),tuple((row.node,weights[row.node].weights) for _,row,_ in rows),tuple(snap),tuple(node_states),publication


class DirectOwnerSequence:
    def __init__(self,owner):
      owner.validate_integrity();self.owner=owner;self.root=owner.make_root();self.robust=CausalRobustSharedRootOwner(owner)
      # Retained as an explicitly non-authoritative legacy comparator owner.
      # UWB groups no longer advance it or feed its state into the root.
      self.diagnostic=CausalRawRangeDiagnosticJournal(owner)
    def process(self,item):
      if isinstance(item,RootWorkerEvent):
        if item.kind!="IMU" or not self.root.add_imu(item.payload):raise RuntimeError("IMU rejected")
        return PublishedResult("IMU",item.sequence,None,None,self.root.current_state.vector,self.root.current_state.covariance)
      prepared=_timed("prepare.dynamic_owner",_prepare_dynamic_owner,self.owner,item)
      return _execute_group(self.owner,self.root,item,prepared,self.robust)

def _decode_and_process(engine,discriminator,blob):
    """Decode completely before entering any authoritative mutable owner."""
    value=_decode_typed_transport(discriminator,blob)
    return engine.process(value)


def _worker(static_blob,inq,outq):
    global _ACTIVE_TIMINGS
    try:
      if any(os.environ.get(k)!=v for k,v in THREAD_ENV.items()):raise RuntimeError("thread environment not frozen")
      _ACTIVE_TIMINGS={};owner=decode_static_owner(static_blob);engine=DirectOwnerSequence(owner);outq.put(("READY",owner.digest))
      count=0
      while True:
        item=inq.get()
        if item is None:break
        discriminator,blob,submitted=item;total_started=time.perf_counter_ns()
        decode_started=time.perf_counter_ns();value=_decode_typed_transport(discriminator,blob)
        _ACTIVE_TIMINGS.setdefault("transport.parse_canonical_decode",[]).append((time.perf_counter_ns()-decode_started)*1e-6)
        service=time.perf_counter_ns();result=engine.process(value);object.__setattr__(result,"service_ms",(time.perf_counter_ns()-service)*1e-6);object.__setattr__(result,"publication_lag_ms",(time.perf_counter_ns()-submitted)*1e-6)
        pickle_started=time.perf_counter_ns();payload=pickle.dumps(result,protocol=5)
        _ACTIVE_TIMINGS.setdefault("transport.result_pickle",[]).append((time.perf_counter_ns()-pickle_started)*1e-6)
        output_started=time.perf_counter_ns();outq.put(("RESULT",payload))
        _ACTIVE_TIMINGS.setdefault("transport.outq_put",[]).append((time.perf_counter_ns()-output_started)*1e-6)
        _ACTIVE_TIMINGS.setdefault("transport.total_service",[]).append((time.perf_counter_ns()-total_started)*1e-6);count+=1
      outq.put(("FINAL",{"count":count,"sentinel":True,"rss":__import__("resource").getrusage(__import__("resource").RUSAGE_SELF).ru_maxrss,
        "transport_timings_ms":{key:tuple(values) for key,values in sorted(_ACTIVE_TIMINGS.items())}}))
    except BaseException as exc:outq.put(("ERROR",f"{type(exc).__name__}: {exc}"))


class AsyncOwnerWorker:
    def __init__(self,owner,capacity=64):
      if not 1<=capacity<=64:raise ValueError("capacity")
      for k,v in THREAD_ENV.items():os.environ[k]=v
      self.owner_digest=owner.digest;blob=encode_static_owner(owner);ctx=mp.get_context("spawn");self._in=ctx.Queue(capacity);self._out=ctx.Queue(capacity)
      self._p=ctx.Process(target=_worker,args=(blob,self._in,self._out));started=time.perf_counter_ns();self._p.start();kind,value=self._out.get(timeout=30)
      if kind!="READY" or not hmac.compare_digest(value,owner.digest):self.abort();raise RuntimeError(value)
      self.cold_start_ms=(time.perf_counter_ns()-started)*1e-6;self._last=-math.inf;self._closed=False;self.hwm=0;self.submit_ms=[];self.encode_ms=[];self.put_ms=[]
    @property
    def pid(self):return self._p.pid
    def _close_queues(self):
      for value in (self._in,self._out):
        try:value.cancel_join_thread()
        except BaseException:pass
        try:value.close()
        except BaseException:pass
    def abort(self):
      try:self._in.put(None,timeout=.1)
      except BaseException:pass
      self._p.join(1.5)
      if self._p.is_alive():self._p.terminate();self._p.join(.4)
      self._close_queues()
      self._closed=True
    def submit(self,item):
      started=time.perf_counter_ns()
      try:
        encode_started=time.perf_counter_ns()
        if type(item) is RootWorkerEvent:discriminator=TRANSPORT_IMU;blob=encode_imu(item);when=item.availability_time_s
        elif type(item) is BoundGroupPacket:discriminator=TRANSPORT_GROUP;blob=encode_group(item);when=item.event.availability_time_s
        else:raise TypeError("unsupported transport item type")
        self.encode_ms.append((time.perf_counter_ns()-encode_started)*1e-6)
        if when<self._last:raise ValueError("event time reversed")
        put_started=time.perf_counter_ns();self._in.put((discriminator,blob,started),timeout=5);self.put_ms.append((time.perf_counter_ns()-put_started)*1e-6)
      except BaseException:self.abort();raise
      self._last=when;self.submit_ms.append((time.perf_counter_ns()-started)*1e-6)
      try:self.hwm=max(self.hwm,self._in.qsize())
      except NotImplementedError:pass
    def close_and_collect(self,expected,timeout=30):
      self._in.put(None,timeout=5);results=[];final=None;deadline=time.monotonic()+timeout
      try:
        while final is None:
          kind,value=self._out.get(timeout=max(.01,deadline-time.monotonic()))
          if kind=="ERROR":raise RuntimeError(value)
          if kind=="RESULT":results.append(pickle.loads(value))
          else:final=value
        self._p.join(1.5)
        if self._p.is_alive() or self._p.exitcode!=0 or len(results)!=expected:raise RuntimeError("worker drain failure")
        qsize=self._in.qsize();final.update({"exitcode":self._p.exitcode,"alive":self._p.is_alive(),"qsize":qsize});self._close_queues();self._closed=True;return results,final
      except BaseException:self.abort();raise
