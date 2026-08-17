#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5];sys.path.insert(0,str(ROOT/"BioSpur_Fusion/Fusion_Part/src"))
from biospur_fusion.calibration_v2.p3_probe import probe_bundle
def main():
 p=argparse.ArgumentParser();p.add_argument("--bundle",required=True);p.add_argument("--output",required=True);a=p.parse_args();r=probe_bundle(json.load(open(a.bundle)));Path(a.output).write_text(json.dumps(r,sort_keys=True,indent=2)+"\n");print(json.dumps(r,sort_keys=True))
if __name__=="__main__":main()

