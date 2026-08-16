#!/usr/bin/env python3
import argparse,json,pathlib,urllib.request
from common import dump,tool_identity
def main():
 p=argparse.ArgumentParser();p.add_argument('--registry',required=True);p.add_argument('--output',required=True);a=p.parse_args();r=json.loads(pathlib.Path(a.registry).read_text());checks=[]
 for s in r['sources']:
  ok=any(s['official_url'].startswith(x) for x in ('https://standards.ieee.org/','https://www.iso.org/','https://www.ros.org/'))
  status=None
  try:
   req=urllib.request.Request(s['official_url'],headers={'User-Agent':'Mozilla/5.0'});status=urllib.request.urlopen(req,timeout=15).status
  except Exception as e:status='UNAVAILABLE:'+type(e).__name__
  checks.append({'designation':s['designation'],'official_domain':ok,'http_status':status,'use_status':s['use_status'],'full_normative_text_reviewed':False})
 result={'tool':tool_identity(__file__),'checked_at':'2026-08-16','checks':checks,'all_official_domains':all(x['official_domain'] for x in checks),'claim':'COMPLIANCE_UNVERIFIED','secondary':['PASS_MINIMUM_STANDARD_SEMANTIC_KERNEL','STANDARD_ALIGNMENT_PROVISIONAL','NO_IEEE_COMPLIANCE_CLAIM']};dump(a.output,result);raise SystemExit(not result['all_official_domains'])
if __name__=='__main__':main()
