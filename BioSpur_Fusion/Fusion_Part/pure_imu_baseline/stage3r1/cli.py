from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import refresh_manifest, run


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("run"); command.add_argument("--output", type=Path, required=True)
    refresh = sub.add_parser("refresh-manifest"); refresh.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        result = run(args.output); print(result["verdict"]); print(args.output)
    else:
        refresh_manifest(args.output); print(args.output)


if __name__ == "__main__":
    main()
