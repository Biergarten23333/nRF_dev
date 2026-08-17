import copy,importlib.util,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[5];P=ROOT/"BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase1/validate_phase1.py";s=importlib.util.spec_from_file_location("p1v",P);v=importlib.util.module_from_spec(s);sys.modules[s.name]=v;s.loader.exec_module(v)
SNAP={"head":"h","index_tree":"i","status_digest":"s"};E={"implementation_sha":"i"*40,"attestation_sha":"a"*40,"branch":"feature/fusion-v2","remote":"https://github.com/Biergarten23333/nRF_dev.git","prepublication_handoff_sha256":"p"*64,"compatibility_sha256":"c"*64,"publication_report_sha256":"r"*64,"publication_report_realpath":"/e/report.json"}
def objs():
 r={"schema":"biospur-phase1-publication-v1","stage":"PHASE1",**{k:E[k] for k in ("implementation_sha","attestation_sha","branch","remote","prepublication_handoff_sha256")},"publication_status":"SUCCESS","research_verdict":v.PASS,"production_intrinsic_status":"PRODUCTION_INTRINSIC_NOT_YET_QUALIFIED","tests":{"passed":32,"failed":0,"skipped":0,"xfailed":0,"waived":0},"D1":{},"D2":{},"forbidden_counts":{"UWB":0},"D3_current":{"imu_fields_decoded":0},"D3_cumulative":{"known_incident_count":1,"D3_pristine_claim":False,"D3_status":"ACTION_LEVEL_LIMITED_HOLDOUT"},"compatibility_sha256":E["compatibility_sha256"],"protected_start":copy.deepcopy(SNAP),"protected_end":copy.deepcopy(SNAP)}
 e={"schema":"biospur-phase-handoff-envelope-v1","stage":"PHASE1","attestation_sha":E["attestation_sha"],"live_remote_sha":E["attestation_sha"],"publication_report_realpath":E["publication_report_realpath"],"publication_report_sha256":E["publication_report_sha256"],"prepublication_handoff_sha256":E["prepublication_handoff_sha256"],"protected_start":copy.deepcopy(SNAP),"protected_end":copy.deepcopy(SNAP),"publication_status":"SUCCESS"};return r,e
def reject(fn):
 r,e=objs();fn(r,e)
 with pytest.raises(v.ValidationError):v.validate_publication_objects(r,e,E)
def test_positive():r,e=objs();assert v.validate_publication_objects(r,e,E)
@pytest.mark.parametrize("k",["schema","stage","implementation_sha","attestation_sha","branch","remote","publication_status","research_verdict","production_intrinsic_status","forbidden_counts","D3_current","D3_cumulative","compatibility_sha256","protected_end"])
def test_report_mutations(k):reject(lambda r,e:r.__setitem__(k,None))
@pytest.mark.parametrize("k",["schema","stage","attestation_sha","live_remote_sha","publication_report_realpath","publication_report_sha256","protected_end","publication_status"])
def test_envelope_mutations(k):reject(lambda r,e:e.__setitem__(k,None))
