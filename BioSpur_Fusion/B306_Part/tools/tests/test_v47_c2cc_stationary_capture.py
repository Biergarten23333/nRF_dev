from v47_c2cc_stationary_capture import (
    FWID, IMAGE, MARKER, MASTER, NODE, evaluate_inventory,
)
from analyze_v47_c2cc_stationary import (
    is_shutdown_boundary_fragment, missing_sequence_count, planned_stop_is_complete,
    post_t0_contract_is_read_only,
)


def valid():
    return (
        {"marker": MASTER, "count": "1", "ready": "1"},
        {"count": "1", "ready": "1"},
        [{"name": NODE, "connected": "1", "subscribed": "1"}],
        {"name": NODE, "fw": MARKER, "fwid": FWID, "image_sha": IMAGE},
        {"confirmed": "1"},
    )


def test_exact_single_peer_inventory_passes():
    assert evaluate_inventory(*valid()) == []


def test_unexpected_or_duplicate_peer_fails_closed():
    args = list(valid())
    args[2] = args[2] + [{"name": "BSF6C53", "connected": "1", "subscribed": "1"}]
    assert "unexpected_or_duplicate_peer" in evaluate_inventory(*args)


def test_count_ready_and_subscription_are_exact():
    args = list(valid()); args[0] = {**args[0], "count": "10"}
    assert "master_count_ready" in evaluate_inventory(*args)
    args = list(valid()); args[2] = [{**args[2][0], "subscribed": "0"}]
    assert "peer_not_connected_subscribed" in evaluate_inventory(*args)


def test_frozen_identity_and_confirmation_are_required():
    args = list(valid()); args[3] = {**args[3], "image_sha": "00" * 32}
    assert "pong_identity" in evaluate_inventory(*args)
    args = list(valid()); args[4] = {"confirmed": "0"}
    assert "not_confirmed" in evaluate_inventory(*args)


def test_no_post_t0_mutation_contract_is_fail_closed():
    assert post_t0_contract_is_read_only({"commands_after_t0": [], "mutation": False})
    assert not post_t0_contract_is_read_only({"commands_after_t0": ["PING"], "mutation": False})
    assert not post_t0_contract_is_read_only({"commands_after_t0": [], "mutation": True})


def test_planned_duration_requires_clean_exact_stop():
    good = {"status": "CAPTURE_COMPLETE", "stop_reason": "PLANNED_DURATION_COMPLETE",
            "duration_s": 600.01}
    assert planned_stop_is_complete(good)
    assert not planned_stop_is_complete({**good, "duration_s": 599.99})
    assert not planned_stop_is_complete({**good, "stop_reason": "FAILURE_TAIL_COMPLETE"})


def test_only_incomplete_final_record_is_shutdown_fragment():
    assert is_shutdown_boundary_fragment(100, 100, b"\x03")
    assert not is_shutdown_boundary_fragment(99, 100, b"\x03")
    assert not is_shutdown_boundary_fragment(100, 100, b"\0")


def test_missing_sequence_count_preserves_gap_magnitude():
    import numpy as np
    assert missing_sequence_count(np.asarray([65534, 65535, 0, 4]), 1 << 16) == 3
