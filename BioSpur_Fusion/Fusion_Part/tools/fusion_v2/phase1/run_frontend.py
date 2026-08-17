#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5];sys.path.insert(0,str(ROOT/"BioSpur_Fusion/Fusion_Part/src"))
from biospur_fusion.imu_frontend_v2.runner import run_partition
def main():
 p=argparse.ArgumentParser();p.add_argument("--input-contract",required=True);p.add_argument("--partition",choices=("D1","D2"),required=True);p.add_argument("--output",required=True);a=p.parse_args();r=run_partition(a.input_contract,a.partition);Path(a.output).write_text(json.dumps(r,sort_keys=True,indent=2)+"\n");print(json.dumps({"partition":a.partition,"rows":r["rows"],"states":len(r["states"]),"sha":r["output_stream_sha256"]},sort_keys=True))
if __name__=="__main__":main()
