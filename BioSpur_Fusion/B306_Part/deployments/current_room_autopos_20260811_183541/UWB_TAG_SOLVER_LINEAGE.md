# UWB Tag solver lineage

`UWB_TAG_T4` is canonical. This name is deliberately separate from
`FUSION_T2/T3/T5`, none of which was run here.

The pristine UWB_TAG_T4 package is tied to commit
`3acfeeda5fede3b157081549fdf1a5f4ca939a82` (2026-06-20, “Add V5 convention
unification and solver audit outputs”). The frozen `ERLANGEN_CHAIN.md` labels
V4-io plus T4 as PRIMARY and production. It consumes raw range observations,
uses the V4-io anchor positions and per-anchor delays, requires no orientation,
and selects its behavior through `SolverConfig(method="T4")`.

The directory called U5 is the later package state associated with commit
`1c59103aff6b023d873b21403d05f8a169ebaaf1` (2026-07-13, “APS011 range-bias
campaign + Geiger CIR/overnight diagnostics + solver updates”). Its material
delta adds optional first-path-SNR based sigma inflation and supporting layout
loading. It does not introduce a `method="U5"`; the public method remains T4.
No frozen registry promotes this package delta over the pristine PRIMARY chain.
It is therefore `UWB_TAG_U5` only as an audit label and comparison implementation,
not a production successor.

Both packages were run on the same 149,999 v47 sweeps, timestamps, validity
masks, raw ranges, and current-room V4-io layout. The capture has no U5 RF
extension or anchor-sigma override, so all 149,999 XYZ outputs matched exactly.
That confirms compatibility but supplies no ground-truth evidence to promote U5.

