"""Qualification audit hook for the R2.6C-R2 isolated worktree."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time


_IN_HOOK = False
_FORBIDDEN_FRAGMENTS = (
    "/tests/integration/test_current_capture_result.py",
    "/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2",
    "/00_initial_still/",
    "/17_final_still/",
    "/walk/",
    "/boxing/",
    "/golf/",
    "/vicon/",
    "/opensense/",
    "/sealed_holdout/",
)


def _append(record: dict[str, object]) -> None:
    global _IN_HOOK
    target = os.environ.get("R26C_AUDIT_LOG")
    if not target or _IN_HOOK:
        return
    _IN_HOOK = True
    try:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    finally:
        _IN_HOOK = False


def _path_text(value: object) -> str | None:
    if isinstance(value, int):
        return None
    if isinstance(value, bytes):
        return os.fsdecode(value)
    if isinstance(value, (str, Path)):
        return os.fspath(value)
    return None


def _audit(event: str, args: tuple[object, ...]) -> None:
    if _IN_HOOK:
        return
    if event == "open" and args:
        path = _path_text(args[0])
        if path is not None:
            absolute = os.path.abspath(path).lower()
            if any(fragment in absolute for fragment in _FORBIDDEN_FRAGMENTS):
                _append({
                    "event": "forbidden_open_blocked",
                    "path": absolute,
                    "pid": os.getpid(),
                    "timestamp_ns": time.time_ns(),
                })
                raise PermissionError(f"R26C forbidden path access blocked: {absolute}")
    if event in {"subprocess.Popen", "os.system"}:
        rendered = " ".join(str(item) for item in args).lower()
        if "strace" in rendered or "ptrace" in rendered:
            _append({
                "event": "nested_ptrace_blocked",
                "detail": rendered,
                "pid": os.getpid(),
                "timestamp_ns": time.time_ns(),
            })
            raise PermissionError("nested strace/ptrace is forbidden")


sys.addaudithook(_audit)
os.environ["R26C_AUDIT_HOOK_ACTIVE"] = "1"
_append({
    "event": "audit_hook_active",
    "pid": os.getpid(),
    "python": sys.executable,
    "timestamp_ns": time.time_ns(),
})

