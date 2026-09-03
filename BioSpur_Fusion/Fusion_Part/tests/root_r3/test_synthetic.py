import numpy as np

from biospur_fusion.root_r3.estimator import RootFilterConfig
from biospur_fusion.root_r3.replay import run_cv_tracker
from biospur_fusion.root_r3.synthetic import harmonic_metrics, synthetic_observations


def test_harmonic_metric_recovers_known_gain_and_delay():
    time = np.arange(0.0, 20.0, 0.01); frequency = 0.5; delay = 0.08
    truth = np.sin(2 * np.pi * frequency * time)
    output = 0.7 * np.sin(2 * np.pi * frequency * (time - delay))
    result = harmonic_metrics(time, truth, output, frequency)
    assert abs(result["gain"] - 0.7) < 1e-4
    assert abs(result["phase_delay_s"] - delay) < 1e-4


def test_synthetic_replay_is_deterministic_and_strict_causal():
    observations, _, _ = synthetic_observations(0.3, sigma_xy_m=0.2, sigma_z_m=0.4,
                                                 duration_s=3.0, seed=17)
    config = RootFilterConfig(maximum_position_influence_m=0.05)
    first, first_audit = run_cv_tracker(observations, config)
    second, second_audit = run_cv_tracker(observations, config)
    for key in ("time_s", "root_m", "velocity_mps", "covariance_diag_m2", "accepted", "reason", "mode"):
        assert np.array_equal(first[key], second[key])
    assert first_audit == second_audit
    assert first_audit["future_uwb_count"] == first_audit["future_imu_count"] == 0
    assert first_audit["preavailability_output_count"] == 0
