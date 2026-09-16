"""Pose-private causal derivatives for the four public C2 hinges."""
from __future__ import annotations
from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import hmac
import math
import re
import struct
from typing import Any, Mapping
import numpy as np

from biospur_fusion.c2_timing_contract import (
    MAXIMUM_POSE_AGE_NS, NATIVE200_PERIOD_NS,
)

CANONICAL_HINGES = ("elbow_left", "elbow_right", "knee_left", "knee_right")
COVARIANCE_STATUS = "UNAVAILABLE_NOT_PROPAGATED"
PUBLIC_PROJECTION_OWNER = "biospur_fusion.c2_articulated_biomechanics.orientation_ik.project_hinge_corrections"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_NATIVE200_PERIOD_NS = NATIVE200_PERIOD_NS
_SOURCE_HISTORY_HORIZON_NS = int(MAXIMUM_POSE_AGE_NS) + 2 * _NATIVE200_PERIOD_NS
_SOURCE_HISTORY_CAPACITY = math.ceil(
    _SOURCE_HISTORY_HORIZON_NS / _NATIVE200_PERIOD_NS
) + 1


class HingeTemporalEvidenceError(ValueError): pass


@dataclass(frozen=True)
class ObsoleteNative200SourcePairDiagnostic:
    requested_current_timer_us: int
    requested_current_global_ns: int
    latest_current_timer_us: int
    latest_current_global_ns: int
    continuity_generation: int

    def __post_init__(self):
        values = (
            self.requested_current_timer_us, self.requested_current_global_ns,
            self.latest_current_timer_us, self.latest_current_global_ns,
            self.continuity_generation,
        )
        if (any(type(value) is not int for value in values)
                or self.continuity_generation < 0
                or self.requested_current_timer_us > self.latest_current_timer_us
                or self.requested_current_global_ns > self.latest_current_global_ns):
            raise HingeTemporalEvidenceError("OBSOLETE_NATIVE200_SOURCE_PAIR_DIAGNOSTIC_INVALID")


class ObsoleteNative200SourcePair(HingeTemporalEvidenceError):
    def __init__(self, diagnostic: ObsoleteNative200SourcePairDiagnostic):
        if not isinstance(diagnostic, ObsoleteNative200SourcePairDiagnostic):
            raise HingeTemporalEvidenceError("OBSOLETE_NATIVE200_SOURCE_PAIR_DIAGNOSTIC_INVALID")
        self.diagnostic = diagnostic
        super().__init__("OBSOLETE_NATIVE200_SOURCE_PAIR")


@dataclass(frozen=True)
class HingeTemporalRetentionContract:
    """Bound source-time history by admitted latency plus derivative lookback."""

    maximum_source_latency_ns: int
    native200_period_ns: int = _NATIVE200_PERIOD_NS
    derivative_lookback_samples: int = 2

    def __post_init__(self):
        if (
            isinstance(self.maximum_source_latency_ns, bool)
            or not isinstance(self.maximum_source_latency_ns, int)
            or self.maximum_source_latency_ns < 0
            or self.native200_period_ns != _NATIVE200_PERIOD_NS
            or self.derivative_lookback_samples != 2
        ):
            raise HingeTemporalEvidenceError("HINGE_TEMPORAL_RETENTION_CONTRACT_INVALID")

    @property
    def horizon_ns(self):
        return self.maximum_source_latency_ns + self.derivative_lookback_samples * self.native200_period_ns

    @property
    def capacity(self):
        return math.ceil(self.horizon_ns / self.native200_period_ns) + 1


DEFAULT_RETENTION_CONTRACT = HingeTemporalRetentionContract(int(MAXIMUM_POSE_AGE_NS))

class HingeTemporalValidity(str, Enum):
    QUALIFIED = "QUALIFIED"
    WARMUP_QDOT_QDDOT = "WARMUP_QDOT_QDDOT"
    WARMUP_QDDOT = "WARMUP_QDDOT"
    RESET_SEQUENCE_GAP = "RESET_SEQUENCE_GAP"
    RESET_CONTINUITY_GENERATION = "RESET_CONTINUITY_GENERATION"

def _array(value: Any) -> np.ndarray:
    result = np.asarray(value, dtype=np.float64).reshape(4).copy()
    if not np.isfinite(result).all(): raise HingeTemporalEvidenceError("HINGE_TEMPORAL_NONFINITE")
    result.setflags(write=False)
    return result

