from __future__ import annotations

import numpy as np

from biospur_fusion.root_r5a.ownership import write_ownership_ledger


def test_exact_association_and_matched_support(c1):
    data, lineage, support = c1
    assert lineage["exact_constituent_t4_events"] == 98342
    assert lineage["conservative_envelope_t4_events"] == 0
    assert lineage["unresolved_t4_events"] == 0
    assert len(support.matched_epochs) > 200
    assert np.all(np.isin(data.epoch[support.matched_event_mask], support.matched_epochs))


def test_event_ownership_mutation_is_rejected(c1, tmp_path):
    data, _, support = c1; audit = write_ownership_ledger(tmp_path, data, support)
    assert audit["DIRECT_EVENT_DOUBLE_COUNT_PREVENTED"]
    assert audit["duplicate_mutation_detected"]
    assert audit["T4_FULL_FUNCTIONAL_DEPENDENCY_NOT_YET_PROVEN"]
