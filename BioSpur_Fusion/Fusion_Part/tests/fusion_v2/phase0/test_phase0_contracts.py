import json,pathlib,pytest,sys
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/"src"))
from biospur_fusion.io_v2.contracts import *
CFG=json.loads((ROOT/"config/fusion_v2/phase0/IMU_INPUT_CONVERSION_CONTRACT.json").read_text())
def test_exact_identity_permutation_unknown():
 a=identity(HARDWARE_IDS);b=identity(reversed(HARDWARE_IDS));assert a==b and all(x["logical_role"] is None and x["mapping_status"]=="UNASSIGNED" for x in a.values())
@pytest.mark.parametrize("node",HARDWARE_IDS)
def test_integer_si_signed_axis(node):
 a,g=convert_raw(2048,node,CFG);an,gn=convert_raw(-2048,node,CFG);assert a==-an and g==-gn and a==pytest.approx(9.80665)
def test_time_domain_and_host_rejection():
 m={"first_timer_us":10,"last_timer_us":20,"a_ns_per_us":1000.0,"b_ns":0};assert map_ns(15,m)==15000 and map_ns(9,m) is None
def test_timer_wrap_seq_cases():
 x,e=widen_u32(0xfffffffe,2,0);assert x==(1<<32)+2 and e==1;assert seq_class(1,1)=="DUPLICATE" and seq_class(65535,0)=="FORWARD" and seq_class(10,9)=="OUT_OF_ORDER"
def test_fixed_anchor_rejects_node_to_node():
 with pytest.raises(ValueError):validate_fixed_anchor({"endpoint_type":"NODE_TO_NODE"})
 assert validate_fixed_anchor({"endpoint_type":"NODE_ANTENNA_TO_FIXED_ANCHOR"})
def test_projection_safety():
 d={x:1 for x in ("validity","uncertainty_status","active_gauges","provenance")};assert safe_projection(d,set())=="REFUSED_UNREPRESENTABLE" and safe_projection(d,set(d))=="EXACT"
@pytest.mark.parametrize("bad",['.','../x','dir/','*.json','-x',':(attr)x'])
def test_literal_allowlist_rejects(bad):
 with pytest.raises(ValueError):literal_allowlist([bad],ROOT)
def test_dependency_firewall():
 assert reject_dependency('safe') and not reject_dependency('fusion_v1.estimation.minimal')
def test_traceability_complete():
 t=json.loads((ROOT/"config/fusion_v2/phase0/REQUIREMENT_TRACEABILITY.json").read_text());assert len(t['architectures'])==13 and len(t['invariants'])==13 and len(t['standards'])==18
def test_no_anatomy_in_identity():
 text=json.dumps(identity(HARDWARE_IDS));assert not any(x in text for x in ('Pelvis','Wrist','Ankle','left','right'))
def test_future_activation_not_fake():
 f=json.loads((ROOT/"config/fusion_v2/phase0/FACTOR_SCHEMA.json").read_text());assert f['phase0_runtime_factor_count']==0 and f['properties']['factor_count']['minimum']==1
def test_golden_synthetic_only():
 g=json.loads((ROOT/"standards/conformance/golden_vectors/identity_refusal.json").read_text());assert not g['real_or_sealed_data'] and g['expected_projection']=='REFUSED_UNREPRESENTABLE'
def test_forbidden_profiles():
 x=json.loads((ROOT/"standards/mappings/STANDARDS_MAPPING_AUDIT.yaml").read_text());assert set(x['forbidden_profile_names'])=={'IEEE_STRICT','IEEE_BS_EXTENDED'}
