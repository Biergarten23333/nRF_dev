from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


FUSION = Path(__file__).resolve().parents[2]
SOURCE = FUSION / "src"
BRIEF = Path("/home/zekaixiao/.codex/attachments/bea65fa4-48e2-4e7a-a765-b44d2dd83868/pasted-text.txt")
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))


@pytest.fixture(scope="session")
def qualification():
    from biospur_fusion.root_r6a1b import build_contracts, resolve_sources, run_synthetic_qualification

    sources = resolve_sources(FUSION, BRIEF)
    ledger = json.loads(sources["ledger"].read_text(encoding="utf-8"))
    contracts = build_contracts(ledger, FUSION, sources)
    return run_synthetic_qualification(ledger, contracts)
