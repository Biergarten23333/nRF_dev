from __future__ import annotations

from pathlib import Path
import sys

import pytest


FUSION = Path(__file__).resolve().parents[2]
SOURCE = FUSION / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from biospur_fusion.root_r6a1c_bsf31cc import build_addendum, run_gates  # noqa: E402


@pytest.fixture(scope="session")
def audit():
    return build_addendum(FUSION)


@pytest.fixture(scope="session")
def gates(audit):
    return run_gates(audit)
