import math

import numpy as np
import pytest

from tools.run_c2_tight_range_u5b import final_bias_snapshot_epoch


def test_final_group_link_overhang_uses_next_float_after_maximum_link_epoch():
    prefix_stop = 100.0
    maximum_link = prefix_stop + 0.0042
    query = final_bias_snapshot_epoch(maximum_link)
    assert query == np.nextafter(maximum_link, math.inf)
    assert query > maximum_link > prefix_stop


def test_no_accepted_decisions_has_no_summary_epoch():
    assert final_bias_snapshot_epoch(None) is None


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_maximum_accepted_epoch_rejects(value):
    with pytest.raises(ValueError, match="finite"):
        final_bias_snapshot_epoch(value)
