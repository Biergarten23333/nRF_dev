import inspect

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.async_root_worker_u7c import AsyncRootWorker
from test_c2_async_root_worker import (
    _config,
    _corroboration,
    _envelope,
    _event,
    _group,
    _imu,
)
from biospur_fusion.c2_uwb_root_world.causal_update_guard import ReachabilityClass


def test_constructor_waits_for_real_ready_and_preserves_public_api():
    worker = AsyncRootWorker(_config(capacity=4))
    assert worker.pid is not None and worker.cold_start_ms > 0.0
    assert callable(worker.submit) and callable(worker.close_and_collect)
    worker.submit(_imu(0, 0.005))
    rows, final = worker.close_and_collect(0)
    assert rows == []
    assert final["sentinel_received"] is True
    assert final["processed_event_count"] == 1
    assert final["input_queue_size_after_join"] == 0
    assert final["process_exitcode"] == 0
    assert final["process_alive_after_join"] is False


def test_ready_handshake_uses_neither_sleep_nor_dummy_state_event():
    import biospur_fusion.c2_uwb_root_world.async_root_worker_u7c as owner

    source = inspect.getsource(owner.AsyncRootWorker.__init__)
    assert 'kind != "READY"' in source
    assert "time.sleep" not in source
    assert "RootWorkerEvent" not in source


def test_post_ready_exact_parity_and_submit_blocking_measurement():
    from biospur_fusion.c2_uwb_root_world.async_root_worker_u7c import run_synchronous

    events = [_imu(0, 0.005), _imu(1, 0.010), _event(_group(), 2)]
    reference, reference_final = run_synchronous(_config(), events)
    reference = [row for row in reference if row["kind"] == "UWB"]
    worker = AsyncRootWorker(_config())
    for event in events:
        worker.submit(event)
    actual, final = worker.close_and_collect(1)
    assert [(r["decision"], r["root_reason"]) for r in actual] == [
        (r["decision"], r["root_reason"]) for r in reference
    ]
    for name in ("state", "covariance", "h", "r", "s", "innovation"):
        np.testing.assert_allclose(actual[0][name], reference[0][name], atol=1e-12, rtol=0)
    assert actual[0]["nis"] == pytest.approx(reference[0]["nis"], abs=1e-12)
    np.testing.assert_allclose(final["state"], reference_final["state"], atol=1e-12, rtol=0)
    assert len(worker.submit_blocking_ms) == len(events)


def test_impossible_jump_and_only_fully_corroborated_fall_semantics_remain():
    jump = _group(target=np.array([0.35, 0.0, 0.0]))
    dynamic = _envelope(2.0, ReachabilityClass.DYNAMIC_FALL)
    activity, consensus, contact = _corroboration(jump)
    quiet = AsyncRootWorker(_config(_envelope(0.001)))
    quiet.submit(_event(jump))
    rows, _ = quiet.close_and_collect(1)
    assert not rows[0]["committed"]
    falling = AsyncRootWorker(_config(_envelope(0.001)))
    falling.submit(_event(jump, dynamic_envelope=dynamic, activity=activity,
                          consensus=consensus, contact=contact))
    rows, _ = falling.close_and_collect(1)
    assert rows[0]["committed"]
    assert rows[0]["decision"] == "ACCEPT_CORROBORATED_DYNAMIC"
