# Root-R6A2A-R2 covariance repair

Verdict: `PASS_ROOT_R6A2A_R2_SYNTHETIC_COVARIANCE_REPAIR`.

The covariance root cause was demonstrated. The failed implementation represented weak geometry through the full UWB Jacobian/information matrix and then added 0.0015 m² in the weakest root direction on every weak step. Removing that term exposed a smaller mismatch: synthetic initialization applied fixed 55/35/18 mm axis errors while declaring an unrelated isotropic 60 mm root covariance. The final repair removes the additive term and uses the initializer's exact per-axis second moments.

Attempt 001 is retained as a 35/36 failure. Fresh attempt 002 passed all 36 gates and 157 predecessor/R2 tests. Independent raw-evidence verification passed. Low-vertical normalized NEES is 1.007094 within 0.742219–1.295612; 1σ/2σ/3σ coverage is 0.62/0.98/1.00. Raw nominal scalar UWB NIS is 1.432403 and effective weighted scalar NIS is 1.359929, each with one measurement degree of freedom.

No real fusion, real body-state update, held-out payload access, or real calibration-slot write occurred. Root-R6A2B and production fusion remain unauthorized. The bounded-real-shadow critical path is recorded separately.
