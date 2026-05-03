# Runtime Layout + AutoPos V3 Anchor-Only Validation

Date: 2026-05-02

## Scope

This validates runtime Tag anchor-layout update with NVS persistence, using AutoPos V3 anchor-only inter-anchor sweep output. No Tag115/Tag CM data was used for the AutoPos solve.

## AutoPos V3 Anchor-Only Sweep

Sweep directory:

```text
logs/autopos_v3_anchor_only_100set_prewarm10_20260502_211137
```

Sweep settings:

```text
order=ABCDEFGH
sw_sets=100
prewarm_sw_sets=10
warmup_min_quality=90
Tag assist / Tag115 CM: not used
```

Sweep result:

```text
SW-A ... SW-H: 100/100 accepted each, raw=110 discarded=10
final responder restore: ready=8/8
```

Solver output:

```text
solve_v3_box/anchor_layout_v3_box.json
rms_edges_mm=113.27
rms_inlier_mm=70.86
inlier_count=27
outlier_count=1
top outlier: B-D err=472.9 mm
```

Coordinates pushed to Tags:

```text
APOS 0 0 0 0
APOS 1 4751 0 0
APOS 2 3984 3714 5
APOS 3 -462 2741 0
APOS 4 83 -78 1625
APOS 5 4423 85 1603
APOS 6 3846 3767 1613
APOS 7 -546 2698 1605
```

## b57 Issue

b57 proved APOS/NVS persistence but was built with the wrong Tag build path. The outer cache had broadcast parameters, but the actual Tag app cache had:

```text
APP_ALT_SS_TWR_ENABLE=0
APP_ALT_SS_TWR_BCAST_ENABLE=0
APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE=0
APP_ALT_SS_TWR_GUARD_US=400
```

This caused the first post-AutoPos motion capture to emit only TD/SD diagnostics and no UWB poll frames.

## b58 Fix

b58 rebuilds the runtime layout/NVS firmware using the proven b55 Tag build path.

Marker:

```text
alt-bcast-b58-rtlayout-nvsfix-b55base-g1200-r1000-rms0
```

Verified actual Tag app cache:

```text
APP_ALT_SS_TWR_ENABLE=1
APP_ALT_SS_TWR_BCAST_ENABLE=1
APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP=1
APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE=1
APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE=1
APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE=1
APP_ALT_SS_TWR_GUARD_US=1200
APP_ALT_SS_TWR_RESP_SPACING_US=1000
APP_TAG_TDMA_SLOT_PERIOD_MS=10
APP_TAG_TDMA_SLOT_COUNT=10
APP_TAG_OUTPUT_FILTER_RMS_MM=0
```

OTA result:

```text
BSF66F match=True
BS2DCE match=True
BSDC91 match=True
```

APOS persistence spot-check after b58:

```text
BSDC91 APOS_STATUS_DONE SRC=SETTINGS
coordinates match AutoPos V3 result
```

## 60s Motion Validation

Capture directory:

```text
logs/motion_3tag_after_autopos_v3_anchor_only_b58_20260502_213951
```

Anchor preflight:

```text
ready=8/8
```

Result:

```text
positions_all=1672
tf_all=0
listener UF=3190
listener UL=686
listener UF code: 0xe0=2504, 0xe1=686
```

Per-tag:

```text
BS2DCE: 599 rows, 9.98 Hz, RMS median/p95/p99/max = 131/278/365/671 mm
BSDC91: 600 rows, 10.00 Hz, RMS median/p95/p99/max = 136/262/312/448 mm
BSF66F: 473 rows, 7.88 Hz over full 60s, RMS median/p95/p99/max = 174/205/217/248 mm
```

BSF66F started late in this capture:

```text
first TS at 6.19s
last TS at 59.95s
bins: 0-10s=15, 10-20s=63, 20-30s=96, 30-60s about 100 per 10s
```

Anchor sets:

```text
ABCDEFGH: 913
ABCDEFG: 683
```

Comparison to frozen b55 300s baseline:

```text
BS2DCE RMS median/p95: 292/431 -> 131/278 mm
BSDC91 RMS median/p95: 279/471 -> 136/262 mm
BSF66F RMS median/p95: 132/183 -> 174/205 mm
```

## Conclusion

Runtime layout NVS path works, AutoPos V3 anchor-only coordinates can be pushed to Tags, and the b58 broadcast baseline restores UWB operation with the runtime layout path included.

AutoPos V3 coordinates improved the two Roto Tags substantially. BSF66F is slightly worse than the old layout in this 60s run and also started late, so it should be rechecked in a longer capture before retuning RMS/speed gates.

## V3 Free / Weaker Geometry Prior Test

Follow-up reason:

