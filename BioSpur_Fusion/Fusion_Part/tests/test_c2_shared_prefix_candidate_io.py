"""Holdout boundary cannot accept an incomplete or modified shared candidate."""
import json
import pytest
from c2_shared_prefix_candidate_io import resolve


def test_unfinished_candidate_rejected_before_any_holdout_access(tmp_path):
    (tmp_path/'RESULT.json').write_text(json.dumps(dict(completed=False,H_used=False,ten_used=False)))
    with pytest.raises(ValueError,match='completed five-only'):resolve(tmp_path)


def test_changed_artifact_rejected_before_geometry_or_models(tmp_path):
    (tmp_path/'RESULT.json').write_text(json.dumps(dict(completed=True,H_used=False,ten_used=False,bindings={'FRONTEND.json':'wrong'})))
    (tmp_path/'FRONTEND.json').write_text('{}')
    with pytest.raises(ValueError,match='binding changed'):resolve(tmp_path)
