#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def atomic_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def append_ledger(path: Path, payload: dict) -> None:
    line = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--completed", action="append", default=[])
    parser.add_argument("--binding", action="append", default=[])
    args = parser.parse_args()
    state = json.loads(args.checkpoint.read_text())
    state["status"] = args.status
    state["checkpoint_sequence"] = int(state.get("checkpoint_sequence", 0)) + 1
    completed = list(state.get("completed_task_ids", []))
    for item in args.completed:
        if item not in completed:
            completed.append(item)
    state["completed_task_ids"] = completed
    for item in args.binding:
        key, value = item.split("=", 1)
        state[key] = value
    atomic_json(args.checkpoint, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
