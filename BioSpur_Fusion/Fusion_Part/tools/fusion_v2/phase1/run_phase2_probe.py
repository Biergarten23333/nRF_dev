#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5];sys.path.insert(0,str(ROOT/"BioSpur_Fusion/Fusion_Part/src"))
from biospur_fusion.phase2.ingress_probe import probe_phase1_run
def main():
 p=argparse.ArgumentParser();p.add_argument("--run",required=True);p.add_argument("--output",required=True);a=p.parse_args();r=probe_phase1_run(json.load(open(a.run)));Path(a.output).write_text(json.dumps(r,sort_keys=True,indent=2)+"\n");print(json.dumps(r,sort_keys=True))
if __name__=="__main__":main()
