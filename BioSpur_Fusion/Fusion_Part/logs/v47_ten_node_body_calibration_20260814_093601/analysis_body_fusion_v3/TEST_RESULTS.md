# Fusion V3 regression results

The complete `Fusion_Part/tests` suite was executed twice after the final
model, finite-null-effect classifier, and report-only verdict logic were
frozen.

- Replay A: `27 passed in 32.43s`; exit code 0.
- Replay B: `27 passed in 32.74s`; exit code 0.

The suite includes deterministic exciting-motion parameter recovery,
stationary-motion degeneracy, dropout and 0.5/1/2 m UWB flyer rejection,
unilateral articulated correction, genuine lag-window relinearization,
common-clock reconstruction, canonical UWB_TAG_T4 binding, typed ingest, and
calibration/held-out ledger separation.

The complete real-capture derivation also ran twice. `DETERMINISTIC_REPLAY.json`
reports PASS for the calibration verdicts, physical null-effect evidence,
clock result, freeze block, and byte-stable calibration/held-out ledger
contents. No held-out payload was opened by either calibration process.
