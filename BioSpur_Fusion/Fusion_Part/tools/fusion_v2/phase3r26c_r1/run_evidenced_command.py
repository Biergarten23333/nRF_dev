#!/usr/bin/env python3
"""Run one command under audit-hook and strace access monitoring."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--expect", choices=("pass", "fail"), default="pass")
    parser.add_argument("--forbid", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("command is required")
    out = args.evidence_dir.resolve() / args.label
    out.mkdir(parents=True, exist_ok=False)
    tool_dir = Path(__file__).resolve().parent
    open_log = out / "python_open_events.jsonl"
    strace_log = out / "strace.log"
    stdout_path = out / "stdout.txt"
    stderr_path = out / "stderr.txt"
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_ADDOPTS"] = "-p no:cacheprovider"
    env["MPLBACKEND"] = "Agg"
    env["R26C_R1_OPEN_LOG"] = str(open_log)
    env["R26C_R1_FORBIDDEN_PATHS_JSON"] = json.dumps(
        [str(path.resolve()) for path in map(Path, args.forbid)]
    )
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(tool_dir), *(filter(None, [existing]))]
    )
    wrapped = ["strace", "-f", "-qq", "-e", "trace=%file", "-o", str(strace_log), "--", *command]
    start = datetime.now(timezone.utc).isoformat()
    completed = subprocess.run(wrapped, cwd=args.cwd, env=env, capture_output=True)
    end = datetime.now(timezone.utc).isoformat()
    stdout_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)
    forbidden_roots = [str(path.resolve()) for path in map(Path, args.forbid)]
    strace_text = strace_log.read_text(errors="replace")
    open_syscalls = (" open(", " openat(", " openat2(", " creat(")
    forbidden_strace = [
        line for line in strace_text.splitlines()
        if any(call in line for call in open_syscalls)
        and any(root in line for root in forbidden_roots)
    ]
    forbidden_audit = []
    if open_log.exists():
        forbidden_audit = [
            row for row in map(json.loads, open_log.read_text().splitlines())
            if row.get("event") == "R26C_R1_FORBIDDEN_OPEN"
        ]
    expected_exit = completed.returncode == 0 if args.expect == "pass" else completed.returncode != 0
    qualified = expected_exit and not forbidden_strace and not forbidden_audit
    manifest = {
        "schema": "biospur.phase3r26c_r1.command_evidence.v1",
        "label": args.label,
        "command_argv": command,
        "command_shell_escaped": shlex.join(command),
        "wrapped_command_argv": wrapped,
        "cwd": str(args.cwd.resolve()),
        "start_utc": start,
        "end_utc": end,
        "expected": args.expect,
        "exit_code": completed.returncode,
        "stdout_sha256": sha(stdout_path),
        "stderr_sha256": sha(stderr_path),
        "strace_sha256": sha(strace_log),
        "python_open_log_sha256": sha(open_log) if open_log.exists() else None,
        "forbidden_roots": forbidden_roots,
        "forbidden_strace_events": forbidden_strace,
        "forbidden_audit_events": forbidden_audit,
        "tripwire_role": "MONITOR_AND_BLOCK_ONLY_NO_INPUT_MOCKING",
        "qualified": qualified,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"label": args.label, "exit_code": completed.returncode, "qualified": qualified}))
    return 0 if qualified else 1


if __name__ == "__main__":
    raise SystemExit(main())
