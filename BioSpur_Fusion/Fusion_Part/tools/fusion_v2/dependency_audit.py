#!/usr/bin/env python3
import argparse,ast,json,pathlib
from common import dump,tool_identity
FORBIDDEN=('fusion_v1.estimation.minimal','Q1_ATTITUDE_TIMELINES.npz','T4_POSITION_TIMELINES.npz','subject_calibration_v1.json','UltraInertialPoser')
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--output',required=True);a=p.parse_args();root=pathlib.Path(a.root);viol=[];scanned=[]
 for f in (root/'src/biospur_fusion/io_v2').rglob('*.py'):
  text=f.read_text();scanned.append(str(f.relative_to(root)));tree=ast.parse(text)
  for n in ast.walk(tree):
   if isinstance(n,(ast.Import,ast.ImportFrom)):
    names=[x.name for x in n.names] if isinstance(n,ast.Import) else [n.module or '']
    if any(any(b in x for b in FORBIDDEN) for x in names):viol.append(str(f))
 result={'tool':tool_identity(__file__),'scanned':scanned,'static_import_violations':viol,'runtime_file_denylist':list(FORBIDDEN),'production_loader_allowlist':['D1_IMU_VIEW','D2_IMU_VIEW'],'production_estimator_entrypoints':0,'call_file_coverage':'NO_ESTIMATOR_ENTRYPOINT_IN_PHASE0','legacy_adapter_output_only':'REJECTED_SCIENTIFIC_ARTIFACT'};dump(a.output,result);raise SystemExit(bool(viol))
if __name__=='__main__':main()
