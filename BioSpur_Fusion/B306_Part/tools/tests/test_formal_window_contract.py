import pytest

from B306_Part.tools.formal_window_contract import assert_formal_window_contract


def row(count=12, slot=1):
    return {"command": f"CFG SLOT={slot} COUNT={count} PERIOD=10 BEACON_SYNC=1 RUN=1",
            "reply": f"CFG_OK SLOT={slot}/{count} PERIOD=10 BEACON_SYNC=1 RUN=1"}


def test_contract_accepts_mandated_guard_slot():
    result = assert_formal_window_contract(
        tag_cfg={"BSF0001": row()},
        fleet={"aggregate": {"spacing": "ON", "spacing_us": "5000",
                              "spacing_generation": "2"}},
        beacon_result={"period_120_seen": True}, expected_count=12,
        expected_period_ms=10, expected_beacon_us=120000,
        expected_slots={"BSF0001": 1})
    assert result["pass"]


def test_contract_rejects_count11_before_capture():
    with pytest.raises(ValueError):
        assert_formal_window_contract(
            tag_cfg={"BSF0001": row(11)},
            fleet={"aggregate": {"spacing": "ON", "spacing_us": "5000",
                                  "spacing_generation": "2"}},
            beacon_result={"period_120_seen": True}, expected_count=12,
            expected_period_ms=10, expected_beacon_us=120000)


def test_contract_rejects_occupied_guard_slot():
    with pytest.raises(ValueError):
        assert_formal_window_contract(
            tag_cfg={"BSF0001": row(slot=11)},
            fleet={"aggregate": {"spacing": "ON", "spacing_us": "5000",
                                  "spacing_generation": "2"}},
            beacon_result={"period_120_seen": True}, expected_count=12,
            expected_period_ms=10, expected_beacon_us=120000,
            expected_slots={"BSF0001": 11})
