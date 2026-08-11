import numpy as np
import pytest

from v47_state_adaptive_fusion import (
    AdaptiveParameters, StateAdaptiveFusion, merge_event_order,
    require_vector_frame_binding, wrap_safe_delta_us,
)


QUIET = {"gyro_rms_dps": .01, "accel_dev_rms_g": .001,
         "gyro_std_dps": .005, "accel_std_g": .0002}
ACTIVE = {"gyro_rms_dps": 2.0, "accel_dev_rms_g": .2,
          "gyro_std_dps": 1.0, "accel_std_g": .1}


def params():
    return AdaptiveParameters(
        uwb_r_m2=np.eye(3) * .02**2,
        gyro_rms_threshold_dps=.1, accel_dev_rms_threshold_g=.01,
        gyro_std_threshold_dps=.05, accel_std_threshold_g=.005,
        platform_stability_threshold_m=.05, platform_shift_threshold_m=.15,
        exit_dwell_s=.3, moving_quiet_dwell_s=.3, settling_dwell_s=.5,
        consensus_window_s=.5, consensus_min_observations=4,
        consensus_update_period_s=.2,
    )


def feed(est, start, end, center, features=QUIET):
    first = int(round(start * 20))
    stop = int(round(end * 20))
    for tick in range(first, stop):
        t = tick / 20.0
        est.process_control(float(t), features)
        if round(t * 20) % 2 == 0:
            est.process_uwb(float(t + .01), np.asarray(center, float), record_index=len(est.audit))


def initialized():
    est = StateAdaptiveFusion(params())
    feed(est, 0, 2, [0, 0, 0])
    assert est.state == "STATIONARY"
    return est


def test_async_uwb_is_inserted_between_control_samples():
    order = merge_event_order(np.array([1.0, 1.05]), np.array([1.025]))
    assert order == [("control", 0, 1.0), ("uwb", 0, 1.025), ("control", 1, 1.05)]


def test_stationary_entry_and_exit_dwell_and_true_motion_release():
    est = initialized()
    feed(est, 2, 2.25, [.5, 0, 0], ACTIVE)
    assert est.state == "STATIONARY"
    feed(est, 2.25, 3.2, [.5, 0, 0], ACTIVE)
    assert est.state == "MOVING"


def test_no_zupt_during_known_motion():
    est = initialized()
    feed(est, 2, 3.2, [.5, 0, 0], ACTIVE)
    assert est.state == "MOVING"
    count = est.zupt_updates
    feed(est, 3.2, 4, [.7, 0, 0], ACTIVE)
    assert est.zupt_updates == count


def test_stationary_outlier_rejected_and_position_not_chased():
    est = initialized()
    before = est.x[:3].copy()
    est.process_uwb(2.01, np.array([10., 0., 0.]), record_index=99)
    assert est.audit[-1]["category"] == "rejected"
    assert np.allclose(est.x[:3], before)


def test_transient_table_vibration_does_not_create_persistent_transition():
    est = initialized()
    # IMU disturbance with a noisy but non-shifted UWB platform.
    for tick in range(40, 50):
        t = tick / 20.0
        est.process_control(t, ACTIVE)
        if tick % 2 == 0:
            jitter = .04 if tick % 4 == 0 else -.04
            est.process_uwb(t + .01, np.array([jitter, 0., 0.]), record_index=tick)
    feed(est, 2.5, 3.5, [0, 0, 0], QUIET)
    assert est.state == "STATIONARY"
    assert not any(t["to_state"] == "MOVING" for t in est.transitions)


def test_settles_onto_new_platform_without_reinitialization():
    est = initialized()
    feed(est, 2, 3.3, [.5, 0, 0], ACTIVE)
    assert est.state == "MOVING"
    feed(est, 3.3, 5.5, [.5, 0, 0], QUIET)
    assert est.state == "STATIONARY"
    assert np.linalg.norm(est.x[:3] - [.5, 0, 0]) < .08
    assert est.reinitializations == 0


def test_covariance_psd_symmetric_and_accounting_complete():
    est = initialized()
    est.process_uwb(2.01, None, status="missing", record_index=1)
    est.process_uwb(2.02, np.array([np.nan, 0, 0]), record_index=2)
    est.process_uwb(2.03, np.array([9., 0, 0]), record_index=3)
    categories = [a["category"] for a in est.audit]
    assert len(categories) == len(est.audit)
    assert {"accepted", "rejected", "invalid", "unavailable"} <= set(categories)
    assert np.linalg.eigvalsh(est.covariance).min() >= -1e-10
    assert np.max(np.abs(est.covariance-est.covariance.T)) <= 1e-12


def test_wrap_safe_timestamp_handling():
    assert wrap_safe_delta_us(3, 65534, bits=16) == 5
    with pytest.raises(ValueError):
        wrap_safe_delta_us(10, 100, bits=16)


def test_replay_is_deterministic():
    def once():
        est = initialized()
        feed(est, 2, 3.3, [.5, 0, 0], ACTIVE)
        feed(est, 3.3, 5.5, [.5, 0, 0], QUIET)
        return est.x.copy(), est.covariance.copy(), list(est.transitions), list(est.audit)
    a, b = once(), once()
    assert np.array_equal(a[0], b[0])
    assert np.array_equal(a[1], b[1])
    assert a[2:] == b[2:]


def test_unavailable_frame_transform_fails_explicitly():
    with pytest.raises(ValueError, match="BLOCKED_FRAME_BINDING"):
        require_vector_frame_binding({"sensor_to_v4_transform_status": "BLOCKED_FRAME_BINDING"})
