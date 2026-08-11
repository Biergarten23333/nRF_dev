# S1 failure autopsy

The audit was completed before S2 was implemented. BSFC2CC first crossed its 0.18 m candidate-shift threshold at T0+496.836446 s. The required persistent predicate then accumulated until the unlock at 497.986363 s; candidate formation and shift crossing, not the nominal 0.75 s dwell alone, dominated the 5.986363 s latency.

BSFAA61 contained strong discarded motion evidence: 0.5 s gyro RMS reached 94.490886 dps, one-second integrated absolute gyro angle 40.337822 degrees, and gravity-direction change 8.391075 degrees. Its candidate shift peaked at 0.306714 m, just below the high-scatter-derived 0.312501 m threshold, so the conjunctive predicate never became true.

S1's stationary consensus changed both the estimator position and `lock_position` whenever each incremental shift remained under the threshold. This let AA61 creep approximately [-81.8,-48.9,-401.6] mm without an unlock. C2CC's inferred velocity reached 3.082959 m/s. It entered SETTLING three times much later, but quiet/stability did not persist and each attempt was interrupted. No timestamp reversal, sequence gap, unit error, or half-open-window error contributed. The root causes were conjunctive evidence loss, absolute scatter-dependent thresholds, mutable lock semantics, and a relock contract that coupled noisy platform stability to an inflated velocity state.
