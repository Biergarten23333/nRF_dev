from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import run


def main() -> None:
    parser=argparse.ArgumentParser(); sub=parser.add_subparsers(dest="command",required=True)
    command=sub.add_parser("run"); command.add_argument("--output",type=Path,required=True)
    resume=sub.add_parser("resume"); resume.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.command=="run":
        result=run(args.output); print(result["verdict"]); print(args.output)
    elif args.command=="resume":
        result=run(args.output,resume=True); print(result["verdict"]); print(args.output)


if __name__=="__main__":main()
