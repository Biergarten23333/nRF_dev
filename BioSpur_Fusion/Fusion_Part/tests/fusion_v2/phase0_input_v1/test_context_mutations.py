import importlib.util,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[5]
P=ROOT/"BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase0_input_v1/validate_input_completion.py"
s=importlib.util.spec_from_file_location("input_validator",P);v=importlib.util.module_from_spec(s);sys.modules[s.name]=v;s.loader.exec_module(v)
NODES=sorted(v.NODES)
MODELS={f"{n}/boot-0/segment-0":{"mapping_valid":True,"valid_timer_domain_us":[1,1000],"sample_age_model":{"support_us":[0,5000],"distribution":"UNKNOWN_BOUNDED","fixed_delay_forbidden":True}} for n in NODES}
HASHES={"source_view_sha256":"v","source_time_sidecar_sha256":"s","source_time_ledger_sha256":"l"}
def rows():
 return [{"view_row_index":str(i),"split_class":"D1","selector_name":"still","hardware_node_id":n,"raw_record_index":str(i),"node_timer_us":"10","sequence":"1","boot_epoch":"0","clock_mapping_valid":"true","clock_uncertainty_model_ref":"m","sample_age_model_ref":"a",**HASHES} for i,n in enumerate(NODES)]
def ok(r=None,m=None,times=None):return v.validate_records(r or rows(),m or MODELS,"D1",10,HASHES,iter(times) if times is not None else None)
def reject(mut):
 r=rows();mut(r)
 with pytest.raises(v.ValidationError):ok(r)
def test_baseline():assert ok()
def test_wrong_boot():reject(lambda r:r[0].__setitem__("boot_epoch","9"))
def test_missing_join():reject(lambda r:r.pop())
def test_duplicate_join():reject(lambda r:r.__setitem__(1,{**r[0],"view_row_index":"1"}))
def test_one_ns():
 r=rows()
 for x in r:x["common_time_ns"]="100"
 with pytest.raises(v.ValidationError):ok(r,times=[101]+[100]*9)
def test_timer_domain():reject(lambda r:r[0].__setitem__("node_timer_us","1001"))
def test_missing_uncertainty():reject(lambda r:r[0].__setitem__("sample_age_model_ref",""))
def test_fixed_sample_age():
 m={k:{**x,"sample_age_model":{**x["sample_age_model"],"distribution":"FIXED"}} for k,x in MODELS.items()}
 with pytest.raises(v.ValidationError):ok(m=m)
def test_reorder():reject(lambda r:r.reverse())
def test_source_hash():reject(lambda r:r[0].__setitem__("source_view_sha256","bad"))
def test_d3():reject(lambda r:r[0].__setitem__("selector_name","D3/golf"))
