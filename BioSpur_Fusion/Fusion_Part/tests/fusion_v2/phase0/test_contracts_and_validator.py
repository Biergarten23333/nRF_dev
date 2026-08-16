import hashlib,json,pathlib,subprocess,sys,tempfile,pytest
ROOT=pathlib.Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'tools/fusion_v2'))
from biospur_fusion.io_v2.contracts import *
import validate_phase0 as v
CFG=json.loads((ROOT/'config/fusion_v2/phase0/IMU_INPUT_CONVERSION_CONTRACT.json').read_text())
def test_identity_permutation_unknown():assert identity(HARDWARE_IDS)==identity(reversed(HARDWARE_IDS)) and all(x['logical_role'] is None for x in identity(HARDWARE_IDS).values())
@pytest.mark.parametrize('node',HARDWARE_IDS)
def test_conversion_signed(node):
 c=CFG['nodes'][node];assert 2048*c['accelerometer']['si_scale_per_lsb']==pytest.approx(9.80665) and c['axis_sign']==[1,1,1]
def test_timer_and_sequence():
 x,e=widen(0xfffffffe,2,0);assert x==(1<<32)+2 and e==1 and seq(1,1)=='DUPLICATE' and seq(65535,0)=='FORWARD' and seq(10,9)=='OUT_OF_ORDER'
def test_time_domain_host_not_api():
 m={'a_ns_per_us':1000,'b_ns':0,'first_timer_us':10,'last_timer_us':20};assert map_ns(15,m)==15000 and map_ns(9,m) is None
def test_fixed_anchor_and_projection():
 with pytest.raises(ValueError):fixed_anchor('NODE_TO_NODE')
 assert projection([])=='REFUSED_UNREPRESENTABLE'
def test_exact_crosswalk_bidirectional():
 t=json.loads((ROOT/'config/fusion_v2/phase0/REQUIREMENT_TRACEABILITY.json').read_text());assert set(t['architectures'])=={f'A-{i:02d}' for i in range(1,14)};assert set(t['invariants'])=={f'I-{i:02d}' for i in range(1,14)};assert set(t['standards'])=={f'STD-{i:02d}' for i in range(1,19)}
 for a,x in t['architectures'].items():
  assert x['resolution_phase'] and x['mandatory_evidence'] and x['primary_invariants']
  for i in x['primary_invariants']:assert a in t['invariants'][i]['architecture_refs']
def test_manifest_self_path():
 m=json.loads((ROOT/'config/fusion_v2/phase0/PHASE0_PATH_MANIFEST.json').read_text());assert m['paths']['PHASE0_PATH_MANIFEST.json']=='BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase0/PHASE0_PATH_MANIFEST.json'
def _staged_repo(tmp_path, files):
 subprocess.run(['git','init','-q',tmp_path],check=True)
 for name,data in files.items():
  p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data)
 subprocess.run(['git','-C',tmp_path,'add','--',*files],check=True)
 return tmp_path
def test_mutation_wrong_hash(tmp_path):
 repo=_staged_repo(tmp_path,{'a':'content','SHA256SUMS.txt':'0'*64+'  a\n'})
 with pytest.raises(v.ValidationError):v.validate_sha_sums(repo,'index',['a','SHA256SUMS.txt'],'SHA256SUMS.txt')
def test_mutation_wrong_parent(monkeypatch):
 monkeypatch.setattr(v,'run',lambda *a,**k:'bad parent\n')
 with pytest.raises(v.ValidationError):v.validate_parent('.', 'impl','base')
def test_mutation_missing_extra_files():
 with pytest.raises(v.ValidationError):v.validate_pathset([('A','a')],['a','b'])
 with pytest.raises(v.ValidationError):v.validate_pathset([('A','a'),('A','b')],['a'])
def test_mutation_wrong_manifest_path(monkeypatch):
 monkeypatch.setattr(v,'json_blob',lambda *a,**k:{'paths':{'PHASE0_PATH_MANIFEST.json':'wrong/path.json'}})
 with pytest.raises(v.ValidationError):v.validate_manifest('.','impl','index')
def test_mutation_staged_blob_differs_from_working_copy(tmp_path):
 bad=json.dumps({'qualification_verdict':v.PASS,'publication_status':'COMPLETE'})
 repo=_staged_repo(tmp_path,{'result.json':bad});(tmp_path/'result.json').write_text(json.dumps({'qualification_verdict':'PHASE0_PREPUBLICATION_QUALIFICATION_PASSED','publication_status':'PENDING'}))
 with pytest.raises(v.ValidationError):v.validate_tracked_result(json.loads(v.blob(repo,'index','result.json')))
def test_mutation_allowlist_nonliteral():
 with pytest.raises(v.ValidationError):v.literal(['*.json'])
def test_mutation_sha_sums_missing(tmp_path):
 h=hashlib.sha256(b'a').hexdigest();repo=_staged_repo(tmp_path,{'a':'a','b':'b','SHA256SUMS.txt':h+'  a\n'})
 with pytest.raises(v.ValidationError):v.validate_sha_sums(repo,'index',['a','b','SHA256SUMS.txt'],'SHA256SUMS.txt')
def test_mutation_publication_remote_sha(tmp_path,monkeypatch):
 report=tmp_path/'publication.json';report.write_text(json.dumps({'final_primary_verdict':v.PASS,'base_sha':'base','implementation_sha':'impl','attestation_sha':'att','live_remote_sha':'wrong','protected_status_digest':'x','phase1_status':'NOT_STARTED'}))
 monkeypatch.setattr(v,'validate_parent',lambda *a,**k:None);monkeypatch.setattr(v,'run',lambda *a,**k:'different\trefs/heads/r2\n')
 with pytest.raises(v.ValidationError):v.validate_publication(report,'.','impl','att','base','refs/heads/r2','.', 'x')
def test_mutation_self_hash():
 with pytest.raises(v.ValidationError):v.forbidden_claims({'self_hash':'x'})
def test_mutation_illegal_pass_token():
 with pytest.raises(v.ValidationError):v.validate_tracked_result({'qualification_verdict':v.PASS,'publication_status':'PENDING'})
