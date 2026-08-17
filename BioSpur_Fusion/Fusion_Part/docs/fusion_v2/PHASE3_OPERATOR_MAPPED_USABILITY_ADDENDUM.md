# Phase 3 operator-mapped usability addendum

This addendum hash-binds the accepted Phase 1 pose usability contract at
`0895252ca7f4d77aca0f739e65bcdaa2ce22188be81a709157956a1f38fad736`.
It narrows Phase 3 to internal, operator-mapped, body-relative engineering
usability. It does not establish external accuracy.

The output grid is 100 Hz. Initialization targets 2 s and may not exceed 5 s.
Every eligible grid point must emit a filtered or degraded-predicted record;
development usable availability must be at least 99%, and H00 at least 95%,
with no unexplained unusable run longer than 250 ms. H00 natural walking and
turning is the normal-motion in-scope gate. H01 boxing and H02 golf are stress
probes: action-specific quality cannot overturn an ordinary-motion result, but
crash, NaN, frozen state, permutation, frame swap, or covariance collapse fails
the universal safety contract.

Instrumented orientations and body-relative joint states are conditional on the
operator mapping and limited calibration. Root is local and drifting. World
absolute state, feet, and contact are unavailable. Positions are
`MODEL_INFERRED_SCALE_CONDITIONAL`; no metric or clinical interpretation is
permitted.
