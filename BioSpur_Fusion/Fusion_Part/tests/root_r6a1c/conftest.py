from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


FUSION = Path(__file__).resolve().parents[2]
SOURCE = FUSION / "src"
BRIEF = Path("/home/zekaixiao/.codex/attachments/628d5d6d-1adc-4e4d-8b65-9afc4e91988d/pasted-text.txt")
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))


@pytest.fixture(scope="session")
def qualification():
    from biospur_fusion.root_r6a1c import build_contracts, resolve_sources, run_qualification

    sources = resolve_sources(FUSION, BRIEF)
    ledger = json.loads(sources["ledger"].read_text(encoding="utf-8"))
    contracts = build_contracts(ledger, sources)
    return run_qualification(ledger, contracts, protected_hashes_exact=True)
