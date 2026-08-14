# Fusion V4 regression results

- Complete suite A: `31 passed in 35.35s`; exit code 0.
- Complete suite B: `31 passed in 33.52s`; exit code 0.
- V4 derivation A/B: deterministic PASS.

New tests cover recovery of the axial-twist quotient centerline, explicit
retention of eight unavailable limb axial-twist DOFs, physical-unit null
metrics, stationary quotient degeneracy, incomplete anthropometry rejection,
and acceptance of a complete versioned anthropometry document including a
signed hip vertical offset.

The real V4 run stopped before opening calibration payload because the
anthropometry input is incomplete. Held-out walk/final-still were not opened.
