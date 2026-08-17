from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import pytest

from biospur_fusion.articulated_v2.binding import EXPECTED_NODES, FrozenMappingBinding, OperatorRecordedMappingProvider, ROLES
from biospur_fusion.articulated_v2.estimator import ArticulatedImuEstimator
from biospur_fusion.semantics_v2.canonical_human_state import to_canonical_human_state


MAPPING = dict(zip(EXPECTED_NODES, ROLES))


def payload():
    return {"binding_id":"p3-test", "schema_version":"biospur-frozen-mapping-binding-v1", "capture_id":"c", "session_id":"s", "donning_id":"d", "node_to_role":dict(MAPPING), "authority_source":"OPERATOR_RECORDED", "provenance_sha256":"0"*64, "validity":"exact session", "operator_confirmed":True, "automatic_association_status":"FAILED_DEFERRED"}


def config():
    root = Path(__file__).resolve().parents[5]
    return json.loads((root/"BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase3/PHASE3_SOLVER_CONFIG.json").read_text())


def test_exact_binding_and_runtime_immutability():
    binding = OperatorRecordedMappingProvider().load(payload(), expected_capture="c", expected_session="s", expected_donning="d")
    with pytest.raises(TypeError):
        binding.node_to_role["BSF1120"] = "torso"
    with pytest.raises(FrozenInstanceError):
        binding.session_id = "other"


@pytest.mark.parametrize("mutation", ["duplicate_role", "missing_node", "typo", "wrong_session", "automatic_top1"])
def test_fail_closed_binding_mutations(mutation):
    p = payload()
    if mutation == "duplicate_role": p["node_to_role"] = {**MAPPING, EXPECTED_NODES[-1]: ROLES[0]}
    if mutation == "missing_node": p["node_to_role"] = dict(list(MAPPING.items())[:-1])
    if mutation == "typo": p["node_to_role"] = {**MAPPING, "BSFC22C": p["node_to_role"].pop("BSFC2CC")}
    if mutation == "wrong_session": p["session_id"] = "old"
    if mutation == "automatic_top1": p["authority_source"] = "AUTOMATIC_VALIDATED"
    with pytest.raises(ValueError):
        OperatorRecordedMappingProvider().load(p, expected_capture="c", expected_session="s", expected_donning="d")


def test_equivalent_serialization_has_same_core_output():
    provider = OperatorRecordedMappingProvider()
    a = provider.load(payload(), expected_capture="c", expected_session="s", expected_donning="d")
    p = json.loads(json.dumps(payload(), sort_keys=True))
    b = provider.load(p, expected_capture="c", expected_session="s", expected_donning="d")
    assert ArticulatedImuEstimator(a, config()).output(0) == ArticulatedImuEstimator(b, config()).output(0)


def test_semantic_adapter_preserves_unavailable_and_gauges():
    binding = OperatorRecordedMappingProvider().load(payload(), expected_capture="c", expected_session="s", expected_donning="d")
    output = ArticulatedImuEstimator(binding, config()).output(0)
    state = to_canonical_human_state(output, {"subject_id":"x", "capture_id":"c", "session_id":"s", "donning_id":"d"}, "conditional")
    assert state["world_absolute_state"] == "UNAVAILABLE"
    assert state["feet"] == "UNAVAILABLE"
    assert "GLOBAL_YAW_1" in state["gauges"]
    assert not state["external_accuracy_claim"]
