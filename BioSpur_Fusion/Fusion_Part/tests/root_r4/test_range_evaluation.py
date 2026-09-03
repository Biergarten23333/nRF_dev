from __future__ import annotations

from biospur_fusion.root_r4.evaluation import candidate_matrix, frequency_and_pareto
from biospur_fusion.root_r4.synthetic import range_goldens


def test_range_fault_goldens():
    result = range_goldens()
    assert result["passed"]
    assert result["cases"]["one_corrupted_anchor"]["identified_anchor"] == 5
    assert result["cases"]["one_corrupted_tag"]["identified_tag"] == 4


def test_candidate_matrix_never_promotes_blocked_frame():
    matrix, results = candidate_matrix(frame_authorized=False, inertial_synthetic_pass=True, lineage_closed=True)
    by_id = {row["candidate"]: row for row in matrix["rows"]}
    assert by_id["R4-C5"]["status"] == "STOPPED_FRAME_GATE"
    assert results["genuine_lineage_safe_real_c1_imu_uwb_common_root_candidate"] is None
    assert by_id["NC-1"]["status"] == "REJECTED"


def test_pareto_preserves_frontier_without_selection():
    frequency, pareto = frequency_and_pareto()
    assert frequency["no_production_row_selected"]
    assert pareto["no_production_row_selected"]
    assert pareto["frontier"]
