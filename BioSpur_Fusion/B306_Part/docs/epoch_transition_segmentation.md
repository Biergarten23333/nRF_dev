# Host alignment: marked epoch-transition segmentation

Status: required host-aligner behavior, recorded 2026-08-02 from relay8.3 Addendum 6.

The carried modulo-16 absolute-epoch label can acquire a constant offset when beacon lock is established. A constant listener-backed offset is usable and must be recorded. The offset is not required to remain unchanged across an arbitrarily long capture because a reset or legitimate beacon reacquisition can establish a new anchor.

The host aligner must therefore end the current time-axis segment and estimate a new absolute-epoch anchor whenever an epoch-offset transition is accompanied by a node-local marker. Accepted markers are:

- an `sf_valid=0` run;
- a corroborated `tag_reset_detected` event with a backward sweep-counter discontinuity;
- a measured `sweep_drop` counter increment;
- a beacon `lock` transition or an `rx` stall.

The relay8.3 verification analyzer uses a bounded ±12.0 s association window. This covers the slowest current marker source, the approximately 10 s beacon-status query cadence. Raw offset changes separated by at most 1.0 s are grouped as one transition episode (for example a one-record excursion `0→1→0` after relock). Every raw change, transition timestamp, marker timestamp, and association remains in evidence.

An offset transition without one of these markers is a hard failure: the host has no observable reason to terminate the old segment. Do not silently infer or repair such a transition from its eventual modal value.

The cheapest firmware improvement is to timestamp each `sweep_drop` increment at the node. A periodically sampled counter value cannot prove when the increment occurred and cannot retrospectively mark a transition.
