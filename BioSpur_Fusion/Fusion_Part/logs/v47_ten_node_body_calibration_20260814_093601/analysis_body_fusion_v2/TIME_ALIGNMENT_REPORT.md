# Strict common-clock Gate 0

Verdict: `TIME_ALIGNMENT_PASS`.

The fit uses retained Listener LBD Beacon counters, LPD poll source/sequence, the capture's 120 ms superframe, the hardware `strobe_us` TIMER2 timestamp and the carried segment-constant modulo-16 label. Master arrival participates only in a coarse sequence candidate join and in the operator-annotation bridge; it supplies no measurement timestamp, drift or fractional phase.

Worst clean residual p95 is 280.852 us; worst clean maximum is 408.279 us. Every discarded clock pair remains in CLOCK_RESIDUALS.csv as `rejected-timing-outlier`. All ten boot segments are explicit and have no timestamp reversal.
