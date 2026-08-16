#!/usr/bin/env python3
import argparse,hashlib,json,os,pathlib,subprocess
from common import dump,tool_identity
def status(repo):
 b=subprocess.check_output(["git","-C",repo,"status","--porcelain=v2","-z","--untracked-files=all"]);return hashlib.sha256(b).hexdigest(),len([x for x in b.split(b"\0") if x])
def main():
 p=argparse.ArgumentParser();p.add_argument("--repo",required=True);p.add_argument("--protected",required=True);p.add_argument("--expected-protected-digest",required=True);p.add_argument("--output",required=True);a=p.parse_args()
 fetch=subprocess.check_output(["git","-C",a.repo,"config","--get-all","remote.origin.url"],text=True).splitlines();push=subprocess.run(["git","-C",a.repo,"config","--get-all","remote.origin.pushurl"],text=True,capture_output=True).stdout.splitlines() or fetch
 authorized=all(x.rstrip('/').removesuffix('.git')=='https://github.com/Biergarten23333/nRF_dev' for x in fetch+push)
 if not authorized:raise SystemExit(2)
 pd,pc=status(a.protected);rd,rc=status(a.repo);head=subprocess.check_output(["git","-C",a.repo,"rev-parse","HEAD"],text=True).strip();remote=subprocess.check_output(["git","-C",a.repo,"ls-remote","--refs","origin","refs/heads/feature/fusion-imu-baseline-phase0-r2"],text=True).strip()
 result={"tool":tool_identity(__file__),"protected_digest":pd,"protected_expected":a.expected_protected_digest,"protected_records":pc,"protected_match":pd==a.expected_protected_digest,"worktree_digest":rd,"worktree_records":rc,"head":head,"detached":subprocess.run(["git","-C",a.repo,"symbolic-ref","-q","HEAD"],capture_output=True).returncode!=0,"origin_fetch_urls":fetch,"origin_push_urls":push,"remote_r2":remote or None,"remote_local_separation":True}
 dump(a.output,result);raise SystemExit(not(result["protected_match"] and result["detached"] and rc==0 and authorized))
if __name__=="__main__":main()