def extract_public_hinge_q_rad(projection: Mapping[str, Any]) -> np.ndarray:
    if not isinstance(projection, Mapping) or set(projection.get("joint", {})) != set(CANONICAL_HINGES):
        raise HingeTemporalEvidenceError("PUBLIC_HINGE_INVENTORY_INVALID")
    try: values = [projection["joint"][name]["post_projection_signed_deg"] for name in CANONICAL_HINGES]
    except (KeyError, TypeError) as error: raise HingeTemporalEvidenceError("PUBLIC_HINGE_Q_UNAVAILABLE") from error
    return _array(np.radians(np.asarray(values, dtype=np.float64)))

@dataclass(frozen=True)
class HingeTemporalEvidence:
    time_s: float
    q_rad: np.ndarray
    step_rad: np.ndarray | None
    qdot_rad_s: np.ndarray | None
    qddot_rad_s2: np.ndarray | None
    validity: HingeTemporalValidity
    covariance_status: str
    q_covariance_rad2: None
    derivative_covariance: None
    provenance: str
    def __post_init__(self):
        object.__setattr__(self, "q_rad", _array(self.q_rad))
        for name in ("step_rad", "qdot_rad_s", "qddot_rad_s2"):
            if getattr(self, name) is not None: object.__setattr__(self, name, _array(getattr(self, name)))
    @property
    def qualified(self): return self.validity is HingeTemporalValidity.QUALIFIED

@dataclass(frozen=True)
class _PreparedHingePublication:
    authority: object; base_owner_revision: int; pose_revision: int
    continuity_generation: int; publication_sequence: int; time_s: float
    correction_hash: str; projection_hash: str; projection_owner: str
    q_rad: np.ndarray; step_rad: np.ndarray | None; qdot_rad_s: np.ndarray | None
    qddot_rad_s2: np.ndarray | None; validity: HingeTemporalValidity
    rom_valid: bool; fk_valid: bool; covariance_status: str
    q_covariance_rad2: None; derivative_covariance: None
    source_node: str | None; source_boot_epoch: int | None
    source_timer_us: int | None; source_global_ns: int | None
    source_clock_mapping_digest: str | None
    provenance: str; digest: str

@dataclass(frozen=True)
class _HingeTemporalSnapshotToken:
    authority: object
    revision: int
    last_digest: str | None
    last_identity: tuple[int,float,int,int] | None
    history_times_s: tuple[float,...]
    history_digest: str
    evidence: HingeTemporalEvidence | None
    digest: str

@dataclass(frozen=True)
class _PreparedHingeRebase:
    authority: object
    base_revision: int
    base_last_digest: str | None
    publication: _PreparedHingePublication
    history: tuple[_PreparedHingePublication, ...]
    revision: int
    last_digest: str
    digest: str

@dataclass(frozen=True)
class _HingeTemporalRollback:
    history: tuple[_PreparedHingePublication, ...]
    last_digest: str | None
    revision: int

def _part(value: Any) -> bytes:
    if value is None: return b"N"
    if isinstance(value, bool): return b"B1" if value else b"B0"
    if isinstance(value, int): return b"I" + struct.pack("!q", value)
    if isinstance(value, float): return b"F" + struct.pack("!d", value)
    if isinstance(value, Enum): return _part(value.__class__.__name__) + _part(value.value)
    if isinstance(value, str):
        raw=value.encode(); return b"S"+struct.pack("!I",len(raw))+raw
    if isinstance(value, np.ndarray): return b"A"+value.dtype.str.encode()+struct.pack("!I",value.size)+value.tobytes()
    if isinstance(value, tuple): return b"T"+struct.pack("!I",len(value))+b"".join(_part(item) for item in value)
    raise HingeTemporalEvidenceError("UNHASHABLE_PREPARED_FIELD")

