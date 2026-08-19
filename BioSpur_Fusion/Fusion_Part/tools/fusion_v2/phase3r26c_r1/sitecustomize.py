"""Audit-hook tripwire for R2.6C-R1 qualification subprocesses.

This module only records and blocks filesystem opens.  It never supplies,
replaces, or mocks an input.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


_LOG_PATH = os.environ.get("R26C_R1_OPEN_LOG")
_FORBIDDEN = tuple(
    str(Path(item).resolve())
    for item in json.loads(os.environ.get("R26C_R1_FORBIDDEN_PATHS_JSON", "[]"))
)
_LOG_FD = os.open(_LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600) if _LOG_PATH else None


def _write(row: dict[str, object]) -> None:
    if _LOG_FD is not None:
        os.write(_LOG_FD, (json.dumps(row, sort_keys=True) + "\n").encode())


def _audit(event: str, args: tuple[object, ...]) -> None:
    if event != "open" or not args:
        return
    raw = args[0]
    try:
        path = str(Path(os.fsdecode(raw)).resolve()) if isinstance(raw, (str, bytes, os.PathLike)) else repr(raw)
    except (OSError, TypeError, ValueError):
        path = repr(raw)
    forbidden = next(
        (root for root in _FORBIDDEN if path == root or path.startswith(root + os.sep)),
        None,
    )
    _write({"event": event, "path": path, "forbidden_root": forbidden})
    if forbidden is not None:
        _write({"event": "R26C_R1_FORBIDDEN_OPEN", "path": path, "forbidden_root": forbidden})
        raise PermissionError(f"R2.6C-R1 qualification forbids access to {path}")


sys.addaudithook(_audit)
_write({"event": "R26C_R1_TRIPWIRE_ACTIVE", "forbidden_roots": list(_FORBIDDEN)})
