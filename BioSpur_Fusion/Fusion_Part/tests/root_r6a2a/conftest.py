from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest


FUSION = Path(__file__).resolve().parents[2]
SRC = FUSION / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(scope="session")
def fusion() -> Path:
    return FUSION


@pytest.fixture(scope="session")
def qualification(fusion):
    result_dir = os.environ.get("R6A2A_RESULT_DIR")
    if not result_dir:
        from biospur_fusion.root_r6a2a.qualification import run_qualification

        return run_qualification(fusion)
    root = Path(result_dir)
    return {
        "gates": json.loads((root / "QUALIFICATION_GATES.json").read_text()),
        "scenario_results": json.loads((root / "SYNTHETIC_SCENARIO_RESULTS.json").read_text())["scenarios"],
        "validator_results": json.loads((root / "FAULT_INJECTION_QUALIFICATION.json").read_text())["validator_results"],
        "covariance": json.loads((root / "COVARIANCE_QUALIFICATION.json").read_text()),
        "contracts": {
            "registry": json.loads((root / "UNIFIED_HARDWARE_REGISTRY.json").read_text()),
            "isolation": json.loads((root / "SYNTHETIC_REAL_ISOLATION_CONTRACT.json").read_text()),
            "state": json.loads((root / "STATE_AND_OWNERSHIP_CONTRACT.json").read_text()),
            "health": json.loads((root / "MEASUREMENT_HEALTH_AND_ATTRIBUTION_CONTRACT.json").read_text()),
            "modes": json.loads((root / "DEGRADED_MODE_MATRIX.json").read_text()),
            "recovery": json.loads((root / "RECOVERY_AND_REENTRY_CONTRACT.json").read_text())["contract"],
            "architecture": json.loads((root / "SYSTEM_ARCHITECTURE_CONTRACT.json").read_text()),
        },
    }
