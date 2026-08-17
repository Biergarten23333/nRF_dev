import ast
import json
from pathlib import Path
import pytest

from biospur_fusion.anchor_fusion_v2.zero_uwb_consumer import additive_measurement_interface_capabilities, construct_zero_uwb
from biospur_fusion.articulated_v2.binding import EXPECTED_NODES, FrozenMappingBinding, ROLES
from biospur_fusion.articulated_v2.estimator import ArticulatedImuEstimator


ROOT = Path(__file__).resolve().parents[5]/"BioSpur_Fusion/Fusion_Part"


def binding(): return FrozenMappingBinding("x","v1","c","s","d",dict(zip(EXPECTED_NODES,ROLES)),"OPERATOR_RECORDED","0"*64,"exact",True,"FAILED_DEFERRED")
def config(): return json.loads((ROOT/"config/fusion_v2/phase3/PHASE3_SOLVER_CONFIG.json").read_text())


def test_phase3_production_import_graph_has_no_uwb_or_anchor_fusion():
    for path in (ROOT/"src/biospur_fusion/articulated_v2").glob("*.py"):
        tree = ast.parse(path.read_text())
        imports = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert not any("uwb" in x.lower() or "anchor_fusion" in x for x in imports)


def test_p4_zero_uwb_constructor_is_exact_phase3_core():
    direct = ArticulatedImuEstimator(binding(), config())
    consumer = construct_zero_uwb(binding(), config())
    assert type(direct) is type(consumer)
    assert direct.output(0) == consumer.output(0)
    with pytest.raises(ValueError): construct_zero_uwb(binding(), config(), range_enabled=True)
    assert additive_measurement_interface_capabilities() == {"future_range_factor":"ADDITIVE_ONLY_NOT_IMPLEMENTED", "quaternion_overwrite":False, "hard_reset":False}


@pytest.mark.parametrize("forbidden", ["per_node_free_xyz", "bone_stretch", "perfect_hinge", "contact_hard_zupt", "phase1_orientation_factor", "uwb_factor", "mounting_cluster_factor"])
def test_forbidden_production_mutations_absent(forbidden):
    cfg = config()
    assert forbidden not in cfg or cfg[forbidden] is False
    assert cfg["real_dynamic_specific_force_enabled"] is False