```text
B-D and B-E should not be discarded as simple outliers.
The current V3-box rectangular prior is too strong for the real space.
Keep the two-layer height constraint, but do not force E-A/F-B/G-C/H-D
to be perfect vertical columns.
```

Bidirectional sweep audit showed that B-D and B-E are internally stable:

```text
B-D: B->D mean=5379.0 sd=27.1, D->B mean=5406.3 sd=40.5, direction delta=-27.3 mm
B-E: direction delta=-2.1 mm
```

So the large V3-box residuals are more likely geometry-prior mismatch than bad raw edges.

Tested V3-free variants:

```text
free_nominal       rms_edges=109.0  inlier=66.5
free_loose_planes  rms_edges=106.2  inlier=62.8
free_no_robust     rms_edges=91.5   inlier=91.5
free_loose_bias    rms_edges=106.8  inlier=54.2
free_very_loose    rms_edges=76.1   inlier=76.1
```

`free_very_loose` had the lowest matrix RMS but looked overfit: lower-plane C moved up to about 549 mm and the height bands became less physically plausible. For runtime testing, `free_no_robust` was selected because it preserved a plausible two-layer structure while allowing the room to be non-rectangular.

Pushed `free_no_robust` coordinates to all three Tags via APOS + APOS_COMMIT:

```text
APOS 0 0 0 0
APOS 1 4688 0 0
APOS 2 4067 3673 77
APOS 3 -285 2683 0
APOS 4 31 -46 1733
APOS 5 4428 -6 1612
APOS 6 3917 3701 1683
APOS 7 -510 2741 1589
```

Capture directory:

```text
logs/motion_3tag_after_autopos_v3_free_no_robust_20260502_222929
```

Anchor preflight:

```text
ready=8/8
```

Result:

```text
positions_all=1802
tf_all=0
```

Per-tag:

```text
BSF66F: 600 rows, 10.00 Hz, RMS median/p95/p99/max = 290/343/372/405 mm
BS2DCE: 602 rows, 10.03 Hz, RMS median/p95/p99/max = 149.5/298/353/470 mm
BSDC91: 600 rows, 10.00 Hz, RMS median/p95/p99/max = 137.5/275/388/557 mm
```

Anchor sets:

```text
ABCDEFGH: 1168
ABCDEFG: 604
```

Comparison to V3-box runtime layout:

```text
           V3-box med/p95       V3-free-no-robust med/p95
BS2DCE     131 / 278 mm         149.5 / 298 mm
BSDC91     136 / 262 mm         137.5 / 275 mm
BSF66F     174 / 205 mm         290 / 343 mm
```

Interpretation:

```text
V3-free-no-robust improves the matrix solve RMS and restores perfect
3-tag throughput, but it does not improve Tag-side residuals in this
runtime capture. BSF66F is significantly worse, while the two Roto Tags
are roughly similar or slightly worse than V3-box.
```

Current decision:

```text
Do not freeze V3-free-no-robust as the runtime layout yet.
Use it as evidence that the box prior is too strict, but tune the weak
geometry prior further and validate by Tag-side RMS, not by matrix RMS alone.
```

## V3 Free Loose Planes Runtime Test

Reason for this candidate:

```text
free_no_robust gave better matrix RMS, but moved the lower layer more
aggressively and made BSF66F runtime residual much worse.

free_loose_planes keeps the high-DOF/non-box geometry model, but uses
stronger lower/upper height-band priors. It does not force E-A/F-B/G-C/H-D
to be perfect vertical columns.
```

Pushed `free_loose_planes` coordinates to all three Tags via APOS + APOS_COMMIT:

```text
APOS 0 0 0 0
APOS 1 4738 0 0
APOS 2 3986 3719 34
APOS 3 -455 2738 0
APOS 4 66 -44 1735
APOS 5 4411 71 1552
APOS 6 3851 3760 1640
APOS 7 -553 2722 1561
```

APOS push notes:

```text
BSF66F: APOS_STATUS confirmed all coordinates.
BSDC91: APOS_STATUS confirmed all coordinates.
BS2DCE: APOS 2 initially failed once with rc=-128 / send failed -120,
        then was resent and APOS_STATUS confirmed all coordinates.
```

Capture directory:

```text
logs/motion_3tag_after_autopos_v3_free_loose_planes_20260502_225300
```

Anchor preflight:

```text
ready=8/8
```

Result:

```text
positions_all=1797
tf_all=0
```

Per-tag:

```text
BS2DCE: 599 rows, 9.98 Hz, RMS median/p95/p99/max = 103/276/322/366 mm
BSDC91: 599 rows, 9.98 Hz, RMS median/p95/p99/max = 97/224/272/417 mm
BSF66F: 599 rows, 9.98 Hz, RMS median/p95/p99/max = 225/257/272/302 mm
```

