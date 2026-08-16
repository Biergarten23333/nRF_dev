"""Phase 0 governance primitives; no estimator implementation."""
import json,math,pathlib
HARDWARE_IDS=("BSF31CC","BSFC2CC","BSFAA61","BSF1120","BSFB165","BSFEC35","BSF44AD","BSF3C79","BSF6C53","BSF8BC4")
FORBIDDEN=("fusion_v1.estimation.minimal","Q1_ATTITUDE_TIMELINES.npz","T4_POSITION_TIMELINES.npz","subject_calibration_v1.json","UltraInertialPoser")
def identity(ids):
 s=sorted(ids); assert s==sorted(HARDWARE_IDS) and len(set(s))==10
 return {x:{"hardware_node_id":x,"logical_role":None,"mapping_status":"UNASSIGNED"} for x in s}
def map_ns(local_us,m):
 if m["first_timer_us"]<=local_us<=m["last_timer_us"]: return round(float(m["a_ns_per_us"])*float(local_us)+float(m["b_ns"]))
 return None
def widen_u32(prev,value,epoch):
 if not 0<=value<=0xffffffff: raise ValueError("width")
 if prev is not None and value<prev: epoch+=1
 return (epoch<<32)|value,epoch
def seq_class(prev,value):
 if prev is None:return "START"
 d=(value-prev)&0xffff
 return "DUPLICATE" if d==0 else "FORWARD" if d<0x8000 else "OUT_OF_ORDER"
def validate_fixed_anchor(m):
 if m.get("endpoint_type")!="NODE_ANTENNA_TO_FIXED_ANCHOR": raise ValueError("node-to-node forbidden")
 return True
def safe_projection(doc,fields):
 mandatory={"validity","uncertainty_status","active_gauges","provenance"}
 return "REFUSED_UNREPRESENTABLE" if not mandatory.issubset(fields) else "EXACT" if fields==set(doc) else "LOSSY_WITH_MANIFEST"
def literal_allowlist(paths,root):
 root=pathlib.Path(root).resolve();out=[]
 for p in paths:
  if not p or p.startswith('-') or '..' in pathlib.PurePosixPath(p).parts or any(x in p for x in ('*','?','[',':(')) or p in ('.','./') or p.endswith('/'): raise ValueError(p)
  q=(root/p).resolve()
  if root not in q.parents: raise ValueError(p)
  out.append(p)
 return out
def reject_dependency(text):
 return not any(x in text for x in FORBIDDEN)
def convert_raw(raw,node,cfg):
 c=cfg["nodes"][node]; return raw*c["accelerometer"]["si_scale_per_lsb"],raw*c["gyroscope"]["si_scale_per_lsb"]
