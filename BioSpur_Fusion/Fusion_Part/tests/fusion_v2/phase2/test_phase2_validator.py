import copy,pytest
from biospur_fusion.calibration_v2.association import ROLES
from importlib.util import spec_from_file_location,module_from_spec
from pathlib import Path
P=Path(__file__).resolve().parents[3]/"tools/fusion_v2/phase2/validate_phase2.py";s=spec_from_file_location("p2v",P);v=module_from_spec(s);s.loader.exec_module(v)
I="i"*40;A="a"*40;H="h"*64;PRO={"head":"h","index_tree":"i","status_digest":"s"}
E={"implementation_sha":I,"attestation_sha":A,"branch":"feature/fusion-v2","remote":"https://github.com/Biergarten23333/nRF_dev.git","prepublication_handoff_sha256":H,"P3_probe_sha256":"p"*64,"target_capture_package_sha256":"t"*64,"publication_report_realpath":"/x/report.json","publication_report_sha256":"r"*64}
R={"schema":"biospur-phase2-publication-v1","stage":"PHASE2","implementation_sha":I,"attestation_sha":A,"branch":E["branch"],"remote":E["remote"],"live_remote_sha":A,"publication_status":"SUCCESS","stage_verdict":v.VERDICT,"tests":{},"D1_access":{},"D2_access":{},"D3_current":{"imu":0,"uwb":0,"arrays":0,"factors":0},"D3_cumulative":{"known_incident_count":1,"D3_pristine_claim":False,"D3_status":"ACTION_LEVEL_LIMITED_HOLDOUT"},"protected_start":PRO,"protected_end":PRO,"prepublication_handoff_sha256":H,"P3_probe_sha256":"p"*64,"target_capture_package_sha256":"t"*64,"phase3_started":False}
V={"schema":"biospur-phase-handoff-envelope-v1","stage":"PHASE2","attestation_sha":A,"live_remote_sha":A,"publication_report_realpath":"/x/report.json","publication_report_sha256":"r"*64,"prepublication_handoff_sha256":H,"protected_start":PRO,"protected_end":PRO,"publication_status":"SUCCESS"}
def reject(mut):
 r=copy.deepcopy(R);e=copy.deepcopy(V);mut(r,e)
 with pytest.raises(v.ValidationError):v.validate_publication_objects(r,e,E)
def test_publication_positive():assert v.validate_publication_objects(R,V,E)
@pytest.mark.parametrize("k",["schema","stage","implementation_sha","attestation_sha","branch","remote","live_remote_sha","publication_status","stage_verdict","D3_current","D3_cumulative","protected_end","prepublication_handoff_sha256","P3_probe_sha256","target_capture_package_sha256","phase3_started"])
def test_report_mutations(k):reject(lambda r,e:r.__setitem__(k,None))
@pytest.mark.parametrize("k",["schema","stage","attestation_sha","live_remote_sha","publication_report_realpath","publication_report_sha256","protected_end","publication_status"])
def test_envelope_mutations(k):reject(lambda r,e:e.__setitem__(k,None))
def test_self_hash_rejected():reject(lambda r,e:r.__setitem__("self_hash","x"))
def test_future_sha_rejected():reject(lambda r,e:e.__setitem__("future_sha","x"))
def test_nonzero_D3_rejected():reject(lambda r,e:r["D3_current"].__setitem__("imu",1))
