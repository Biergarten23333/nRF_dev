#!/usr/bin/env python3
import argparse,hashlib,json,os,pathlib,subprocess,sys
from common import dump,sha,tool_identity
def cmd(args,log):
 p=subprocess.run(args,text=True,capture_output=True);pathlib.Path(log).write_text(p.stdout+p.stderr);return p.returncode
def main():
 p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--evidence',required=True);p.add_argument('--implementation-sha',required=True);p.add_argument('--protected-digest',required=True);a=p.parse_args();repo=pathlib.Path(a.repo);ev=pathlib.Path(a.evidence);ev.mkdir(parents=True,exist_ok=True);tools=repo/'BioSpur_Fusion/Fusion_Part/tools/fusion_v2';cfg=repo/'BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase0';ledger=ev/'PHASE0_DATA_ACCESS_LEDGER.jsonl';allow=cfg/'DATA_READ_ALLOWLIST.json'
 commands=[]
 def run(name,args):
  log=ev/(name+'.log');rc=cmd(args,log);commands.append({'name':name,'command':args,'returncode':rc,'log':str(log),'log_sha256':sha(log)});return rc
 rc=[]
 rc.append(run('pytest',['pytest','-q','-p','no:cacheprovider',str(repo/'BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase0')]))
 rc.append(run('preflight',[sys.executable,str(tools/'preflight.py'),'--repo',str(repo),'--protected','/mnt/nrf_ssd/nRF_dev','--expected-protected-digest',a.protected_digest,'--output',str(ev/'PREFLIGHT_REPORT.json')]))
 rc.append(run('data_audit',[sys.executable,str(tools/'data_audit.py'),'--allowlist',str(allow),'--ledger',str(ledger),'--output',str(ev/'DATA_IDENTITY_REPORT.json')]))
 rc.append(run('sealed_extract',[sys.executable,str(tools/'sealed_extract.py'),'--allowlist',str(allow),'--ledger',str(ledger),'--output-dir',str(ev/'views'),'--report',str(ev/'SEALED_EXTRACTION_REPORT.json')]))
 rc.append(run('clock_replay',[sys.executable,str(tools/'clock_replay.py'),'--allowlist',str(allow),'--ledger-log',str(ledger),'--output',str(ev/'CLOCK_EQUIVALENCE_REPORT.json')]))
 rc.append(run('dependency',[sys.executable,str(tools/'dependency_audit.py'),'--root',str(repo/'BioSpur_Fusion/Fusion_Part'),'--output',str(ev/'DEPENDENCY_AUDIT.json')]))
 rc.append(run('standards',[sys.executable,str(tools/'standards_audit.py'),'--registry',str(repo/'BioSpur_Fusion/Fusion_Part/standards/registry/standards_registry.yaml'),'--output',str(ev/'STANDARDS_AUDIT.json')]))
 registry=json.loads((cfg/'MANDATORY_GATE_REGISTRY.json').read_text());data=json.loads((ev/'DATA_IDENTITY_REPORT.json').read_text()) if (ev/'DATA_IDENTITY_REPORT.json').exists() else {};seal=json.loads((ev/'SEALED_EXTRACTION_REPORT.json').read_text()) if (ev/'SEALED_EXTRACTION_REPORT.json').exists() else {};clock=json.loads((ev/'CLOCK_EQUIVALENCE_REPORT.json').read_text()) if (ev/'CLOCK_EQUIVALENCE_REPORT.json').exists() else {};dep=json.loads((ev/'DEPENDENCY_AUDIT.json').read_text()) if (ev/'DEPENDENCY_AUDIT.json').exists() else {};std=json.loads((ev/'STANDARDS_AUDIT.json').read_text()) if (ev/'STANDARDS_AUDIT.json').exists() else {}
 common_ok=not any(rc);results=[]
 for g in registry['gates']:
  actual='PASS' if common_ok else 'FAIL';evidence=[]
  if g['id'] in ('hash_count_schema_match','exact_ten_ids'):actual='PASS' if data.get('all_hashes_match') and len(data.get('hardware_node_ids',[]))==10 else 'FAIL';evidence=['DATA_IDENTITY_REPORT.json']
  elif g['id']=='D3_selector_rejection':actual='PASS' if seal.get('D3_measurement_values_decoded') is False and seal.get('counts',{}).get('D3_rejected_before_value_decode',0)>0 else 'FAIL';evidence=['SEALED_EXTRACTION_REPORT.json']
  elif g['id']=='common_clock_no_refit_exact':actual='PASS' if clock.get('max_ledger_difference_ns')==0 and clock.get('max_sidecar_difference_ns')==0 else 'FAIL';evidence=['CLOCK_EQUIVALENCE_REPORT.json']
  elif g['group']=='architecture_dependency':actual='PASS' if not dep.get('static_import_violations') else 'FAIL';evidence=['DEPENDENCY_AUDIT.json','pytest.log']
  elif g['group']=='standards':actual='PASS' if std.get('claim')=='COMPLIANCE_UNVERIFIED' else 'FAIL';evidence=['STANDARDS_AUDIT.json','pytest.log']
  else:evidence=['pytest.log','PREFLIGHT_REPORT.json']
  results.append({**g,'actual':actual,'evidence':evidence})
 out={'tool':tool_identity(__file__),'implementation_sha':a.implementation_sha,'commands':commands,'gates':results,'totals':{'pass':sum(x['actual']=='PASS' for x in results),'fail':sum(x['actual']!='PASS' for x in results),'skip':0,'xfail':0,'waive':0,'ignored':0},'qualification_verdict':'PHASE0_PREPUBLICATION_QUALIFICATION_PASSED' if all(x['actual']=='PASS' for x in results) else 'FAIL_PHASE0_TESTS'};dump(ev/'CONTRACT_TEST_REPORT.json',out);raise SystemExit(out['qualification_verdict']!='PHASE0_PREPUBLICATION_QUALIFICATION_PASSED')
if __name__=='__main__':main()
