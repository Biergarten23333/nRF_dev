from __future__ import annotations
import argparse
from pathlib import Path
from .io.audit import run

def main() -> None:
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="command",required=True)
    a=sub.add_parser("audit"); a.add_argument("--capture",type=Path,required=True); a.add_argument("--output",type=Path,required=True)
    ns=p.parse_args()
    raw=ns.capture/"continuous_collector"/"fusion_host_raw.cobs.bin"
    result=run(raw.resolve(),ns.output.resolve())
    print(result["canonical_path"]); print(result["counts"])
if __name__ == "__main__": main()

