import csv,gzip,json,hashlib
from pathlib import Path
import numpy as np,pytest
from biospur_fusion.io_v2.uwb_view import generate

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def fixture(tmp_path):
 can=tmp_path/"c.csv.gz";fields=["source_file","source_record","byte_start","byte_end","record_sha256","node","anchor","measurement","value_0","value_1","value_2","value_3","value_4","value_5","units","native_time_us","common_time_us","sequence","valid","rejection_reason","parser_version","master_arrival_ms"]
 with gzip.open(can,"wt",newline="") as f:
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
  for rec,val in [(1,"123.5"),(2,"999999.25")]:
   r={k:"" for k in fields};r.update(source_record=rec,node="N0",anchor="A",measurement="uwb_range",value_0=val,units="mm");w.writerow(r)
 dtype=[("raw_record_index","<i8"),("common_time_ns","<i8"),("clock_status","u1")]
 d1=int(159485*1e9);d3=int(160500*1e9);side=tmp_path/"s.npz";np.savez(side,uwb_N0=np.array([(1,d1,1),(2,d3,1)],dtype=dtype))
 clock=tmp_path/"clock.json";clock.write_text(json.dumps({"gates":{"action_annotation_bridge":{"listener_global_us_per_host_s":1e6,"listener_global_us_intercept":0}}}))
 return can,side,clock
def test_D1_only_and_D3_poison_never_output(tmp_path):
 can,side,clock=fixture(tmp_path);out=tmp_path/"o.csv.gz";r=generate(can,side,clock,out,sha(can),sha(side));text=gzip.open(out,"rt").read();assert "123.5" in text and "999999.25" not in text and r["D3_measurement_numeric_decode"]==0
def test_wrong_hash_rejected(tmp_path):
 can,side,clock=fixture(tmp_path)
 with pytest.raises(ValueError):generate(can,side,clock,tmp_path/"o.gz","bad",sha(side))
def test_selector_overlap_rejected():
 from biospur_fusion.io_v2.uwb_view import classify
 with pytest.raises(ValueError):classify(5,{"A":[("x",0,10)],"B":[("y",0,10)]})
def test_legacy_import_absent():
 import biospur_fusion.calibration_v2.association as a
 assert not any(x in str(a.__dict__) for x in ("UltraInertialPoser","Q1 attitude","T4 position"))
