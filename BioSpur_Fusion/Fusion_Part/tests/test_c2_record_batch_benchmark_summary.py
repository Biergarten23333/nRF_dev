from collections import Counter

from benchmark_c2_record_batches import _counts_match, _serialized_calls


def test_summary_zero_fills_all_gate_keys_and_rejects_nonzero_cross_mode_allocations():
    legacy = {
        "mode": "legacy", "callbacks": 2,
        "calls": _serialized_calls(Counter(
            event=2, sensor=2, structural=8, generic=2, imu_child=1, uwb_child=1,
        )),
    }
    batch = {
        "mode": "batch", "callbacks": 1,
        "calls": _serialized_calls(Counter(
            event=2, sensor=2, structural=4, record=1,
        )),
    }
    assert legacy["calls"]["record"] == 0
    assert batch["calls"]["generic"] == 0
    assert batch["calls"]["imu_child"] == batch["calls"]["uwb_child"] == 0
    assert _counts_match(legacy, events=2, records=1)
    assert _counts_match(batch, events=2, records=1)
    batch["calls"]["generic"] = 1
    assert not _counts_match(batch, events=2, records=1)
