from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import run


def main() -> None:
    parser = argparse.ArgumentParser(description="BioSpur three-capture pure-IMU baseline")
    sub = parser.add_subparsers(dest="command", required=True)
    execute = sub.add_parser("run"); execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--no-render", action="store_true")
    args = parser.parse_args()
    if args.command == "run":
        result = run(args.output.resolve(), render=not args.no_render)
        print(result["verdict"])


if __name__ == "__main__":
    main()
