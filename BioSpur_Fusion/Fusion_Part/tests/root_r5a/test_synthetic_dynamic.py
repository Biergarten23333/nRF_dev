from __future__ import annotations

from biospur_fusion.root_r5a.synthetic import synthetic_qualification


def test_synthetic_classes_and_mutations_pass():
    result = synthetic_qualification()
    assert result["passed"]
    assert result["cases"]["broad_weak_no_drift"]["classified_weak"]
    assert result["cases"]["sharp_constant"]["classified_constant"]
    assert result["cases"]["sharp_known_drift"]["dynamic_held_block_improvement"]
    assert result["cases"]["known_global_time_offset"]["absolute_error_s"] <= 5e-4
    assert result["cases"]["tag_inconsistent_geometry"]["detected"]
    assert result["cases"]["circular_wraparound"]["circular_error_deg"] < 0.2
