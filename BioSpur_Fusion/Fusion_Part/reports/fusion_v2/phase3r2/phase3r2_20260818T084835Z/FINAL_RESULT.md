# Phase 3-R2 final result

Primary verdict: `STAGE_COMPLETE_NEEDS_CURRENT_SESSION_TIME_EVIDENCE`.

The continuous 9-state-per-node frontend, one-session 30-D articulated solver,
operator mapping, field-selective timing reader, frozen split, covariance,
observability, gap accounting, and corrected torso FK are implemented at
candidate `c952087df57faae86748b0bc7e74877b14da3f7b`. Detached synthetic qualification passed 39/39 tests.

The current capture did not pass the strict ten-node common-time gate. The
worst fitted segment was BSFAA61 segment 1 (P95 825.651 us, maximum 1019.185
us), and BSFC2CC lacked sufficient independent timing overlap in the controlled
windows. Therefore no real IMU numeric FIT/VALIDATION/H cache was opened: no
real session calibration bundle, semantic score, static-wobble verdict, B0/B1/P
comparison, H retrospective, or animation is claimed.

UWB semantic numeric decode, arrays, statistics, factors, initializer use, and
configuration influence are all zero. The preserved co-located transport text
exposure count is one. Phase 4 was not started.
