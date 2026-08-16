#!/usr/bin/env python3
import argparse,hashlib,json,os,pathlib,re,subprocess,sys
PASS='PASS_FUSION_GOVERNANCE_DATA_AND_ARCHITECTURE_CONTRACT'
class ValidationError(RuntimeError):pass
def run(repo,*args,check=True,binary=False):
 p=subprocess.run(['git','-C',str(repo),*args],capture_output=True,check=check);return p.stdout if binary else p.stdout.decode()
def blob(repo,source,path):
 spec=':'+path if source=='index' else source+':'+path
 p=subprocess.run(['git','-C',str(repo),'show',spec],capture_output=True)
 if p.returncode:raise ValidationError('missing blob '+path)
 return p.stdout
def json_blob(repo,source,path):
 try:return json.loads(blob(repo,source,path))
 except Exception as e:raise ValidationError('invalid json '+path) from e
def literal(paths):
 if len(paths)!=len(set(paths)):raise ValidationError('duplicate allowlist')
 for p in paths:
  q=pathlib.PurePosixPath(p)
  if not p or p.startswith('-') or p in ('.','./') or p.endswith('/') or '..' in q.parts or any(x in p for x in ('*','?','[',':(')):raise ValidationError('nonliteral '+p)
 return paths
def allowlist(repo,impl,name):return literal([x for x in blob(repo,impl,name).decode().splitlines() if x])
def changed(repo,source,parent=None):
 if source=='index':raw=run(repo,'diff','--cached','--name-status','-z',binary=True)
 else:raw=run(repo,'diff-tree','--no-commit-id','--name-status','-r','-z',source,binary=True)
 p=raw.split(b'\0');out=[];i=0
 while i<len(p)-1:
  st=p[i].decode();path=p[i+1].decode();out.append((st,path));i+=2
 return out
def validate_pathset(actual,expected):
 if any(s!='A' for s,_ in actual):raise ValidationError('non-A path status')
 if sorted(p for _,p in actual)!=sorted(expected):raise ValidationError('allowlist path mismatch')
def validate_parent(repo,commit,parent):
 got=run(repo,'rev-list','--parents','-n','1',commit).split()
 if got!=[commit,parent]:raise ValidationError('wrong parent')
def validate_sha_sums(repo,source,paths,sha_path):
 lines=blob(repo,source,sha_path).decode().splitlines();seen={}
 for line in lines:
  if not line or line.startswith('#'):continue
  h,p=line.split('  ',1);seen[p]=h
 expected=set(paths)-{sha_path}
 if set(seen)!=expected or sha_path in seen:raise ValidationError('SHA256SUMS coverage/self exclusion')
 for p,h in seen.items():
  if hashlib.sha256(blob(repo,source,p)).hexdigest()!=h:raise ValidationError('wrong hash '+p)
def validate_manifest(repo,impl,source):
 path='BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase0/PHASE0_PATH_MANIFEST.json';m=json_blob(repo,impl,path)
 if m['paths'].get('PHASE0_PATH_MANIFEST.json')!=path:raise ValidationError('wrong manifest self path')
 for name,p in m['paths'].items():
  hits=0
  for s in (source,impl):
   try:blob(repo,s,p);hits+=1;break
   except ValidationError:pass
  if hits!=1:raise ValidationError('manifest missing '+p)
def forbidden_claims(obj):
 text=json.dumps(obj,sort_keys=True)
 if 'self_hash' in text.lower():raise ValidationError('self hash')
def validate_tracked_result(res):
 if res.get('qualification_verdict')!='PHASE0_PREPUBLICATION_QUALIFICATION_PASSED' or res.get('publication_status')!='PENDING' or PASS in json.dumps(res):raise ValidationError('illegal tracked verdict')
def validate_attestation(repo,source,impl,base,att=None):
 al='BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase0/STAGING_ALLOWLIST_PHASE0_ATTESTATION.txt';paths=allowlist(repo,impl,al);validate_pathset(changed(repo,source),paths)
 rbase='BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase0/';res=json_blob(repo,source,rbase+'PHASE0_RESULT.json');audit=json_blob(repo,source,rbase+'COMMIT_AUDIT.json')
 validate_tracked_result(res)
 if res.get('implementation_sha')!=impl or audit.get('implementation',{}).get('parent')!=base:raise ValidationError('SHA reference')
 validate_sha_sums(repo,source,paths,rbase+'SHA256SUMS.txt');validate_manifest(repo,impl,source);forbidden_claims(res);forbidden_claims(audit)
 if att:validate_parent(repo,att,impl)
 return {'paths':len(paths),'status':'PASS'}
def validate_publication(path,repo,impl,att,base,remote_ref,protected,expected_digest):
 raw=pathlib.Path(path).read_bytes();d=json.loads(raw);forbidden_claims(d)
 if d.get('final_primary_verdict')!=PASS:raise ValidationError('illegal PASS token')
 if (d.get('base_sha'),d.get('implementation_sha'),d.get('attestation_sha'))!=(base,impl,att):raise ValidationError('publication SHAs')
 validate_parent(repo,impl,base);validate_parent(repo,att,impl)
 remote=run(repo,'ls-remote','--refs','origin',remote_ref).split()
 if not remote or remote[0]!=att or d.get('live_remote_sha')!=att:raise ValidationError('publication remote SHA')
 st=run(protected,'status','--porcelain=v2','-z','--untracked-files=all',binary=True);digest=hashlib.sha256(st).hexdigest()
 if digest!=expected_digest or d.get('protected_status_digest')!=digest:raise ValidationError('protected digest')
 if d.get('phase1_status')!='NOT_STARTED':raise ValidationError('phase1 claim')
 return {'publication_bytes_sha256':hashlib.sha256(raw).hexdigest(),'remote_sha':att,'protected_digest':digest,'status':'PASS'}
def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='mode',required=True)
 for mode in ('index','commit'):
  x=sub.add_parser(mode);x.add_argument('--repo',required=True);x.add_argument('--implementation-sha',required=True);x.add_argument('--base-sha',required=True);x.add_argument('--attestation-sha')
 x=sub.add_parser('publication');x.add_argument('--repo',required=True);x.add_argument('--report',required=True);x.add_argument('--implementation-sha',required=True);x.add_argument('--attestation-sha',required=True);x.add_argument('--base-sha',required=True);x.add_argument('--remote-ref',required=True);x.add_argument('--protected',required=True);x.add_argument('--protected-digest',required=True)
 a=p.parse_args()
 try:
  if a.mode=='index':result=validate_attestation(a.repo,'index',a.implementation_sha,a.base_sha)
  elif a.mode=='commit':result=validate_attestation(a.repo,a.attestation_sha,a.implementation_sha,a.base_sha,a.attestation_sha)
  else:result=validate_publication(a.report,a.repo,a.implementation_sha,a.attestation_sha,a.base_sha,a.remote_ref,a.protected,a.protected_digest)
  print(json.dumps(result,sort_keys=True))
 except Exception as e:print(json.dumps({'status':'FAIL','error':str(e)},sort_keys=True));raise SystemExit(1)
if __name__=='__main__':main()
