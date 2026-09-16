import pytest
from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
from biospur_fusion.c2_five_calibration.phase_contract import recorded_prefix


def test_prefix_membership_and_final_requirement():
    assert recorded_prefix(EPISODES[:14])==EPISODES[:14]
    assert recorded_prefix(reversed(EPISODES),require_complete=True)==EPISODES
    with pytest.raises(ValueError,match='complete'):recorded_prefix(EPISODES[:14],require_complete=True)


@pytest.mark.parametrize('names',[[],['H01_boxing'],EPISODES[:4]+EPISODES[5:7],
                                  EPISODES[:2]+EPISODES[:1],EPISODES+('H01_boxing',)])
def test_no_future_holdout_duplicate_or_skipped_action(names):
    with pytest.raises(ValueError,match='prefix'):recorded_prefix(names)
