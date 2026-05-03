## TR 4-Anchor Replay Report

Source capture:

`logs/motion_3tag_after_master_tdma10_b61_20260503_095248/recv_20260503_095249`

Layout source:

`logs/apos_verified_b61_all3_apos_to_20260503_004436/summary.json`

Replay rule:

- Input data: `TR` per-anchor ranges only.
- Compared against existing 8-anchor `TS` output from the same sweeps.
- Candidate subsets require exactly 2 lower anchors and 2 upper anchors.
- Near-coplanar side-plane subsets were filtered by tetrahedron volume.
- Main strict run used `min_volume_m3=1.0`, leaving 28 candidate subsets.

Generated outputs:

- `replay_4anchor_2top2bottom_vol1/replay_4anchor_all_subsets.csv`
- `replay_4anchor_2top2bottom_vol1/replay_4anchor_best_per_sweep.csv`
- `replay_4anchor_2top2bottom_vol1/replay_all_valid.csv`
- `replay_4anchor_2top2bottom_vol1/replay_4anchor_subset_summary.csv`
- `replay_4anchor_2top2bottom_vol1/replay_peer_summary.csv`

## High-Level Result

The 4-anchor replay can produce very low internal solver RMS:

| Replay mode | BSF66F median/p95 | BS2DCE median/p95 | BSDC91 median/p95 |
|---|---:|---:|---:|
| Best 4-anchor per sweep | 2 / 10 mm | 4 / 17 mm | 3 / 12 mm |
| 8-anchor replay from TR | 156 / 195 mm | 104 / 222 mm | 105 / 234 mm |
| Original 8-anchor TS | 156 / 196 mm | 105 / 223 mm | 105 / 234 mm |

Important interpretation:

The very low best-4 RMS is not proof of true 2-10 mm positioning. It is the residual against only four selected ranges, chosen after seeing the data. It is useful as a diagnostic showing range/layout inconsistency, but it is not a deployable quality number by itself.

## Best Fixed 4-Anchor Subsets

Best fixed subsets after the strict non-coplanar filter:

| Subset | Rows | RMS median | RMS p95 | RMS max | Delta to TS8 median | Delta to TS8 p95 |
|---|---:|---:|---:|---:|---:|---:|
| CDEF | 1571 | 22 mm | 88 mm | 457 mm | 295 mm | 1157 mm |
| BDFG | 1564 | 30 mm | 85 mm | 308 mm | 432 mm | 1172 mm |
| BCEG | 1564 | 25 mm | 96 mm | 304 mm | 279 mm | 620 mm |
| CDEG | 1571 | 24 mm | 112 mm | 247 mm | 195 mm | 516 mm |
| BDEF | 1564 | 26 mm | 108 mm | 456 mm | 376 mm | 1263 mm |

Per-tag examples:

| Subset | BSF66F median/p95 | BS2DCE median/p95 | BSDC91 median/p95 |
|---|---:|---:|---:|
| CDEF | 22 / 56 mm | 20 / 86 mm | 25 / 122 mm |
| BDFG | 28 / 54 mm | 28 / 85 mm | 34 / 99 mm |
| BCEG | 15 / 43 mm | 35 / 98 mm | 33 / 120 mm |
| CDEG | 14 / 64 mm | 35 / 121 mm | 33 / 139 mm |

## Interpretation

Yes, 4-anchor solves can reproduce the rough "50 mm class" residual that appeared in the concept tests.

But the current 8-anchor result is not simply worse because "8 anchors are bad." The more precise statement is:

- The 8-anchor solution exposes inconsistency between ranges, layout, and possibly per-anchor bias.
- A 4-anchor subset can hide that inconsistency by fitting only four ranges.
- Low 4-anchor RMS does not guarantee low absolute position error.
- The large 4-anchor-to-8-anchor position deltas, often hundreds of mm, show the current layout/range system is not globally self-consistent yet.

## Practical Next Step

Use fixed 4-anchor replay as a diagnostic and comparison mode, not as proof of final accuracy.

Recommended next test:

1. Run a capture mode that emits both:
   - normal 8-anchor TS
   - offline or host-side fixed 4-anchor replays for CDEF, BCEG, BDFG, CDEG
2. Compare against an external reference or a controlled static/known path.
3. If one fixed subset tracks real motion better, use it temporarily.
4. Long term, solve the global inconsistency with better AutoPos layout and/or per-anchor bias calibration.

