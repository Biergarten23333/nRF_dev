from biospur_fusion.c2_uwb_calibration.held_link_summary import (
    summarize_held_links,
)


def _document() -> dict:
    links = {}
    for node_index in range(10):
        for anchor in range(8):
            new = 0.3 if anchor % 2 else 0.1
            old = 0.2
            links[f"BSF{node_index:04X}/anchor_{anchor}"] = {
                "available": True,
                "positive_bias_enabled": anchor == 6,
                "bias_m": 0.4 if anchor == 6 else 0.0,
                "sigma_bias_m": 0.1,
                "sigma_history_m": 0.2,
                "paired_new_median_abs_m": new,
                "paired_t4_median_abs_m": old,
                "paired_count": 100,
            }
    return {"links": links}


def test_summary_preserves_cross_node_anchor_structure() -> None:
    result = summarize_held_links(_document())
    assert result["links"] == 80
    assert result["by_anchor"]["G"]["positive_bias_links"] == 10
    assert result["systematic_anchor_candidates"] == ["G"]
    assert result["diagnostic_comparator"]["promotion_allowed"] is False
    assert result["scientific_pass"] is False


def test_summary_rejects_incomplete_grid() -> None:
    document = _document()
    document["links"].pop("BSF0000/anchor_0")
    try:
        summarize_held_links(document)
    except ValueError as error:
        assert "complete 10 x 8" in str(error)
    else:
        raise AssertionError("incomplete grid was accepted")
