"""Canonical v1 transport for immutable asynchronous ROOT worker events."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from typing import Any

import numpy as np

from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent
from biospur_fusion.c2_uwb_root_world.causal_update_guard import (
    CausalContactTransitionEvidence,
    CausalImuActivitySummary,
    IndependentNodeConsensusEvidence,
    ReachabilityClass,
    ReachabilityEnvelope,
)
from biospur_fusion.c2_uwb_root_world.u0 import UwbRow
from biospur_fusion.root_r3.models import ImuSample


SCHEMA = "biospur.c2.root-worker-event.v1"
_TOP_KEYS = frozenset(("schema", "type", "payload", "sha256"))
_EVENT_KEYS = frozenset(("sequence", "availability_time_s", "kind", "data",
                         "dynamic_envelope", "activity", "consensus", "contact"))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("numeric value must be int or float")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("numeric value must be finite")
    return result


def _integer(value: Any, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("invalid integer domain")
    return value


def _string(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("string must be nonblank")
    return value


def _bool(value: Any) -> bool:
    if type(value) is not bool:
        raise TypeError("boolean domain requires bool")
    return value


def _array(value: Any, shape: tuple[int, ...], *, integral: bool = False) -> np.ndarray:
    if not isinstance(value, list):
        raise TypeError("array DTO must be a list")
    array = np.asarray(value)
    if array.shape != shape:
        raise ValueError(f"array DTO shape must be {shape}")
    if integral:
        if array.dtype.kind not in "iu" or array.dtype.kind == "b":
            raise TypeError("integer array required")
        result = np.asarray(array, dtype=np.int64).copy()
    else:
        result = np.asarray(array, dtype=float).copy()
        if not np.isfinite(result).all():
            raise ValueError("array DTO must be finite")
    result.setflags(write=False)
    return result


def _exact(mapping: Any, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(mapping, dict) or frozenset(mapping) != keys:
        raise ValueError("DTO key set mismatch")
    return mapping


def _imu_to_dto(value: ImuSample) -> dict[str, Any]:
    if not isinstance(value, ImuSample):
        raise TypeError("IMU payload must be ImuSample")
    value.validate()
    return {"type": "ImuSample", "measurement_time_s": _finite(value.measurement_time_s),
        "availability_time_s": _finite(value.availability_time_s),
        "specific_force_sensor_mps2": np.asarray(value.specific_force_sensor_mps2, float).tolist(),
        "rotation_world_from_sensor": np.asarray(value.rotation_world_from_sensor, float).tolist(),
        "source_sequence": _integer(value.source_sequence), "m1_valid": _bool(value.m1_valid),
        "m1_reset": _bool(value.m1_reset)}


def _imu_from_dto(value: Any) -> ImuSample:
    keys = frozenset(("type", "measurement_time_s", "availability_time_s",
        "specific_force_sensor_mps2", "rotation_world_from_sensor", "source_sequence",
        "m1_valid", "m1_reset"))
    item = _exact(value, keys)
    if item["type"] != "ImuSample": raise ValueError("unknown IMU tag")
    force = _array(item["specific_force_sensor_mps2"], (3,))
    rotation = _array(item["rotation_world_from_sensor"], (3, 3))
    result = ImuSample(_finite(item["measurement_time_s"]), _finite(item["availability_time_s"]),
        force, rotation, _integer(item["source_sequence"]), _bool(item["m1_valid"]),
        _bool(item["m1_reset"]))
    result.validate(); return result


_UWB_KEYS = frozenset(("node", "boot", "sequence", "sweep", "strobe_us", "frame_us",
    "anchor_ids", "ranges_mm", "t_round_us", "quality", "valid_mask", "identity", "node_ms"))


def _uwb_to_dto(value: UwbRow) -> dict[str, Any]:
    if not isinstance(value, UwbRow): raise TypeError("UWB payload must contain UwbRow")
    return {"node": _string(value.node), "boot": _integer(value.boot),
        "sequence": _integer(value.sequence), "sweep": _integer(value.sweep),
        "strobe_us": _integer(value.strobe_us), "frame_us": _integer(value.frame_us),
        "anchor_ids": [_integer(x) for x in value.anchor_ids],
        "ranges_mm": [_integer(x) for x in value.ranges_mm],
        "t_round_us": [_integer(x) for x in value.t_round_us],
        "quality": [_integer(x) for x in value.quality], "valid_mask": _integer(value.valid_mask),
        "identity": _integer(value.identity), "node_ms": _integer(value.node_ms)}


def _uwb_from_dto(value: Any) -> UwbRow:
    item = _exact(value, _UWB_KEYS)
    sequences = []
    for key in ("anchor_ids", "ranges_mm", "t_round_us", "quality"):
        raw = item[key]
        if not isinstance(raw, list) or len(raw) != 8: raise ValueError("UWB arrays must have length 8")
        sequences.append(tuple(_integer(x) for x in raw))
    return UwbRow(_string(item["node"]), _integer(item["boot"]), _integer(item["sequence"]),
        _integer(item["sweep"]), _integer(item["strobe_us"]), _integer(item["frame_us"]),
        *sequences, _integer(item["valid_mask"]), _integer(item["identity"]),
        _integer(item["node_ms"]))


_ENV_KEYS = frozenset(("type", "reachability_class", "maximum_root_displacement_m",
    "maximum_root_speed_change_mps", "maximum_root_implied_acceleration_mps2",
    "maximum_joint_step_rad", "maximum_joint_angular_velocity_rad_s",
    "maximum_joint_angular_acceleration_rad_s2", "maximum_evidence_age_s",
    "minimum_impulse_mps", "minimum_angular_rate_rad_s", "minimum_activity_persistence_s",
    "minimum_unique_nodes", "maximum_node_root_spread_m", "maximum_node_geometry_condition",
    "provenance"))


def _env_to_dto(value):
    if value is None: return None
    if not isinstance(value, ReachabilityEnvelope): raise TypeError("invalid envelope type")
    if value.qualification_errors(): raise ValueError("unqualified envelope")
    result = {"type": "ReachabilityEnvelope", "reachability_class": value.reachability_class.name,
              "minimum_unique_nodes": _integer(value.minimum_unique_nodes, minimum=2),
              "provenance": _string(value.provenance)}
    for key in _ENV_KEYS - {"type", "reachability_class", "minimum_unique_nodes", "provenance"}:
        result[key] = _finite(getattr(value, key))
    return result


def _env_from_dto(value):
    if value is None: return None
    item = _exact(value, _ENV_KEYS)
    if item["type"] != "ReachabilityEnvelope": raise ValueError("unknown envelope tag")
    try: kind = ReachabilityClass[item["reachability_class"]]
    except (KeyError, TypeError) as exc: raise ValueError("invalid envelope enum") from exc
    kwargs = {key: _finite(item[key]) for key in _ENV_KEYS - {
        "type", "reachability_class", "minimum_unique_nodes", "provenance"}}
    result = ReachabilityEnvelope(kind, minimum_unique_nodes=_integer(item["minimum_unique_nodes"], minimum=2),
        provenance=_string(item["provenance"]), **kwargs)
    if result.qualification_errors(): raise ValueError("unqualified envelope")
    return result


def _activity_to_dto(value):
    if value is None: return None
    if not isinstance(value, CausalImuActivitySummary): raise TypeError("invalid activity type")
    return {"type": "CausalImuActivitySummary", "measurement_time_s": _finite(value.measurement_time_s),
        "availability_time_s": _finite(value.availability_time_s),
        "window_start_time_s": _finite(value.window_start_time_s), "node_ids": list(value.node_ids),
        "latest_sample_time_s": value.latest_sample_time_s.tolist(), "impulse_mps": value.impulse_mps.tolist(),
        "angular_rate_rad_s": value.angular_rate_rad_s.tolist(), "persistence_s": value.persistence_s.tolist(),
        "provenance": _string(value.provenance)}


def _activity_from_dto(value):
    if value is None: return None
    keys=frozenset(("type","measurement_time_s","availability_time_s","window_start_time_s","node_ids",
        "latest_sample_time_s","impulse_mps","angular_rate_rad_s","persistence_s","provenance"))
    item=_exact(value,keys)
    if item["type"]!="CausalImuActivitySummary":raise ValueError("unknown activity tag")
    if not isinstance(item["node_ids"],list):raise TypeError("node IDs must be list")
    ids=tuple(_string(x) for x in item["node_ids"]); n=len(ids)
    return CausalImuActivitySummary(_finite(item["measurement_time_s"]),_finite(item["availability_time_s"]),
        _finite(item["window_start_time_s"]),ids,_array(item["latest_sample_time_s"],(n,)),
        _array(item["impulse_mps"],(n,)),_array(item["angular_rate_rad_s"],(n,)),
        _array(item["persistence_s"],(n,)),_string(item["provenance"]))


def _consensus_to_dto(value):
    if value is None:return None
    if not isinstance(value,IndependentNodeConsensusEvidence):raise TypeError("invalid consensus type")
    return {"type":"IndependentNodeConsensusEvidence","measurement_time_s":_finite(value.measurement_time_s),
        "node_ids":list(value.node_ids),"root_position_m":value.root_position_m.tolist(),
        "geometry_rank":value.geometry_rank.tolist(),"geometry_condition":value.geometry_condition.tolist(),
        "computed_before_joint_candidate":_bool(value.computed_before_joint_candidate),
        "provenance":_string(value.provenance)}


def _consensus_from_dto(value):
    if value is None:return None
    keys=frozenset(("type","measurement_time_s","node_ids","root_position_m","geometry_rank",
        "geometry_condition","computed_before_joint_candidate","provenance"));item=_exact(value,keys)
    if item["type"]!="IndependentNodeConsensusEvidence":raise ValueError("unknown consensus tag")
    if not isinstance(item["node_ids"],list):raise TypeError("node IDs must be list")
    ids=tuple(_string(x) for x in item["node_ids"]);n=len(ids)
    return IndependentNodeConsensusEvidence(_finite(item["measurement_time_s"]),ids,
        _array(item["root_position_m"],(n,3)),_array(item["geometry_rank"],(n,),integral=True),
        _array(item["geometry_condition"],(n,)),_bool(item["computed_before_joint_candidate"]),
        _string(item["provenance"]))


def _contact_to_dto(value):
    if value is None:return None
    if not isinstance(value,CausalContactTransitionEvidence):raise TypeError("invalid contact type")
    return {"type":"CausalContactTransitionEvidence","time_s":_finite(value.time_s),
        "previous_state":dict(value.previous_state),"current_state":dict(value.current_state),
        "released_sides":list(value.released_sides),"swing_sides":list(value.swing_sides),
        "provenance":_string(value.provenance)}


def _contact_from_dto(value):
    if value is None:return None
    keys=frozenset(("type","time_s","previous_state","current_state","released_sides","swing_sides","provenance"));item=_exact(value,keys)
    if item["type"]!="CausalContactTransitionEvidence":raise ValueError("unknown contact tag")
    for key in ("previous_state","current_state"):
        if not isinstance(item[key],dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in item[key].items()):
            raise TypeError("contact state map invalid")
    for key in ("released_sides","swing_sides"):
        if not isinstance(item[key],list):raise TypeError("contact sides invalid")
    return CausalContactTransitionEvidence(_finite(item["time_s"]),item["previous_state"],item["current_state"],
        tuple(_string(x) for x in item["released_sides"]),tuple(_string(x) for x in item["swing_sides"]),
        _string(item["provenance"]))


def _event_payload(event: RootWorkerEvent) -> dict[str, Any]:
    if not isinstance(event, RootWorkerEvent): raise TypeError("event type invalid")
    if event.kind == "IMU": data = _imu_to_dto(event.payload)
    elif event.kind == "UWB":
        if not isinstance(event.payload,tuple) or not event.payload: raise TypeError("UWB payload must be nonempty tuple")
        data={"type":"UwbRowTuple","rows":[_uwb_to_dto(row) for row in event.payload]}
    else: raise ValueError("unknown event kind")
    if event.kind=="IMU" and any(x is not None for x in (event.dynamic_envelope,event.activity,event.consensus,event.contact)):
        raise ValueError("IMU cannot carry corroboration")
    return {"sequence":_integer(event.sequence),"availability_time_s":_finite(event.availability_time_s),
        "kind":event.kind,"data":data,"dynamic_envelope":_env_to_dto(event.dynamic_envelope),
        "activity":_activity_to_dto(event.activity),"consensus":_consensus_to_dto(event.consensus),
        "contact":_contact_to_dto(event.contact)}


def encode_event(event: RootWorkerEvent) -> bytes:
    core={"schema":SCHEMA,"type":"RootWorkerEvent","payload":_event_payload(event)}
    document={**core,"sha256":hashlib.sha256(_canonical(core)).hexdigest()}
    return _canonical(document)


def decode_event(blob: bytes) -> RootWorkerEvent:
    if not isinstance(blob,bytes):raise TypeError("event blob must be bytes")
    try: document=json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError,json.JSONDecodeError) as exc:raise ValueError("invalid canonical JSON") from exc
    item=_exact(document,_TOP_KEYS)
    if item["schema"]!=SCHEMA or item["type"]!="RootWorkerEvent":raise ValueError("unknown schema/type")
    if not isinstance(item["sha256"],str) or len(item["sha256"])!=64:raise ValueError("invalid digest")
    core={"schema":item["schema"],"type":item["type"],"payload":item["payload"]}
    expected=hashlib.sha256(_canonical(core)).hexdigest()
    if not hmac.compare_digest(item["sha256"],expected):raise ValueError("event digest mismatch")
    payload=_exact(item["payload"],_EVENT_KEYS);kind=payload["kind"]
    if kind=="IMU":data=_imu_from_dto(payload["data"])
    elif kind=="UWB":
        data_item=_exact(payload["data"],frozenset(("type","rows")))
        if data_item["type"]!="UwbRowTuple" or not isinstance(data_item["rows"],list) or not data_item["rows"]:
            raise ValueError("invalid UWB tuple tag")
        data=tuple(_uwb_from_dto(row) for row in data_item["rows"])
    else:raise ValueError("unknown event kind")
    event=RootWorkerEvent(_integer(payload["sequence"]),_finite(payload["availability_time_s"]),kind,data,
        _env_from_dto(payload["dynamic_envelope"]),_activity_from_dto(payload["activity"]),
        _consensus_from_dto(payload["consensus"]),_contact_from_dto(payload["contact"]))
    if encode_event(event)!=blob:raise ValueError("event is not canonical byte-exact")
    return event


__all__=["SCHEMA","encode_event","decode_event"]
