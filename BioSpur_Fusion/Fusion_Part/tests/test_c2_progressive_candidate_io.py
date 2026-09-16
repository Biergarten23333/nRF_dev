import json
import pytest
from c2_progressive_candidate_io import resolve


def test_incomplete_candidate_cannot_reach_any_holdout_reader(tmp_path):
    with pytest.raises(FileNotFoundError):resolve(tmp_path)
    (tmp_path/'RESULT.json').write_text(json.dumps(dict(actions=['00_initial_still'],completed=False)))
    (tmp_path/'CONTRACT.json').write_text(json.dumps(dict(H_used=False,ten_node_used=False)))
    with pytest.raises(ValueError,match='complete'):resolve(tmp_path)
