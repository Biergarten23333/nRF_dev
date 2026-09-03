"""Command-line entry point for the approved synthetic qualification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .joint_type_stage import run_joint_type_stage
from .synthetic_axis_stage import run_axis_stage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("axis-stage", "joint-type-stage"))
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "axis-stage":
        result = run_axis_stage(args.repo_root.resolve(), args.output.resolve())
    elif args.command == "joint-type-stage":
        result = run_joint_type_stage(args.repo_root.resolve(), args.output.resolve())
    else:
        raise AssertionError(args.command)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
