from pathlib import Path
import importlib.util


ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase2r/finalize_phase2r.py"
SPEC = importlib.util.spec_from_file_location("phase2r_finalize", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_authoritative_names_are_forbidden_for_failed_stage():
    assert MODULE.FORBIDDEN_AUTHORITATIVE == {"NODE_ASSOCIATION_FREEZE.json", "CALIBRATION_BUNDLE_MANIFEST.json"}


def test_required_delivery_contract_is_explicit():
    assert "BLIND_CANDIDATE_FREEZE.json" in MODULE.REQUIRED
    assert "CALIBRATION_BUNDLE_CONDITIONAL_MANIFEST.json" in MODULE.REQUIRED
    assert "P3_CONSUMER_PROBE_RESULT.json" in MODULE.REQUIRED
