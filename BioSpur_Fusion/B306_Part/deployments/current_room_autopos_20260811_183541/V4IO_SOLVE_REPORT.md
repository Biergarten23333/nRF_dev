# Current-room V4-io solve

Verdict: `V4IO_LAYOUT_PASS` and `RELATIVE_GEOMETRY_ONLY`.

The only input was this deployment's formal SW100. All 56 directed paths have
at least 99 valid observations; 55 have 100 and the F→C path has 99 because its
single quality-zero observation remains in raw evidence but is excluded by the
frozen parser. Forward/reverse observations are fused by the production v3
MAD/MVUE rule. The layout uses the frozen Huber joint objective, A delay fixed
to zero, B–H delays bounded to ±60 mm, and the frozen soft two-layer prior.

The full 28-pair solve converged with 56.49 mm inter-anchor RMS. The first/last
halves differ by at most 127.45 mm in pair distance and 8.87 mm in delay; fitting
one half and evaluating the other gives 52.74/66.29 mm RMS. Repeated selected
initialization is numerically identical, and the tested initializations differ
by at most 6.61 mm in pair distance after selecting the frozen objective's best
mirror. Jacobian condition number is 96.14. The frozen solver does not publish
a calibrated layout covariance, so none is invented.

Leave-one deletion is reported as an advisory sensitivity diagnostic because
the frozen solver defines no calibrated acceptance threshold: maximum pair
distance response is 358.89 mm for leave-one-pair and 184.52 mm for leave-one-
anchor. These do not replace the independent split/held-out acceptance gate.
No Tag location, old Erlangen coordinate, or visual adjustment was used to fit
the layout.

Detailed evidence is in `V4IO_QUALIFICATION.json`, `V4IO_PAIR_RESIDUALS.csv`,
`V4IO_MULTISTART.csv`, and the two leave-one tables.
