# BSFC2CC held-out transient forensic audit

Historical verdict remains **C2CC_DEVICE_CALIBRATION_FAIL**.

Disposition: **REPEATED_SENSOR_ANOMALY**.

1. `1_consecutive_samples`: `1`
2. `2_dominant_raw_channel`: `"a1"`
3. `3_one_5ms_sample`: `true`
4. `4_adjacent_samples_nominal`: `true`
5. `5_simultaneous_gyro_motion`: `false`
6. `6_vector_rotation_consistent_with_handling`: `false`
7. `7_one_channel_dip_or_spike`: `true`
8. `8_batch_or_frame_boundary`: `false`
9. `9_transport_or_time_anomaly`: `false`
10. `10_similar_anomalies`: `{"accepted_stationary_samples": 89060, "burst_count_ge_2": 0, "counts_by_set": {"HELDOUT": 2}, "empirical_isolated_rate_per_sample": 2.2456770716370986e-05, "isolated_single_sample_transients": 2, "maximum_consecutive_samples": 1, "threshold_exceedances": 2, "transient_candidates": 2}`
11. `11_empirical_isolated_transient_rate`: `2.2456770716370986e-05`
12. `12_forced_q1_material_perturbation`: `{"covariance_min_eigenvalue": 6.33409982179238e-07, "nis": 924.376371634729, "quaternion_step_deg": 0.1987726869356472}`
13. `13_causal_gate_rejects`: `{"accepted": false, "covariance_min_eigenvalue": 6.517616079222361e-07, "nis": 924.376371634729, "norm_residual_g": 0.10979410267877454, "quaternion_norm": 1.0, "reason": "INNOVATION_NIS_REJECTED"}`

The audit does not relabel the previous run, remove the sample, refit parameters, or claim a hardware defect. The complete 10-second context and stationary-population accounting are retained in the adjacent CSV files.
