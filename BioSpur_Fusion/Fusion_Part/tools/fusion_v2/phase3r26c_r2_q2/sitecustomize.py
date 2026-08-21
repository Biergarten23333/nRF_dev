"""Fail-closed audit hook for every R2.6C-R2-Q2 child process."""

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
    "/biospur_fusion/fusion_part/datasets/",
    "/biospur_fusion/fusion_part/logs/",
    "17_final_still",
    "_walk/",
    "_boxing/",
    "_golf/",
    "/vicon/",
    "/opensense/",
    "/sealed_holdout/",
    "/mnt/nrf_ssd/nrf_dev_worktrees/",
    "/tmp/biospur_",
    "/fusion-phase3r26c-r2-psi-free-20260820t124251z/",
    "/mnt/datenbankhdd/biospur_archive/fusion_worktree_cold_",
)


def _append(record: dict[str, object]) -> None:
    global _IN_HOOK
    target = os.environ.get("R26C_Q2_AUDIT_LOG")
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
                    "operation": "open",
                    "decision": "DENY",
                    "classification": "FORBIDDEN_NUMERIC_OR_PATH",
                    "trace_source": "python_audit_hook",
                    "pid": os.getpid(),
                    "timestamp_ns": time.time_ns(),
                })
                raise PermissionError(f"R26C-Q2 forbidden path access blocked: {absolute}")
            mode = args[1] if len(args) > 1 else None
            flags = args[2] if len(args) > 2 else 0
            write_requested = (
                isinstance(mode, str) and any(token in mode for token in ("w", "a", "x", "+"))
            ) or (
                isinstance(flags, int)
                and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            )
            report = os.path.abspath(os.environ.get("R26C_Q2_REPORT_ROOT", "")).lower()
            if write_requested and absolute != "/dev/null" and not (
                report and (absolute == report or absolute.startswith(report + os.sep))
            ):
                _append({
                    "event": "unauthorized_write_blocked",
                    "path": absolute,
                    "operation": "open_for_write",
                    "decision": "DENY",
                    "classification": "WRITE_OUTSIDE_REPORT_ROOT",
                    "trace_source": "python_audit_hook",
                    "pid": os.getpid(),
                    "timestamp_ns": time.time_ns(),
                })
                raise PermissionError(f"R26C-Q3 write outside report root blocked: {absolute}")
    if event in {"subprocess.Popen", "os.system"}:
        rendered = " ".join(str(item) for item in args).lower()
        if "strace" in rendered or "ptrace" in rendered:
            _append({
                "event": "nested_ptrace_blocked",
                "detail": rendered,
                "operation": "process_spawn",
                "decision": "DENY",
                "classification": "NESTED_PTRACE",
                "trace_source": "python_audit_hook",
                "pid": os.getpid(),
                "timestamp_ns": time.time_ns(),
            })
            raise PermissionError("nested strace/ptrace is forbidden")


sys.addaudithook(_audit)
os.environ["R26C_Q2_AUDIT_HOOK_ACTIVE"] = "1"
_append({
    "event": "audit_hook_active",
    "pid": os.getpid(),
    "python": sys.executable,
    "sitecustomize_origin": __file__,
    "timestamp_ns": time.time_ns(),
})
