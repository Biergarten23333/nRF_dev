#!/usr/bin/env python3
import argparse,csv,gzip,hashlib,json,pathlib,struct,sys
from collections import Counter
from access import AccessGate
from common import dump,tool_identity
TOOLS=pathlib.Path(__file__).resolve().parents[3]/'B306_Part/tools';sys.path.insert(0,str(TOOLS))
from fusion_host_binary import decode_frame,FrameError
def raw_audit(gate,path):
 real,_=gate.authorize(path,"TRANSPORT_ENVELOPE_ONLY","data_audit.py");complete=crc_bad=tail=0;kinds=Counter();nodes=set();pending=bytearray()
 with open(real,"rb") as f:
  for chunk in iter(lambda:f.read(4<<20),b''):
   pending.extend(chunk)
   while True:
    j=pending.find(0)
    if j<0:break
    enc=bytes(pending[:j]);del pending[:j+1]
    if not enc:continue
    complete+=1
    try:
     frame=decode_frame(enc);kinds[str(frame.kind)]+=1;nodes.add(frame.node_name)
    except FrameError as exc:crc_bad+=1
 tail=len(pending)
 return {"complete_raw_records":complete,"crc_mismatches":crc_bad,"incomplete_eof_bytes":tail,"frame_kinds":dict(kinds),"node_set":sorted(x for x in nodes if x.startswith("BSF")),"measurement_values_decoded":False}
def canonical(gate,path):
 real,_=gate.authorize(path,"SEALED_BLIND_TRANSPORT_COUNT","data_audit.py");counts=Counter();invalid=total=0
 with gzip.open(real,"rb") as f:
  header=f.readline().rstrip(b"\r\n").decode().split(',')
  if header[7]!="measurement" or header[18]!="valid":raise RuntimeError("schema")
  for line in f:
   fields=line.rstrip(b"\r\n").split(b',');total+=1;counts[fields[7].decode()]+=1;invalid+=fields[18] not in (b'1',b'true',b'True')
 return {"header":header,"total_rows":total,"per_modality":dict(counts),"invalid_observations":invalid,"measurement_values_converted":False}
def main():
 p=argparse.ArgumentParser();p.add_argument("--allowlist",required=True);p.add_argument("--ledger",required=True);p.add_argument("--output",required=True);a=p.parse_args();gate=AccessGate(a.allowlist,a.ledger);cfg=json.loads(pathlib.Path(a.allowlist).read_text());ids=[]
 for e in cfg["entries"]:ids.append(gate.hash(e["path"],"data_audit.py"))
 by={x["realpath"]:x for x in ids};raw=raw_audit(gate,cfg["entries"][0]["path"]);can=canonical(gate,cfg["entries"][1]["path"])
 def load(path):real,_=gate.authorize(path,"METADATA_SCHEMA",'data_audit.py');return json.loads(pathlib.Path(real).read_text())
 ref=load(cfg["entries"][9]["path"]);layout=load(cfg["entries"][10]["path"]);manifest=load(cfg["entries"][11]["path"]);binding=load(cfg["entries"][12]["path"])
 geo=ref["geometry_sha256"]==by[cfg["entries"][10]["path"]]["sha256"]==manifest["layout"]["sha256"] and sorted(map(int,manifest["anchor_identity"]))==list(range(8))
 result={"tool":tool_identity(__file__),"identities":ids,"all_hashes_match":all(x["match"] for x in ids),"raw":raw,"canonical":can,"geometry_chain_complete":geo,"geometry_coordinate_contract":ref["coordinate_contract"],"historical_solver":ref["solver"],"world_scale_status":"WORLD_SCALE_EXTERNAL_METROLOGY_NOT_PROVEN","hardware_node_ids":raw["node_set"],"capture_binding_fields_quarantined":bool(binding.get("mapping"))}
 dump(a.output,result);ok=result["all_hashes_match"] and geo and len(raw["node_set"])==10 and raw["complete_raw_records"]==1234999 and raw["crc_mismatches"]==1 and raw["incomplete_eof_bytes"]==129 and can["per_modality"]=={"imu6_raw":7295015,"uwb_range":2271712} and can["invalid_observations"]==474585;raise SystemExit(not ok)
if __name__=="__main__":main()
