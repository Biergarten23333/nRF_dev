# B306 v47 overnight offline causal analysis

## Causal result

1. **No wedge was observed.** There were zero joint Fusion UWB+IMU silences at or above 20 seconds.
2. **No recovery was observed.** The formal schema lacks the exact recovery counters, so the evidence disposition is `RECOVERY_EVIDENCE_UNAVAILABLE`, not an inferred zero.
3. **No reset was observed.** There was no B306 `node_ms` decrease, connection-epoch change, or clock discontinuity during nominal-power operation. DWM Tag-reset diagnostics are not B306 reset evidence.
4. **The B1 path was not explicitly exercised.** Its required retained-message/MPSL/resubmit counters were not captured; the correct result is `B1_EVIDENCE_UNAVAILABLE`.
5. Consequently, successful B1 non-reset recovery was not proved.
6. Valid continuous ten-node nominal-power exposure was 22975.737475 s (6.382149 h), from T0 2026-08-11T00:13:55.548+02:00 to operator stop 2026-08-11T06:36:51.286+02:00.
7. Healthy exposure was 57.439344 battery board-hours plus 6.382149 BSF6C53 adapter-hours, 63.821493 total board-hours.
8. No board had an evidence-backed power-degradation onset before stop.
9. No non-BSF6C53 Tag had a proved stable low **source-cadence** plateau. Low Listener receipt rates were retained as RF/receiver visibility, not mislabeled as transmitter cadence.
10. No Tag was proved to run stably near 5 Hz while peers remained near 8.33 Hz.
11. Useful exposure ended only because the operator stopped the collectors. Subsequent battery loss/unreadability is operational context, not a reconstructed firmware value.
12. The zero-wedge run is consistent with v47 prevention but does not prove it. It says nothing positive about recovery execution because neither a qualifying wedge nor retained recovery/B1 evidence was captured.

## Evidence and statistics

The formal run lasted 6.382149 h. Fusion delivered nominal approximately 200 Hz IMU and 8.33 Hz UWB streams across all ten boards through stop. Listener union rates are independent RF observations; geometry-dependent loss was not converted into source-rate or depletion claims. BSF6C53's exemption was applied only to absolute Listener reception.

At 63.821493 healthy board-hours, the historical pooled point estimate predicts 2.381 events and P(0)=0.092; the N8-only diagnostic predicts 4.065 and P(0)=0.017. These are descriptive, with only four historical events. This is the longest clean >6-hour ten-node Fusion/beacon capture found in the audited corpus.

Final dispositions: `NO_WEDGE_OBSERVED + RECOVERY_EVIDENCE_UNAVAILABLE + B1_EVIDENCE_UNAVAILABLE + STOPPED_BY_OPERATOR`. Scientific interpretation: `V47_PREVENTION_CONSISTENT_NOT_PROVEN`.

No hardware was accessed during this analysis. The 124-byte preflight shutdown fragment and all post-stop live-read material were excluded. Authoritative raw hashes matched before and after analysis.
