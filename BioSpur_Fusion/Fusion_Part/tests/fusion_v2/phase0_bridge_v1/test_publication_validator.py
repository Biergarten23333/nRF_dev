import copy,hashlib,importlib.util,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[5];P=ROOT/"BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase0_input_v1/validate_input_completion.py"
s=importlib.util.spec_from_file_location("bridge_validator",P);v=importlib.util.module_from_spec(s);sys.modules[s.name]=v;s.loader.exec_module(v)
COUNTERS={p:{k:0 for k in ("bytes_streamed","headers_parsed","routing_fields_decoded","imu_fields_decoded","uwb_fields_decoded","arrays_materialized","values_analyzed","estimator_consumption")} for p in ("D1","D2","D3")}
SNAP={"head":"h","index_tree":"i","status_digest":"s","diagnostic_counts":{"modified":2,"deleted":1,"untracked":3,"other":0}}
EXPECTED={"implementation_sha":"i"*40,"attestation_sha":"a"*40,"branch":"feature/fusion-imu-baseline-phase0-r2","normalized_remote":"https://github.com/Biergarten23333/nRF_dev.git","prepublication_handoff_sha256":"p"*64,"validator_source_sha256":"v"*64,"publication_report_realpath":"/e/report.json","publication_report_sha256":"r"*64,"live_remote_sha":"a"*40,"parent_chain":["0"*40,"1"*40,"i"*40,"a"*40]}
def objects():
 r={"schema":"biospur-phase0-p1-interface-publication-v1","stage":"PHASE0_TO_PHASE1_INTERFACE","phase_identity":"P0_INPUT_COMPLETION_PUBLICATION_BRIDGE","implementation_sha":EXPECTED["implementation_sha"],"attestation_sha":EXPECTED["attestation_sha"],"branch":EXPECTED["branch"],"normalized_remote":EXPECTED["normalized_remote"],"publication_status":"SUCCESS","final_local_status":v.INTERFACE_PASS,"value_views":{"D1":{"rows":800196,"sha256":"232d82435cdd35c614e1a175250f799c6a58cc18b707221a42636780f681d1aa"},"D2":{"rows":74142,"sha256":"9dbd8e41f8d0d5becd98cecf9a93e1d5edca66b091a4fff6d357e01ae30a72ea"}},"time_contexts":{"D1":{"rows":800196,"sha256":"8fec4283615018203476ea627b5e5caf0af9fccf8b928b26a28877389d71c2f0"},"D2":{"rows":74142,"sha256":"aa0bd58029cbfc51bba32bdd85bfa84c97c95a768237267d01367617395299fb"}},"time_models":"23da680767ba6a642f732d7276868174aa1b239db7d65c4a38367261ebcc3f6a","common_time_max_difference_ns":0,"access_counters":copy.deepcopy(COUNTERS),"prepublication_handoff_sha256":EXPECTED["prepublication_handoff_sha256"],"validator_source_sha256":EXPECTED["validator_source_sha256"],"parent_chain":EXPECTED["parent_chain"]}
 e={"schema":"biospur-phase-handoff-envelope-v1","stage":"PHASE0_TO_PHASE1_INTERFACE","attestation_sha":EXPECTED["attestation_sha"],"live_remote_sha":EXPECTED["live_remote_sha"],"publication_report_realpath":EXPECTED["publication_report_realpath"],"publication_report_sha256":EXPECTED["publication_report_sha256"],"protected_stage_start":copy.deepcopy(SNAP),"protected_stage_end":copy.deepcopy(SNAP),"prepublication_handoff_sha256":EXPECTED["prepublication_handoff_sha256"],"validator_source_sha256":EXPECTED["validator_source_sha256"],"publication_status":"SUCCESS"};return r,e
def reject(fn):
 r,e=objects();fn(r,e)
 with pytest.raises(v.ValidationError):v.validate_publication_objects(r,e,EXPECTED)
def test_positive():r,e=objects();assert v.validate_publication_objects(r,e,EXPECTED)
@pytest.mark.parametrize("field",["schema","stage","phase_identity","implementation_sha","attestation_sha","branch","normalized_remote","publication_status","final_local_status","value_views","time_contexts","time_models","common_time_max_difference_ns","access_counters","prepublication_handoff_sha256","validator_source_sha256","parent_chain"])
def test_report_field_mutations(field):reject(lambda r,e:r.__setitem__(field,None))
@pytest.mark.parametrize("field",["schema","stage","attestation_sha","live_remote_sha","publication_report_realpath","publication_report_sha256","protected_stage_start","protected_stage_end","prepublication_handoff_sha256","validator_source_sha256","publication_status"])
def test_envelope_field_mutations(field):reject(lambda r,e:e.__setitem__(field,None))
def test_d3_counter():reject(lambda r,e:r["access_counters"]["D3"].__setitem__("imu_fields_decoded",1))
def test_self_hash():reject(lambda r,e:r.__setitem__("self_hash","x"))
def test_protected_change():reject(lambda r,e:e["protected_stage_end"].__setitem__("head","changed"))
