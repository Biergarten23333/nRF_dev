"""Load a 200 Hz variant of the sealed C2 pose-reset implementation.

The accepted C2 freeze binds ``pose_reset_avatar.py`` by hash, so changing that
file would invalidate the historical baseline.  This adapter therefore verifies
the sealed owner byte-for-byte and creates a separate in-memory module with only
the row-count constants converted from the legacy 20 Hz grid to 200 Hz.  The
algorithm, action windows, thresholds, and physical time spans are unchanged.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import ModuleType


OWNER_SHA256 = "be1f442907f93589fe788e45714dff8916485f936c946319738560e78d8f2bbe"
MODULE_NAME = "biospur_fusion.c2_coupled_progressive._pose_reset_avatar_native200"

_REPLACEMENTS = (
    (
        "def _qmt_window_starts(row_count: int, width: int = 40, stride: int = 20)",
        "def _qmt_window_starts(row_count: int, width: int = 400, stride: int = 200)",
        1,
    ),
    ("begin + 40", "begin + 400", 3),
    ("(end - begin) / 20.0", "(end - begin) / 200.0", 3),
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def owner_path() -> Path:
    return Path(__file__).resolve().parents[1] / "c2_coupled_progressive" / "pose_reset_avatar.py"


def transformed_source() -> tuple[str, dict[str, object]]:
    path = owner_path()
    raw = path.read_bytes()
    owner_sha = _sha256_bytes(raw)
    if owner_sha != OWNER_SHA256:
        raise RuntimeError(
            "sealed pose-reset owner changed; native-200 adapter refuses to run: "
            f"{owner_sha} != {OWNER_SHA256}"
        )
    source = raw.decode("utf-8")
    audit: list[dict[str, object]] = []
    for old, new, expected_count in _REPLACEMENTS:
        observed = source.count(old)
        if observed != expected_count:
            raise RuntimeError(
                f"native-200 replacement count changed for {old!r}: "
                f"{observed} != {expected_count}"
            )
        source = source.replace(old, new)
        audit.append({"old": old, "new": new, "count": observed})
    return source, {
        "owner_path": str(path),
        "owner_sha256": owner_sha,
        "transformed_source_sha256": _sha256_bytes(source.encode("utf-8")),
        "replacements": audit,
        "source_rate_hz": 20.0,
        "target_rate_hz": 200.0,
        "window_width_s": 2.0,
        "window_stride_s": 1.0,
    }


def load_native200_pose_reset_module() -> tuple[ModuleType, dict[str, object]]:
    source, audit = transformed_source()
    module = ModuleType(MODULE_NAME)
    module.__file__ = str(owner_path()) + "#native200"
    module.__package__ = "biospur_fusion.c2_coupled_progressive"
    sys.modules[MODULE_NAME] = module
    try:
        exec(compile(source, module.__file__, "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(MODULE_NAME, None)
        raise
    return module, audit
