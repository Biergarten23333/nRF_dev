# Superseded hard-stationarity dataflow

This document records the implementation that was exercised by the first two
Phase-A runs and why it is not a valid human-wearable calibration frontend.
Those runs are retained as local evidence, but their stationarity verdicts are
superseded.

The old path was:

```text
accepted IMU sample
  -> preliminary bias from the labelled initial-still window
  -> gyro deviation <= 5 deg/s
  -> acceleration-norm deviation <= 0.12 g
  -> filtered jerk <= 8 m/s^3
  -> binary per-sample AND
  -> >=50% positive samples in a 50 ms bin
  -> >=70% of all nodes vote positive in that bin
  -> >=0.40 s contiguous positive bins
  -> per-node confirmed-stationary fraction >=0.60
  -> Q2 frontend PASS/FAIL
```

This path conflates three separate claims: transport validity, bias/gravity
evidence, and neutral-pose quality.  It also lets motion at unrelated nodes
zero another node's evidence.  Normal breathing, postural sway, muscle motion,
and strap micro-motion therefore propagate into a false calibration failure.
The legacy Q2 implementation then uses the whole labelled window as a fallback
when fewer than 20 eligible samples exist, contradicting the failed gate.

The accepted interpretation is instead:

```text
DATA_STREAM_VALIDITY = PASS when accounting/timestamps/finite values pass
Q2_OUTPUT_FINITE = evaluated independently
HARD_ROBOTIC_STATIONARITY_MODEL = INVALID_FOR_HUMAN_WEARABLE_MOCAP
OPERATOR_FAULT = FALSE
```

No percentage produced by this superseded binary path is evidence that the
operator failed to stand naturally.  Revision C and its evidence remain
immutable; this V1 implementation does not modify them.