Comparison:

```text
                V3-box            free_no_robust      free_loose_planes
BS2DCE RMS      131 / 278         149.5 / 298         103 / 276
BSDC91 RMS      136 / 262         137.5 / 275         97 / 224
BSF66F RMS      174 / 205         290 / 343           225 / 257
positions_all   1672              1802                1797
```

Interpretation:

```text
free_loose_planes is the best weak-prior candidate so far for the two
Roto Tags: both median RMS and p95 improve versus V3-box.

BSF66F is still worse than V3-box, but much less bad than free_no_robust.
Because the V3-box BSF66F run started late and had only 473 rows, this
needs a longer apples-to-apples validation before final freeze.
```

Current active runtime Tag layout:

```text
All three Tags are currently on free_loose_planes APOS coordinates.
```

Current recommendation:

```text
Use free_loose_planes as the active high-DOF candidate.
Next, run a longer 120s/300s comparison, or tune one more candidate between
V3-box and free_loose_planes to reduce BSF66F RMS while preserving the Roto
improvement.
```
## APOS Verified Forwarding Checkpoint - 2026-05-03

APOS layout deployment now has OTA-style verification.

Fix:

- Added Master_Tag command `APOS_TO <BSxxxx> APOS ...` in `apps/master_control/src/main.c`.
- Updated `scripts/push_apos_layout_verified.py` to require target-specific `APOS_OK`, `APOS_COMMIT_OK`, and `APOS_STATUS` readback.
- Generic `NUS notify` / `BLE[...]` lines are not accepted as proof.
- Naked `apos rc` is not enough to claim success.

Deployment:

- Rebuilt and flashed Master_Tag B120 only.
- SNR: `1050070698`
- Build: `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b61-apos-verify-carrier`
- LFRC assert passed.
- No Tag OTA.
- No Anchor OTA.

Verified run:

```text
logs/apos_verified_b61_all3_apos_to_20260503_004436/summary.json
```

Result:

```text
APOS_VERIFY target=BSF66F rows_ok=1 commit_ok=1 layout_match=True source=SETTINGS
APOS_VERIFY target=BS2DCE rows_ok=1 commit_ok=1 layout_match=True source=SETTINGS
APOS_VERIFY target=BSDC91 rows_ok=1 commit_ok=1 layout_match=True source=SETTINGS
APOS_VERIFY_ALL layout_match=True
```

Rule going forward:

```text
APOS deployment is successful only if rows_ok=1, commit_ok=1, layout_match=True, source=SETTINGS.
```

## Master_Tag TDMA10 Validation After Verified APOS - 2026-05-03

The verified APOS push succeeded, but the first 3-tag capture showed BSF66F
at zero positions while BS2DCE/BSDC91 were healthy:

```text
logs/motion_3tag_after_verified_apos_free_loose_b61_20260503_094322
positions_all=1200
BSF66F=0
BS2DCE=602
BSDC91=598
TR=14408
CM/CS/CR/CF=0
```

A BSF66F-only probe passed:

```text
logs/probe_BSF66F_only_after_verified_apos_b61_20260503_094713
positions_all=300 / 30s
TR=2400
TR valid=2306
all 8 anchors visible
```

This isolated the issue to 3-tag TDMA scheduling, not APOS or BSF66F hardware.
The Master_Tag carrier was still issuing old weighted TDMA timing:

```text
period=40ms active=24ms
```

while b61 Tags use lightweight TDMA:

```text
lperiod=10ms lcount=10
```

Master_Tag was rebuilt and flashed with:

```text
Build: build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b61-apos-verify-tdma10-carrier
SNR: 1050070698
APP_MASTER_TDMA_SLOT_PERIOD_MS=10
APP_MASTER_TDMA_SLOT_ACTIVE_MS=9
LFRC assert: passed
```

Validation after the carrier fix:

```text
logs/motion_3tag_after_master_tdma10_b61_20260503_095248
positions_all=1602 / 60s
TR=12808
TR valid=12175
TF=0
CM/CS/CR/CF=0
```

Per-tag:

```text
BSF66F positions=501, RMS median/p95/max=156/196/236 mm
BS2DCE positions=551, RMS median/p95/max=105/223/356 mm
BSDC91 positions=550, RMS median/p95/max=105/234/457 mm
```

Conclusion:

```text
Verified APOS layout is installed and persisted on all three Tags.
TR/TS/TF architecture is clean: CM/CS/CR/CF are zero.
Master_Tag TDMA is now aligned to b61 lightweight TDMA.
BSF66F zero-position failure is resolved.
Remaining work is improving rate/stability from 1602/1800 toward 1800/60s.
```
