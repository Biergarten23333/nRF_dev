from __future__ import annotations

from pathlib import Path
import sys

import pytest


FUSION_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = FUSION_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


@pytest.fixture(scope="session")
def model():
    from biospur_fusion.root_r6a0.body import load_body_model
    return load_body_model(FUSION_ROOT / "config/root_r6a0/body_graph.json")


@pytest.fixture(scope="session")
def scenario(model):
    from biospur_fusion.root_r6a0.synthetic import build_synthetic_scenario
    return build_synthetic_scenario(model)


@pytest.fixture(scope="session")
def gates(scenario):
    from biospur_fusion.root_r6a0.synthetic import synthetic_gate_results
    return synthetic_gate_results(scenario)
