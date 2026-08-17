#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5];sys.path.insert(0,str(ROOT/"BioSpur_Fusion/Fusion_Part/src"))
from biospur_fusion.io_v2.uwb_view import generate
def main():
 p=argparse.ArgumentParser();p.add_argument("--contract",required=True);p.add_argument("--clock-config",required=True);p.add_argument("--output",required=True);p.add_argument("--manifest",required=True);a=p.parse_args();c=json.load(open(a.contract));d=c["D1"]
 r=generate(d["canonical"]["realpath"],d["time_sidecar"]["realpath"],a.clock_config,a.output,d["canonical"]["sha256"],d["time_sidecar"]["sha256"]);Path(a.manifest).write_text(json.dumps(r,sort_keys=True,indent=2)+"\n");print(json.dumps({"rows":r["rows"],"sha256":r["sha256"],"D3_numeric_decode":0},sort_keys=True))
if __name__=="__main__":main()

