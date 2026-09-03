"""Command-line entry point for Pure-IMU MVP-M1."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from .pipeline import run


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__); sub=parser.add_subparsers(dest="command",required=True)
    command=sub.add_parser("run"); command.add_argument("--output",type=Path)
    args=parser.parse_args()
    if args.command=="run":
        output=args.output or Path(f"/tmp/biospur_pure_imu_mvp_m1_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
        result=run(output); print(json_result(result,output))


def json_result(result: dict, output: Path) -> str:
    return f"{result['verdict']}\n{output.resolve()}"


if __name__=="__main__": main()
