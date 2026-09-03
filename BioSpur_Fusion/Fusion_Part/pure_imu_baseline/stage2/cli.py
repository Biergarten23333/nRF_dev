"""Command-line entry point for Stage 2."""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import STAGE1_ROOT
from .pipeline import run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("run")
    command.add_argument("--stage1", type=Path, default=STAGE1_ROOT)
    command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        final = run(args.output, args.stage1)
        print(final["verdict"])


if __name__ == "__main__":
    main()
