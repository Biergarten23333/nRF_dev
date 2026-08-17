"""Strict value/context ingress for the Phase 1 raw-IMU bundle."""
from __future__ import annotations
import csv,gzip,hashlib,json
from dataclasses import dataclass
from pathlib import Path

NODES=("BSF31CC","BSFC2CC","BSFAA61","BSF1120","BSFB165","BSFEC35","BSF44AD","BSF3C79","BSF6C53","BSF8BC4")
@dataclass(frozen=True)
class ImuObservation:
 hardware_node_id:str;boot_epoch:int;raw_record_index:int;occurrence_index:int;sequence:int
 node_timer_us:int;common_time_ns:int;common_time_sigma_ns:int;accel_mps2:tuple[float,float,float];gyro_radps:tuple[float,float,float]
 split_class:str;selector_name:str;clock_mapping_ref:str;sample_age_model_ref:str;logical_role:None=None;mapping_status:str="UNASSIGNED"
def file_sha(path):
 h=hashlib.sha256()
 with open(path,"rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def iter_bundle(value_path,context_path,contract_path,models_path,expected):
 for p,key in ((value_path,"value_sha256"),(context_path,"context_sha256"),(models_path,"models_sha256")):
  if file_sha(p)!=expected[key]:raise RuntimeError("input hash mismatch "+key)
 contract=json.load(open(contract_path));models=json.load(open(models_path))["models"]
 counters={"bytes_streamed":Path(value_path).stat().st_size+Path(context_path).stat().st_size,"headers_parsed":2,"routing_fields_decoded":0,"imu_fields_decoded":0,"uwb_fields_decoded":0,"arrays_materialized":0,"values_analyzed":0,"estimator_consumption":0}
 with gzip.open(value_path,"rt",newline="") as vf,gzip.open(context_path,"rt",newline="") as cf:
  values=csv.DictReader(vf);contexts=csv.DictReader(cf)
  count=0;nodes=set()
  for count,(v,c) in enumerate(zip(values,contexts,strict=True),1):
   node=v["node"]
   if node!=c["hardware_node_id"] or int(v["source_record"])!=int(c["raw_record_index"]) or int(v["native_time_us"].split('.')[0])!=int(c["node_timer_us"]) or int(v["sidecar_common_time_ns"])!=int(c["common_time_ns"]):raise RuntimeError("value/context identity mismatch")
   ref=f"{node}/boot-{c['boot_epoch']}/segment-0"
   if ref not in models or c["time_quality_status"]!="VALID" or c["clock_mapping_valid"]!="true":raise RuntimeError("time model/ref invalid")
   cc=contract["nodes"][node];a=float(cc["accelerometer"]["si_scale_per_lsb"]);g=float(cc["gyroscope"]["si_scale_per_lsb"])
   raw=[int(v[f"value_{i}"]) for i in range(6)];counters["imu_fields_decoded"]+=6;counters["routing_fields_decoded"]+=10
   if any(x<=-32768 or x>=32767 for x in raw):raise RuntimeError("rail value in smoke bundle")
   obs=ImuObservation(node,int(c["boot_epoch"]),int(c["raw_record_index"]),int(c["occurrence_index_within_record"]),int(v["sequence"]),int(c["node_timer_us"]),int(c["common_time_ns"]),int(c["common_time_sigma_ns"]),tuple(x*a for x in raw[:3]),tuple(x*g for x in raw[3:]),c["split_class"],c["selector_name"],c["clock_mapping_ref"],c["sample_age_model_ref"])
   nodes.add(node);yield obs,counters
 if count!=expected["rows"] or nodes!=set(NODES):raise RuntimeError("row/node coverage")