class _CausalHingeTemporalOwner:
    """Private fixed-memory owner; only CausalArticulatedPose calls it."""
    def __init__(self, retention_contract=DEFAULT_RETENTION_CONTRACT):
        if not isinstance(retention_contract, HingeTemporalRetentionContract):
            raise HingeTemporalEvidenceError("HINGE_TEMPORAL_RETENTION_CONTRACT_INVALID")
        self.__authority=object(); self.__revision=0
        self.__history: tuple[_PreparedHingePublication,...]=(); self.__last_digest=None
        self.__retention_contract=retention_contract
    def _retention_contract(self): return self.__retention_contract
    def _last_identity(self):
        if not self.__history: return None
        p=self.__history[-1]; return p.publication_sequence,p.time_s,p.continuity_generation,p.pose_revision
    def _snapshot_token(self):
        evidence=None if not self.__history else self._evidence(self.__history[-1])
        identity=self._last_identity(); history_times=tuple(item.time_s for item in self.__history)
        history_digest=hashlib.sha256(b"".join(item.digest.encode() for item in self.__history)).hexdigest()
        fields=(self.__revision,self.__last_digest,identity,history_times,history_digest)
        if evidence is not None:
            fields+= (evidence.time_s,evidence.q_rad,evidence.step_rad,evidence.qdot_rad_s,
                evidence.qddot_rad_s2,evidence.validity,evidence.covariance_status,
                evidence.q_covariance_rad2,evidence.derivative_covariance,evidence.provenance)
        digest=hashlib.sha256(b"".join(_part(value) for value in fields)).hexdigest()
        return _HingeTemporalSnapshotToken(self.__authority,self.__revision,self.__last_digest,identity,history_times,history_digest,evidence,digest)
    def _validate_snapshot_token(self, token):
        expected=self._snapshot_token()
        if isinstance(token,_HingeTemporalSnapshotToken):
            fields=(token.revision,token.last_digest,token.last_identity,token.history_times_s,token.history_digest)
            if token.evidence is not None:
                evidence=token.evidence; fields+=(evidence.time_s,evidence.q_rad,evidence.step_rad,
                    evidence.qdot_rad_s,evidence.qddot_rad_s2,evidence.validity,evidence.covariance_status,
                    evidence.q_covariance_rad2,evidence.derivative_covariance,evidence.provenance)
            supplied=hashlib.sha256(b"".join(_part(value) for value in fields)).hexdigest()
        else: supplied=""
        if (not isinstance(token,_HingeTemporalSnapshotToken) or token.authority is not self.__authority
                or token.revision!=self.__revision or token.last_digest!=self.__last_digest
                or token.last_identity!=expected.last_identity
                or token.history_times_s!=expected.history_times_s or token.history_digest!=expected.history_digest
                or not hmac.compare_digest(token.digest,supplied)
                or not hmac.compare_digest(token.digest,expected.digest)):
            raise HingeTemporalEvidenceError("STALE_HINGE_TEMPORAL_SNAPSHOT")
    def _preview_from_snapshot(self,token,**kwargs):
        """Derive one availability-time publication without committing history."""
        self._validate_snapshot_token(token)
        if self.__history and kwargs["time_s"]==self.__history[-1].time_s:
            q=extract_public_hinge_q_rad(kwargs["projection"])
            if q.tobytes()!=self.__history[-1].q_rad.tobytes():
                raise HingeTemporalEvidenceError("DUPLICATE_PUBLICATION_CONFLICT")
            return self.__history[-1],self._evidence(self.__history[-1])
        plan=self._prepare_from_pose(**kwargs)
        if plan.validity is not HingeTemporalValidity.QUALIFIED:
            raise HingeTemporalEvidenceError("HINGE_TEMPORAL_PREVIEW_UNQUALIFIED")
        return plan,self._evidence(plan)
    def _history_bytes(self): return b"".join(p.digest.encode()+p.q_rad.tobytes() for p in self.__history)
    def _state_bytes(self):
        return (_part(self.__revision)+_part(self.__last_digest)
                +b"".join(_part(row.digest)+_part(row.q_rad) for row in self.__history))
    @staticmethod
    def _validate(p):
        if not isinstance(p.validity,HingeTemporalValidity): raise HingeTemporalEvidenceError("VALIDITY_DOMAIN_INVALID")
        if type(p.rom_valid) is not bool or type(p.fk_valid) is not bool: raise HingeTemporalEvidenceError("ROM_FK_BOOL_DOMAIN_INVALID")
        if p.covariance_status!=COVARIANCE_STATUS or p.q_covariance_rad2 is not None or p.derivative_covariance is not None: raise HingeTemporalEvidenceError("COVARIANCE_HONESTY_INVALID")
        if p.projection_owner!=PUBLIC_PROJECTION_OWNER: raise HingeTemporalEvidenceError("PUBLIC_PROJECTION_OWNER_INVALID")
        if not _HASH.fullmatch(p.correction_hash) or not _HASH.fullmatch(p.projection_hash): raise HingeTemporalEvidenceError("HASH_DOMAIN_INVALID")
        if not isinstance(p.provenance,str) or not p.provenance.strip(): raise HingeTemporalEvidenceError("PROVENANCE_INVALID")
        if not math.isfinite(p.time_s): raise HingeTemporalEvidenceError("TIME_INVALID")
        for v in (p.base_owner_revision,p.pose_revision,p.continuity_generation,p.publication_sequence):
            if not isinstance(v,int) or isinstance(v,bool) or v<0: raise HingeTemporalEvidenceError("REVISION_SEQUENCE_DOMAIN_INVALID")
        _array(p.q_rad)
        for v in (p.step_rad,p.qdot_rad_s,p.qddot_rad_s2):
            if v is not None: _array(v)
    @staticmethod
    def _digest(p):
        fields=(p.base_owner_revision,p.pose_revision,p.continuity_generation,p.publication_sequence,p.time_s,p.correction_hash,p.projection_hash,p.projection_owner,p.q_rad,p.step_rad,p.qdot_rad_s,p.qddot_rad_s2,p.validity,p.rom_valid,p.fk_valid,p.covariance_status,p.q_covariance_rad2,p.derivative_covariance,p.source_node,p.source_boot_epoch,p.source_timer_us,p.source_global_ns,p.source_clock_mapping_digest,p.provenance)
        return hashlib.sha256(b"".join(_part(v) for v in fields)).hexdigest()
    def _obsolete_native200_source_pair(
        self, *, source_node, source_boot_epoch, current_timer_us,
        current_global_ns, source_clock_mapping_digest,
        continuity_generation, include_exact_latest,
    ):
        """Classify only a same-generation pair already superseded by this owner."""
        if not self.__history:
            return None
        latest = self.__history[-1]
        same_source = (
            continuity_generation == latest.continuity_generation
            and source_node == latest.source_node
            and source_boot_epoch == latest.source_boot_epoch
            and source_clock_mapping_digest == latest.source_clock_mapping_digest
        )
        complete = (
            latest.source_timer_us is not None
            and latest.source_global_ns is not None
            and current_timer_us is not None
            and current_global_ns is not None
        )
        if not same_source or not complete:
            return None
        bounded = (
            current_timer_us <= latest.source_timer_us
            and current_global_ns <= latest.source_global_ns
        )
        superseded = (
            current_timer_us < latest.source_timer_us
            or current_global_ns < latest.source_global_ns
        )
        if not bounded or not (include_exact_latest or superseded):
            return None
        return ObsoleteNative200SourcePairDiagnostic(
            current_timer_us, current_global_ns,
            latest.source_timer_us, latest.source_global_ns,
            continuity_generation,
        )
    def _prepare_from_pose(self, *, pose_revision, continuity_generation, publication_sequence, time_s, correction_hash, projection_hash, projection, rom_valid, fk_valid, source_node=None, source_boot_epoch=None, source_timer_us=None, source_global_ns=None, source_clock_mapping_digest=None):
        q=extract_public_hinge_q_rad(projection); history=self.__history; reset=None
        if history:
            prev=history[-1]
            exact_repeat = (
                publication_sequence == prev.publication_sequence
                and time_s == prev.time_s
                and continuity_generation == prev.continuity_generation
                and pose_revision == prev.pose_revision
                and correction_hash == prev.correction_hash
                and projection_hash == prev.projection_hash
                and prev.projection_owner == PUBLIC_PROJECTION_OWNER
                and source_node == prev.source_node
                and source_boot_epoch == prev.source_boot_epoch
                and source_timer_us == prev.source_timer_us
                and source_global_ns == prev.source_global_ns
                and source_clock_mapping_digest == prev.source_clock_mapping_digest
                and q.tobytes() == prev.q_rad.tobytes()
            )
            if exact_repeat:
                return prev
            obsolete = self._obsolete_native200_source_pair(
                source_node=source_node, source_boot_epoch=source_boot_epoch,
                current_timer_us=source_timer_us,
                current_global_ns=source_global_ns,
                source_clock_mapping_digest=source_clock_mapping_digest,
                continuity_generation=continuity_generation,
                include_exact_latest=publication_sequence > prev.publication_sequence,
            )
            if obsolete is not None:
                raise ObsoleteNative200SourcePair(obsolete)
            if publication_sequence == prev.publication_sequence or time_s == prev.time_s:
                raise HingeTemporalEvidenceError("DUPLICATE_PUBLICATION_CONFLICT")
            if continuity_generation<prev.continuity_generation or publication_sequence<prev.publication_sequence or time_s<prev.time_s: raise HingeTemporalEvidenceError("PUBLICATION_ORDER_REVERSED")
            if continuity_generation!=prev.continuity_generation: history=(); reset=HingeTemporalValidity.RESET_CONTINUITY_GENERATION
            elif publication_sequence!=prev.publication_sequence+1: history=(); reset=HingeTemporalValidity.RESET_SEQUENCE_GAP
            elif time_s<=prev.time_s: raise HingeTemporalEvidenceError("PUBLICATION_TIME_NOT_INCREASING")
            elif prev.source_timer_us is None and source_timer_us is not None:
                history=(); reset=HingeTemporalValidity.RESET_SEQUENCE_GAP
            elif prev.source_timer_us is not None or source_timer_us is not None:
                if (source_timer_us is None or prev.source_node!=source_node
                        or prev.source_boot_epoch!=source_boot_epoch
                        or prev.source_clock_mapping_digest!=source_clock_mapping_digest):
                    raise HingeTemporalEvidenceError("HINGE_TEMPORAL_SOURCE_GENERATION_MISMATCH")
                if source_timer_us<=prev.source_timer_us:
                    raise HingeTemporalEvidenceError("HINGE_TEMPORAL_SOURCE_CADENCE_INVALID")
                if source_timer_us-prev.source_timer_us!=5_000:
                    history=(); reset=HingeTemporalValidity.RESET_SEQUENCE_GAP
        step=qdot=qddot=None; validity=reset or HingeTemporalValidity.WARMUP_QDOT_QDDOT
        if history:
            dt1=time_s-history[-1].time_s; step=_array(np.abs(q-history[-1].q_rad)); qdot=_array((q-history[-1].q_rad)/dt1); validity=HingeTemporalValidity.WARMUP_QDDOT
            if len(history)>=2:
                dt0=history[-1].time_s-history[-2].time_s; prior=(history[-1].q_rad-history[-2].q_rad)/dt0
                qddot=_array(2*(qdot-prior)/(dt1+dt0)); validity=HingeTemporalValidity.QUALIFIED
        provenance="pose-minted exact public hinge projection; deterministic causal backward differences; covariance unavailable"
        source_fields=(source_node,source_boot_epoch,source_timer_us,source_global_ns,source_clock_mapping_digest)
        if any(value is not None for value in source_fields):
            if (not isinstance(source_node,str) or not source_node
                    or isinstance(source_boot_epoch,bool) or not isinstance(source_boot_epoch,int)
                    or isinstance(source_timer_us,bool) or not isinstance(source_timer_us,int)
                    or isinstance(source_global_ns,bool) or not isinstance(source_global_ns,int)
                    or not isinstance(source_clock_mapping_digest,str) or not _HASH.fullmatch(source_clock_mapping_digest)
                    or int(round(float(time_s)*1_000_000_000))!=source_global_ns):
                raise HingeTemporalEvidenceError("NATIVE200_SOURCE_BINDING_INVALID")
        blank=_PreparedHingePublication(self.__authority,self.__revision,pose_revision,continuity_generation,publication_sequence,float(time_s),correction_hash,projection_hash,PUBLIC_PROJECTION_OWNER,q,step,qdot,qddot,validity,rom_valid,fk_valid,COVARIANCE_STATUS,None,None,*source_fields,provenance,"")
        self._validate(blank)
        if not rom_valid or not fk_valid: raise HingeTemporalEvidenceError("ROM_OR_FK_INVALID")
        return replace(blank, digest=self._digest(blank))
    def _commit_from_pose(self,p,*,pose_revision):
        if not isinstance(p, _PreparedHingePublication):
            raise HingeTemporalEvidenceError("POSE_PRIVATE_PREPARED_PLAN_REQUIRED")
        if p.authority is not self.__authority: raise HingeTemporalEvidenceError("FOREIGN_OWNER_AUTHORITY")
        self._validate(p)
        if p.digest!=self._digest(p): raise HingeTemporalEvidenceError("PREPARED_PAYLOAD_DIGEST_INVALID")
        if p.pose_revision!=pose_revision: raise HingeTemporalEvidenceError("STALE_POSE_OR_OWNER_REVISION")
        if p.digest==self.__last_digest:
            if p.base_owner_revision+1!=self.__revision: raise HingeTemporalEvidenceError("STALE_POSE_OR_OWNER_REVISION")
            return self._evidence(p)
        if p.base_owner_revision!=self.__revision: raise HingeTemporalEvidenceError("STALE_POSE_OR_OWNER_REVISION")
        retained=() if p.validity in {HingeTemporalValidity.RESET_SEQUENCE_GAP,HingeTemporalValidity.RESET_CONTINUITY_GENERATION} else self.__history
        combined=(*retained,p)
        if p.source_global_ns is None:
            combined=combined[-self.__retention_contract.capacity:]
        else:
            cutoff=p.source_global_ns-self.__retention_contract.horizon_ns
            combined=tuple(row for row in combined if (
                row.source_global_ns is not None and row.source_global_ns>=cutoff
            ))[-self.__retention_contract.capacity:]
        self.__history=combined; self.__last_digest=p.digest; self.__revision+=1
        return self._evidence(p)
    def _direct_candidate_from_snapshot(self, token, *, pose_revision, continuity_generation,
                                        source_node, source_boot_epoch,
                                        previous_timer_us, current_timer_us,
                                        previous_global_ns, current_global_ns,
                                        source_clock_mapping_digest,
                                        correction_hash, projection_hash,
                                        projection, rom_valid, fk_valid):
        """Build candidate kinematics from authenticated t-10/t-5 history and q(t)."""
        self._validate_snapshot_token(token)
        obsolete = self._obsolete_native200_source_pair(
            source_node=source_node, source_boot_epoch=source_boot_epoch,
            current_timer_us=current_timer_us,
            current_global_ns=current_global_ns,
            source_clock_mapping_digest=source_clock_mapping_digest,
            continuity_generation=continuity_generation,
            include_exact_latest=False,
        )
        if obsolete is not None:
            raise ObsoleteNative200SourcePair(obsolete)
        if (current_timer_us-previous_timer_us!=5_000
                or not isinstance(source_node,str) or not source_node
                or not isinstance(source_clock_mapping_digest,str)
                or not _HASH.fullmatch(source_clock_mapping_digest)):
            raise HingeTemporalEvidenceError("NATIVE200_SOURCE_BINDING_INVALID")
        by_timer={row.source_timer_us:row for row in self.__history
                  if row.source_timer_us is not None and row.source_timer_us < current_timer_us}
        older=by_timer.get(previous_timer_us-5_000); prior=by_timer.get(previous_timer_us)
        if older is None or prior is None:
            raise HingeTemporalEvidenceError("HINGE_TEMPORAL_DIRECT_WARMUP")
        expected=(source_node,source_boot_epoch,source_clock_mapping_digest,continuity_generation)
        for row in (older,prior):
            actual=(row.source_node,row.source_boot_epoch,row.source_clock_mapping_digest,row.continuity_generation)
            if actual!=expected:
                raise HingeTemporalEvidenceError("HINGE_TEMPORAL_SOURCE_GENERATION_MISMATCH")
        if (prior.source_timer_us!=previous_timer_us
                or older.source_timer_us!=previous_timer_us-5_000
                or prior.source_global_ns!=previous_global_ns
                or older.correction_hash!=prior.correction_hash
                or current_global_ns<=previous_global_ns
                or older.source_global_ns is None
                or previous_global_ns<=older.source_global_ns):
            raise HingeTemporalEvidenceError("HINGE_TEMPORAL_SOURCE_CADENCE_INVALID")
        q=extract_public_hinge_q_rad(projection)
        dt1=(current_timer_us-previous_timer_us)*1e-6
        dt0=(previous_timer_us-older.source_timer_us)*1e-6
        step=_array(np.abs(q-prior.q_rad))
        qdot=_array((q-prior.q_rad)/dt1)
        prior_qdot=(prior.q_rad-older.q_rad)/dt0
        qddot=_array(2*(qdot-prior_qdot)/(dt1+dt0))
        if not rom_valid or not fk_valid:
            raise HingeTemporalEvidenceError("ROM_OR_FK_INVALID")
        return HingeTemporalEvidence(
            current_global_ns*1e-9,q,step,qdot,qddot,
            HingeTemporalValidity.QUALIFIED,COVARIANCE_STATUS,None,None,
            "candidate q(t) with authenticated native200 t-10/t-5 history; covariance unavailable",
        )
    def _prepare_rebase_from_candidate(self, token, *, pose_revision,
                                       continuity_generation, source_node,
                                       source_boot_epoch, source_timer_us,
                                       source_global_ns,
                                       source_clock_mapping_digest,
                                       correction_hash, projection_hash,
                                       q_rad, rom_valid, fk_valid):
        """Materialize an accepted estimator-gauge reset without derivatives."""
        self._validate_snapshot_token(token)
        if continuity_generation < 1:
            raise HingeTemporalEvidenceError("HINGE_TEMPORAL_REBASE_GENERATION_INVALID")
        prior_sequence=-1 if not self.__history else self.__history[-1].publication_sequence
        blank=_PreparedHingePublication(
            self.__authority,self.__revision,pose_revision,continuity_generation,
            prior_sequence+1,source_global_ns*1e-9,correction_hash,projection_hash,
            PUBLIC_PROJECTION_OWNER,_array(q_rad),None,None,None,
            HingeTemporalValidity.RESET_CONTINUITY_GENERATION,rom_valid,fk_valid,
            COVARIANCE_STATUS,None,None,source_node,source_boot_epoch,
            source_timer_us,source_global_ns,source_clock_mapping_digest,
            "accepted articulated estimator-gauge rebase at authenticated native200 source tick; derivatives intentionally reset","",
        )
        self._validate(blank)
        if not rom_valid or not fk_valid:
            raise HingeTemporalEvidenceError("ROM_OR_FK_INVALID")
        publication=replace(blank,digest=self._digest(blank))
        fields=(self.__revision,self.__last_digest,publication.digest,
                continuity_generation,pose_revision)
        digest=hashlib.sha256(b"".join(_part(value) for value in fields)).hexdigest()
        return _PreparedHingeRebase(
            self.__authority,self.__revision,self.__last_digest,publication,
            (publication,),self.__revision+1,publication.digest,digest,
        )
    def _prevalidate_rebase(self, plan):
        if not isinstance(plan,_PreparedHingeRebase) or plan.authority is not self.__authority:
            raise HingeTemporalEvidenceError("POSE_PRIVATE_REBASE_REQUIRED")
        fields=(plan.base_revision,plan.base_last_digest,plan.publication.digest,
                plan.publication.continuity_generation,plan.publication.pose_revision)
        digest=hashlib.sha256(b"".join(_part(value) for value in fields)).hexdigest()
        if (plan.base_revision!=self.__revision or plan.base_last_digest!=self.__last_digest
                or not hmac.compare_digest(plan.digest,digest)
                or plan.history!=(plan.publication,) or plan.revision!=self.__revision+1
                or plan.last_digest!=plan.publication.digest):
            raise HingeTemporalEvidenceError("STALE_HINGE_TEMPORAL_REBASE")
        self._validate(plan.publication)
        if plan.publication.digest!=self._digest(plan.publication):
            raise HingeTemporalEvidenceError("PREPARED_PAYLOAD_DIGEST_INVALID")
        return plan
    def _prepare_rebase_rollback(self):
        return _HingeTemporalRollback(self.__history,self.__last_digest,self.__revision)
    def _apply_prevalidated_rebase(self, plan):
        self.__history=plan.history
        self.__last_digest=plan.last_digest
        self.__revision=plan.revision
    def _rollback_prevalidated_rebase(self, ticket):
        self.__history=ticket.history
        self.__last_digest=ticket.last_digest
        self.__revision=ticket.revision
    @staticmethod
    def _evidence(p): return HingeTemporalEvidence(p.time_s,p.q_rad,p.step_rad,p.qdot_rad_s,p.qddot_rad_s2,p.validity,p.covariance_status,None,None,p.provenance)
