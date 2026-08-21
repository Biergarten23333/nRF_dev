from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


RUN_UTC = "20260820T200136Z"
BASE_COMMIT = "7cc4be6ab7a8dbb6ac7e8eaf363ac34491fb876d"
BASE_TREE = "f34ab2bd0b6937f99cc17adeb4b783f60feccda6"
CANONICAL_BRANCH = "feature/b306-bringup"
CANONICAL_HEAD = "9480b3f4c620fe13aaae5dc127e8105393d6d392"
CANONICAL_TREE = "41cf15990945863783718d0706e6a64a66e6ff04"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def roots_from_tool(path: Path) -> tuple[Path, Path, Path]:
    resolved = path.resolve()
    fusion_root = resolved.parents[3]
    worktree_root = resolved.parents[5]
    report_root = fusion_root / (
        "reports/fusion_v2/phase3r26c_r2/"
        f"phase3r26c_r2_q7_canonical_{RUN_UTC}"
    )
    return worktree_root, fusion_root, report_root


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
