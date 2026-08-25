from __future__ import annotations

from pathlib import Path
import sys

import pytest


FUSION_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = FUSION_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


@pytest.fixture(scope="session")
def qualification():
    from qualification import run_qualification

    return run_qualification()
