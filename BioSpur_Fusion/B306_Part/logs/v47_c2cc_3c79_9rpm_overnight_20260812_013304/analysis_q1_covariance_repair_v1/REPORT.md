# Q1 covariance repair qualification

Primary verdict: `Q1_COVARIANCE_REPAIR_PASS`.

Both frozen failures reproduced exactly: BSF3C79 at 596.100 s/sample 119200 and BSFC2CC at 664.005 s/sample 132800. The apparent negative eigenvalues occur only after the old matrix has lost float64 resolvability; the meaningful failure is the earlier Cholesky/condition loss caused by enormous artificial covariance growth.

The first material float64-resolution loss (`condition > 1/eps`) is BSF3C79 at 81.200 s and BSFC2CC at 118.558 s. First absolute negative eigenvalues occur later, at 595.552 s and 653.856 s respectively; these absolute negatives are tiny relative to the approximately `1e19` dominant mode.

The root cause is the combination of a non-orthogonal first-order Euler transition for a sustained rotational generator and an inconsistent unbound-frame model that continued an unobservable spatial integration chain. Exact rotational/Van-Loan discretization plus consistent spatial isolation completes the full 7.283928561 h replay for both nodes with finite, Cholesky-valid covariance, normalized/sign-continuous quaternions, zero resets, and zero false stationary relocks.

All 23 synthetic rows pass (including six signed 9 RPM axis cases at 24 h). Compact frozen stationary, interactive-rotation, and ten-node tabletop regressions pass. Full spatial real-data coupling remains `SPATIAL_ACCELERATION_COUPLING_BLOCKED_FRAME_BINDING`; Q1 is ready for the black-box/frame calibration experiment, not for unbound V4 acceleration fusion. Existing authoritative raw evidence closes this numerical question; another overnight rotation is unnecessary.

No covariance/state clipping, epsilon loading, restart, reset, process-noise reduction, hardware access, or evidence rewrite occurred. Large per-step traces are stored under `B306_Part/logs/v47_c2cc_3c79_9rpm_overnight_20260812_013304/forensic_q1_covariance_repair_v1` and excluded from Git.
