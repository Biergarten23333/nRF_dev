# Codex Handoff Memory: BioSpur UWB / AutoPos

This file is a compact memory export for continuing this project on another computer or in another Codex session.

Read this first, then read:

- `autopos_pipeline/offline_test_motice/ERLANGEN_20260519_BASELINE_CAPTURE_FREEZE.md`
- `autopos_pipeline/offline_test_motice/ERLANGEN_OPTITRACK_COMMANDS.md`
- `autopos_pipeline/outdoor_20260513/reports/README_FINAL.md`
- `autopos_pipeline/outdoor_20260513/reports/autopos_20260513_report_10p.tex`
- `autopos_pipeline/outdoor_20260513/reports/autopos_20260513_summary_2p.md`

## 1. Current Project State

The project is BioSpur UWB / AutoPos.

AutoPos estimates the 3D layout of UWB anchors from inter-anchor ranging alone. The long-term goal is to reduce setup burden for UWB-based wearable motion capture: anchor positions should be self-calibrated rather than manually surveyed.

The current focus is preparing and validating an AutoPos paper concept. The latest major dataset is the 2026-05-13 outdoor 8-anchor broadcast SS-TWR experiment.

Two reports were prepared and sent to Prof. Björn Eskofier:

- `Report-Complete-Version-17052026.pdf`
- `Short-Summary-17052026.pdf`

Prof. Eskofier replied positively and supports an optical measurement-system validation. Munich is not yet set up; Erlangen is likely the easier option because measurement equipment exists there.

## 1A. Latest Erlangen Baseline Freeze: 2026-05-19

This is the newest working field baseline before moving the whole folder to the
experiment laptop. Use this first in a new Codex session.

Primary freeze document:

- `autopos_pipeline/offline_test_motice/ERLANGEN_20260519_BASELINE_CAPTURE_FREEZE.md`

Session root:

```text
/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/offline_test_motice/erlangen_20260519_110221
```

Current broadcast timing baseline:

```text
tail900 start5
A 1200 us
B 2200 us
C 3200 us
D 4200 us
E 5200 us
F 6100 us
G 7000 us
H 7900 us
```

Current working Master ports on the desktop where this was frozen:

```bash
export BIOSPUR_ANCHOR_PORT="/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00"
export BIOSPUR_TAG_PORT="/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00"
export BIOSPUR_ANCHOR_SNR="960148546"
export BIOSPUR_TAG_SNR="1050070698"
```

Important for another computer:

- Re-detect serial ports. Do not assume the `/dev/serial/by-id/...` paths are identical.
- Keep SNR values the same:
  - `Master_Anchor`: `960148546`
  - `Master_Tag`: `1050070698`
- Capture commands are now TR-only. Do not use old command logic that separates
  `static`, `roto`, and `motion` profiles.
- Use:
  - `--targets "BS....,BS...."`
  - `--tr-hz 10`
- Do not rely on `--profiles`, `--static-hz`, `--roto-hz`, or `--motion-hz` for
  the current field capture workflow.

The compatibility wrapper was updated:

- `SS-TWR/alt-SS-TWR/broadcast/scripts/run_dual_master_tdma_capture.py`

It now forwards only the supported lower-level capture arguments:

- `--targets`
- `--tr-hz`

Deprecated profile arguments are ignored by the wrapper for compatibility.

### Validated 2026-05-19 Captures

AutoPos sweep:

```text
autopos_sweep1000_prewarm10_us30/sweep1000
summary.json: success true
order: ABCDEFGH
formal SW sets per round: 1000
device SW sets per round: 1010
prewarm setting: 10
final responder restore: success true, sent=8 ready=8/8
```

Note: the folder name contains `us30`, but no `ultrasound_H.csv` was found in
that folder. If the H ultrasound value is needed for the field dataset, run a
standalone US30 and save it under an explicit folder name.

BSF66F 120 s:

```text
folder: BSF66F_120s_20260519_113311
raw_log: tag_capture_20260519_113401/raw.log
tr_all_csv: tag_capture_20260519_113401/tr_all.csv
success: true
TR rows: 9608
valid TR rows: 9540
sweeps_total: 1201
>=7 anchors: 1201 / 1201 = 100.00%
8/8 anchors: 1133 / 1201 = 94.34%
US residual in raw log: none
```

Roto 2 Tag 120 s:

```text
folder: roto_BS2DCE_BSDC91_120s_20260519_114009
raw_log: tag_capture_20260519_114057/raw.log
tr_all_csv: tag_capture_20260519_114057/tr_all.csv
success: true
targets: BS2DCE, BSDC91
TR rows: 19200
valid TR rows: 19045
sweeps_total: 2400
>=7 anchors: 2398 / 2400 = 99.92%
8/8 anchors: 2247 / 2400 = 93.63%
US residual in raw log: none
```

Wand 3 Tag 120 s:

```text
folder: wand3_BS9336_BS955A_BSCCF4_120s_20260519_114436
raw_log: tag_capture_20260519_114525/raw.log
tr_all_csv: tag_capture_20260519_114525/tr_all.csv
success: true
targets: BS9336, BS955A, BSCCF4
TR rows: 28816
valid TR rows: 28616
sweeps_total: 3602
>=7 anchors: 3599 / 3602 = 99.92%
8/8 anchors: 3405 / 3602 = 94.53%
US residual in raw log: none
```

Do not use this failed old-profile folder:

```text
BSF66F_static_120s_20260519_111929
```

It failed because an old command passed unsupported profile arguments to the
lower-level capture script. The hardware was not the cause.

## 2. Critical Safety / Hardware Rules

These rules matter a lot.

- Do not flash `Master_Anchor` or `Master_Tag` unless explicitly requested.
- `Master_Anchor` SNR: `960148546`.
- `Master_Tag` SNR: `1050070698`.
- Never use `nrfjprog`; use the repository J-Link scripts with explicit SNR.
- For B120 master-control builds, always use internal LFRC oscillator on both CPUAPP and CPUNET.
- Required B120 clock config:
  - `CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y`
  - `CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC_CALIBRATION=y`
  - `CONFIG_CLOCK_CONTROL_NRF_K32SRC_XTAL` not set
  - `CONFIG_CLOCK_CONTROL_NRF_K32SRC_SYNTH` not set
- Before any B120 flash, verify with:
  - `scripts/assert_b120_internal_osc_build.sh <build-dir-or-image>`
- Preferred B120 build scripts:
  - `scripts/build_master_control_b120_m1_internal_osc.sh`
  - `scripts/build_master_control_b120_m1.sh`

For the Erlangen experiment, the plan is to collect data, not to flash firmware.

## 3. Broadcast vs Earlier 4-Anchor Online Context

The April 2026 paper concept showed that antenna-delay-aware calibration is important:

- V1 no-delay baseline: about `132 mm` positioning standard deviation.
- V3-full delay-aware solver: about `50 mm`.
- Improvement: about `2.7x`, mostly in Z.

But that earlier concept also had a confusing `100 mm+` Z degradation regime under an online 4-anchor selector.

Current interpretation:

- The earlier online setting was dominated by 4-anchor fixes.
- If one response was missing, the solver could fall to near-minimal 3-anchor geometry.
- The 2026-05-13 broadcast dataset changes the redundancy regime.
- In the current static dataset:
  - `68.0%` of solve-eligible epochs use 7 anchors.
  - `31.9%` use all 8 anchors.
- Therefore, typical degradation is 8 anchors to 7 anchors, not 4 anchors to 3 anchors.
- Keep-k robustness confirms that 7-anchor operation remains close to baseline, while 4/5-anchor operation enters the `100 mm+` degradation regime.

Broadcast SS-TWR also reduces protocol-induced timing inconsistency for moving tags because multiple anchors respond within the same broadcast epoch. This makes RotoArm validation cleaner than independent pairwise polling for moving tags.

## 4. Main 2026-05-13 Dataset

Dataset:

- 8 DWM1001C anchors.
- 4 lower anchors: A-D.
- 4 upper anchors: E-H.
- Outdoor line-of-sight setting.
- Broadcast SS-TWR.
- No OptiTrack ground truth.

Coverage:

- Static Tag: 23 sessions.
  - ID01-ID09, ID11-ID24.
  - ID10 was not captured.
- RotoArm: 17 sessions.
  - ID25-ID41.
- Wand: 5 sessions.
  - W01-W04 are static rigid-wand captures.
  - W05 is mainly dynamic coverage / diagnostic.

Important caveat:

- No OptiTrack ground truth exists for 2026-05-13.
- Do not call current positioning values absolute accuracy.
- Use repeatability, consistency, or positioning standard deviation.

## 5. Anchor Layout

Current live anchor hardware identity map after the 2026-05-18 H hardware replacement:

| Anchor | BS code | J-Link SNR | Device UUID |
| --- | --- | ---: | --- |
| A | BS1FFC | 760184781 | F3BB7A04104F9CB8561DDDACB9E53714 |
| B | BS592A | 760185876 | B9179575C776C98F1CB132DD6EDC6223 |
| C | BS5380 | 760185878 | CEE5A7EFCB35F8A56B430047629F5309 |
| D | BS20AC | 760184974 | B2B5FA625534A8C617135DCAFC9E036A |
| E | BS4B52 | 760185904 | A892AF05DD59CF0D0D3408AD74F364A1 |
| F | BS928B | 760186124 | 840C68591E90019821AACFF1B73AAA34 |
| G | BSEC88 | 760185889 | B3087BC3D87CCCD316AEDC6B71D6677F |
| H | BS506D | 760184500 | B1E487C2B1FD740D1442206A1857DFA1 |

H was replaced as a hard hardware update. The old H was `BSB77F`, SNR `760184753`, UUID `CF12E703AC1A118F6AB440AB05B0BA23`; do not use it for new AutoPos experiments unless explicitly rolling back. The new H was flashed with the frozen `altbcast-responder-a18-g1200-r1000-20260512_154806` image and provisioned as `ANCHOR_ID=H`, `ROLE=responder`, `generation=4`.

V4-io recovered layout:

- Footprint: about `3.17 m x 4.78 m`.
- Z span: about `1.74 m`.
- Average layer separation: about `1.51 m`.

Physical reporting convention:

| Anchor | Layer | X mm | Y mm | Z mm | Delay equiv mm |
| --- | --- | ---: | ---: | ---: | ---: |
| A | lower | 0.0 | 0.0 | 0.0 | 0.0 |
| B | lower | 2961.0 | 0.0 | 0.0 | 20.2 |
| C | lower | 3167.2 | 4507.1 | 0.0 | 32.3 |
| D | lower | 191.7 | 4650.6 | -70.9 | 20.7 |
| E | upper | 106.8 | -103.5 | 1441.4 | 5.9 |
| F | upper | 2882.6 | -14.6 | 1418.2 | -2.4 |
| G | upper | 2958.8 | 4672.3 | 1673.5 | 1.8 |
| H | upper | 39.4 | 4623.6 | 1420.8 | -0.7 |

There was once confusion about layout orientation. The correct physical interpretation is:

- A-D are lower.
- E-H are upper.

## 6. Main Results / Numbers

V4-io FULL-COMPARE-1000:

- Layout residual RMS: `44.3 mm`.
- Layout p95: `87.7 mm`.
- Static 3D median: `49.2 mm`.
- Static 3D p95: `81.6 mm`.
- Static Z median: `37.9 mm`.
- Roto dR RMS: `32.3 mm`.
- Roto turn-center median: `20.6 mm`.

Across splits:

| Dataset | Layout RMS | Layout p95 | Static 3D med | Static 3D p95 | Roto dR RMS |
| --- | ---: | ---: | ---: | ---: | ---: |
| FULL-COMPARE-1000 | 44.3 | 87.7 | 49.2 | 81.6 | 32.3 |
| FULL-COMPARE-500 | 44.7 | 89.2 | 48.4 | 80.9 | 33.0 |
| FULL-COMPARE-500+500 | 44.2 | 88.5 | 48.4 | 80.6 | 33.4 |

Interpretation:

- Layout generation is stable across 1000 / 500 / 500+500.
- Static repeatability is around `49 mm` median.
- Z is the dominant weak axis.

## 7. XYZ / Z Weakness

V4-io XYZ breakdown:

| Dataset | X med | Y med | Z med | 3D med | Z variance share |
| --- | ---: | ---: | ---: | ---: | ---: |
| FULL-COMPARE-1000 | 26.0 | 16.3 | 37.9 | 49.2 | 62.1% |
| FULL-COMPARE-500 | 26.4 | 16.5 | 39.9 | 48.4 | 63.8% |
| FULL-COMPARE-500+500 | 26.3 | 16.6 | 40.7 | 48.4 | 63.7% |

Interpretation:

- The current limitation is not isotropic noise.
- Z contributes about `62-64%` of 3D variance.
- Vertical observability / geometry is the main weakness.

Spatial grouping:

- High height: Z med `29.3 mm`, 3D med `41.6 mm`.
- Low height: Z med `49.9 mm`, 3D med `59.7 mm`.
- CDHG-facing: Z med `56.0 mm`, 3D med `67.8 mm`.

Important caveat:

- CDHG degradation is an observed geometry-linked pattern, not a proven physical mechanism.
- Need GDOP/VDOP map to explain it mechanistically.

## 8. Strict 8/8 Diagnostic

Strict 8/8 means only frames with all 8 anchors present are kept.

All-available:

- Frames: `13817`.
- X med: `26.0 mm`.
- Y med: `16.3 mm`.
- Z med: `37.9 mm`.
- 3D med: `49.2 mm`.
- 3D RMS: `54.8 mm`.
- 3D p95: `81.6 mm`.

Strict 8/8:

- Frames: `4408`.
- Retention: `31.9%`.
- X med: `23.4 mm`.
- Y med: `14.8 mm`.
- Z med: `37.4 mm`.
- 3D med: `44.5 mm`.
- 3D RMS: `49.4 mm`.
- 3D p95: `67.0 mm`.

Interpretation:

- Strict 8/8 improves X/Y and tail.
- Z median remains almost unchanged: `37.9 -> 37.4 mm`.
- Therefore, missing-anchor frames add horizontal/tail degradation, but persistent Z weakness is geometry-driven, not simply availability-driven.

Do not invent strict 8/8 Z variance share unless computed from per-capture strict8 data. Earlier an invalid `67.2%` was removed because it was computed from medians incorrectly.

## 9. Position Cloud Figure

A new static position-cloud figure was generated:

- `autopos_pipeline/outdoor_20260513/reports/static_position_cloud_examples.png`
- Script:
  - `autopos_pipeline/outdoor_20260513/reports/static_position_clouds/build_static_position_clouds.py`
- CSV:
  - `autopos_pipeline/outdoor_20260513/reports/static_position_cloud_examples_points.csv`

Sessions:

- ID06 compact example:
  - X/Y/Z/3D: `21.5 / 14.3 / 20.1 / 32.7 mm`
- ID08 worst example:
  - X/Y/Z/3D: `42.5 / 30.3 / 71.1 / 88.2 mm`
- ID07 weak CDHG:
  - X/Y/Z/3D: `27.5 / 18.8 / 68.2 / 75.9 mm`

The figure shows:

- Good session is compact.
- Weak/CDHG sessions show strong Z elongation, especially in XZ/YZ projections.
- This supports the interpretation that the error is not isotropic; the weak cases are mainly vertical.

Important detail:

- The final figure uses correct axis labels per subplot.
- Row 1: XY.
- Row 2: XZ.
- Row 3: YZ.

## 10. RotoArm

RotoArm setup:

- Two UWB tags mounted on the same rotating arm.
- Mechanical radius difference: `120 mm`.

V4-io:

- dR RMS: `32.3 mm`.
- turn-center median: `20.6 mm`.
- dR mean is negative, about `-24.3 mm`, meaning reconstructed outer-inner radius difference is smaller than mechanical reference.

Reporting guidance:

- It is okay to report turn-center median `~21 mm`.
- Do not over-emphasize the negative dR mean in emails; it opens explanation burden.
- In reports, mention it cautiously as radius-difference offset requiring OptiTrack/mechanical validation.

RotoArm injection:

- V4-io-roto uses RotoArm soft constraints.
- It improves Roto consistency and static tail.
- But Roto metrics are not independent holdout for V4-io-roto because Roto data are injected.

## 11. Wand

Wand setup:

- Rigid three-tag calibration wand.
- Tape-measured inter-tag distances.
- W01-W04 are four static wand captures at different positions/orientations.
- W05 is dynamic coverage/diagnostic.

V4-io as ordinary tag solve:

- Wand pairwise distance bias RMS: `59.3 mm`.

There was a previous confusion:

- `55.7 mm` is the statistical median of absolute biases from CSV.
- Some README text had `59.4 mm` due to a different summary convention.
- Formal report uses `59.3 mm RMS` to avoid ambiguity.

Wand injection:

- V4-io-wand uses W01-W04 soft rigid-body constraints.
- It slightly improves static tail.
- It is useful as ablation/consistency, but not the main result.

Measurement-paper issue:

- Wand bias mechanism is not yet explained.
- Need per-edge/per-position bias decomposition and OptiTrack validation.

## 12. Roto/Wand Injection Table

| Variant | Injected info | Static 3D med | Static 3D p95 | Roto dR RMS | Turn-center med |
| --- | --- | ---: | ---: | ---: | ---: |
| V4-io | none | 49.2 | 81.6 | 32.3 | 20.6 |
| V4-io-roto | rotating-arm soft constraints | 48.1 | 71.9 | 29.9 | 18.0 |
| V4-io-wand | W01-W04 wand soft constraints | 48.6 | 77.3 | 31.7 | 19.1 |

Interpretation:

- Roto/Wand constraints modestly reduce tail metrics.
- They do not fundamentally change median repeatability.
- Main production baseline remains inter-anchor-only V4-io.

## 13. Keep-k Robustness

Random keep-k robustness:

| Keep anchors | Z med | 3D med | 3D p95 |
| ---: | ---: | ---: | ---: |
| 8 | 37.9 | 49.2 | 81.6 |
| 7 | 43.6 | 53.2 | 105.6 |
| 6 | 60.9 | 77.1 | 166.5 |
| 5 | 83.4 | 100.7 | 225.2 |
| 4 | 124.6 | 156.3 | 355.3 |

Interpretation:

- 7-anchor operation remains close to all-available baseline.
- keep-6 is the turning point where tail clearly degrades.
- keep-5 / keep-4 enter `100 mm+` regime.
- This explains earlier 4-anchor online selector degradation.

Fail rate caveat:

- `fail rate = 0%` does not mean good positioning quality.
- The solver can return numerically valid but degraded positions.

## 14. Dropout and Anchor Diagnostics

Independent dropout:

| Dropout | Solved rate | Fail rate | Z med | 3D med | 3D p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| p05 | 100.0% | 0.0% | 46.9 | 59.5 | 115.5 |
| p10 | 99.8% | 0.2% | 55.7 | 69.4 | 142.5 |
| p20 | 97.5% | 2.5% | 70.9 | 87.9 | 188.4 |
| p30 | 89.5% | 10.5% | 83.8 | 104.8 | 225.0 |
| p40 | 74.7% | 25.3% | 94.9 | 119.0 | 285.3 |

Leave-one-out:

| Condition | Z med | 3D med | 3D p95 | Meaning |
| --- | ---: | ---: | ---: | --- |
| baseline | 37.9 | 49.2 | 81.6 | all-available |
| no_B | 45.2 | 58.4 | 78.1 | worst 3D median |
| no_C | 45.5 | 56.4 | 68.6 | worst Z median |
| no_E | 41.2 | 51.1 | 80.6 | E tail large but not largest median effect |
| no_H | 37.4 | 49.0 | 73.4 | H already low availability |

Residual diagnostics:

- E has largest residual tail:
  - abs p95 `212.7 mm`.
  - downweighted `45.3%`.
- B has high residual RMS:
  - RMS `101.7 mm`.
  - abs p95 `196.9 mm`.
- H has low availability / low-Q:
  - observations `4479`, much fewer than other anchors.
  - low-Q rate `90.6%`.

Do not say “E is bad” too simplistically.

Better:

- E = residual-tail anchor.
- B/C = geometry influential anchors.
- H = availability / low-Q issue.

B/E mechanism caveat:

- B and E lie on the high-X side of layout.
- This suggests targeted field inspection of that side.
- It does not prove a common physical cause.

## 15. FIM / Candidate Anchor

FIM simulation is a geometric observability simulation, not empirical validation.

Assumptions:

- unbiased Gaussian range model.
- range sigma about `50 mm`.
- no NLOS.
- no antenna pattern.
- no synchronization/TDMA availability.
- no installation error.

Z factor definition:

- Z factor = baseline predicted Z uncertainty / predicted Z uncertainty after adding candidate anchor.
- Factor > 1 means improvement.
- Factor < 1 means worse predicted Z observability at that evaluation point.
- Median factor = typical improvement.
- p05 factor = lower-tail / worst-region behavior.

Candidate table:

| Candidate | x | y | z | median Z factor | p05 factor |
| --- | ---: | ---: | ---: | ---: | ---: |
| center_low_level | 1538 | 2292 | 71 | 3.54 | 0.11 |
| center_extra_high | 1538 | 2292 | -2473 | 3.32 | 1.76 |
| center_high_level | 1538 | 2292 | -1673 | 2.15 | 0.23 |
| center_mid_level | 1538 | 2292 | -735 | 1.50 | 0.08 |

Interpretation:

- center_low_level has strongest median improvement but p05 `0.11`, meaning some lower-tail regions get worse.
- center_extra_high is more robust because p05 `1.76 > 1`.
- Any real ninth-anchor deployment must also check broadcast timing and installation constraints.

## 16. Reports and PDFs

Current report files:

- `autopos_pipeline/outdoor_20260513/reports/README_FINAL.md`
- `autopos_pipeline/outdoor_20260513/reports/autopos_20260513_report_10p.tex`
- `autopos_pipeline/outdoor_20260513/reports/autopos_20260513_summary_2p.md`
- `autopos_pipeline/outdoor_20260513/reports/review_checklist.md`

PDFs sent / reviewed:

- `Report-Complete-Version-17052026.pdf`
- `Short-Summary-17052026.pdf`

Important:

- Full report is 11 pages, not exactly 10.
- Short summary is 3 pages, not exactly 2.
- Therefore, call them “complete technical report” and “short executive summary”, not “10-page” and “2-page” in emails.

Both PDFs were checked:

- Core numbers are consistent.
- No hard misuse of absolute accuracy.
- Figure numbering fixed in final version.
- Full and short reports can be sent together.

## 17. Email to Prof. Eskofier

Final email was roughly:

- Update about AutoPos paper concept.
- Implemented Broadcast SS-TWR, Rotating-Arm validation, Rigid-Body Calibration Wand as information / constraint source.
- Static 3D repeatability about `49 mm` median.
- Anchor layout stable across data splits.
- Rotating-arm turn centers repeat about `21 mm` median.
- No OptiTrack, so no absolute accuracy yet.
- Request OptiTrack validation.
- Mention 10 tags at 10 Hz stress test supports scalability.

Prof. Eskofier replied:

- He supports optical measurement validation.
- Munich is not set up yet and may take time.
- He knows systems in Munich but that may require coordination.
- Erlangen measurement equipment is available.
- He asked what would be easier.

Current preferred response:

- Erlangen is probably easiest and most reliable.
- The 8-anchor UWB system and tags can be brought there.
- Need compact setup: static Tag positions, short RotoArm measurement, optional Calibration Wand.
- Goal: connect repeatability/consistency metrics to optical ground truth.
- Offer to prepare a short measurement plan.

## 18. Erlangen Experiment

Command checklist:

- `autopos_pipeline/offline_test_motice/ERLANGEN_OPTITRACK_COMMANDS.md`

Main field goals:

1. AutoPos sweep1000.
2. Static Tag poses with OptiTrack.
3. RotoArm with OptiTrack.
4. Calibration Wand static captures with OptiTrack.
5. Optional dynamic Wand.
6. Optional 10-tag stress.

At field site:

- Re-detect serial ports on that computer.
- Do not assume `/dev/serial/by-id/...` paths are same as current laptop.
- Set:
  - `BIOSPUR_ANCHOR_PORT`
  - `BIOSPUR_TAG_PORT`
  - `BIOSPUR_ANCHOR_SNR=960148546`
  - `BIOSPUR_TAG_SNR=1050070698`
- Use broadcast scripts under:
  - `SS-TWR/alt-SS-TWR/broadcast/scripts/`

The AutoPos sweep command uses `BIOSPUR_ANCHOR_PORT` because AutoPos sweep is controlled by `Master_Anchor`.

## 19. Future Measurement-Paper Analyses

Current internal report is strong, but not yet a final Measurement paper.

Needed for publication-level explanation:

1. OptiTrack absolute validation.
2. VDOP/GDOP map over test volume.
3. Temporal drift analysis across static sessions.
4. Wand bias decomposition:
   - per-edge
   - per-position
   - orientation dependence
5. RotoArm inner/outer tag residual split.
6. OptiTrack-based evaluation of RotoArm and Wand injection.
7. Possibly investigate B/E high-X side residual structure physically.

Do not pretend these are already done.

In reports, phrase them as open explanatory diagnostics unless data are analyzed.

## 20. Useful Paths

Broadcast scripts:

- `SS-TWR/alt-SS-TWR/broadcast/scripts/run_autopos_sweep_loop.py`
- `SS-TWR/alt-SS-TWR/broadcast/scripts/run_dual_master_tdma_capture.py`
- `SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py`
- `SS-TWR/alt-SS-TWR/broadcast/scripts/verify_all_anchor_responder_runtime.py`
- `SS-TWR/alt-SS-TWR/broadcast/scripts/scan_and_map.py`

2026-05-13 analysis:

- `autopos_pipeline/outdoor_20260513/run_clean_full_compare.py`
- `autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/v4-io/layout.json`
- `autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/v4-io/static_all_captures.csv`
- `autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/tables/version_summary.csv`
- `autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/tables/roto_physical_consistency_summary.csv`
- `autopos_pipeline/outdoor_20260513/ROBUSTNESS/v4io_1000_static_robustness/README.md`

Report figures:

- `autopos_pipeline/outdoor_20260513/reports/setup_geometry/anchor_geometry_report.png`
- `autopos_pipeline/outdoor_20260513/reports/setup_geometry/static_anchor_count_distribution_pie.png`
- `autopos_pipeline/outdoor_20260513/reports/static_position_cloud_examples.png`
- `autopos_pipeline/outdoor_20260513/ROBUSTNESS/v4io_1000_static_robustness/figures/random_keep_k_z_3d.png`
- `autopos_pipeline/outdoor_20260513/ROBUSTNESS/v4io_1000_static_robustness/figures/leave_one_anchor_out_z_3d.png`
- `autopos_pipeline/outdoor_20260513/ROBUSTNESS/v4io_1000_static_robustness/figures/residual_abs_p95_by_anchor.png`

## 21. Style / Claim Guidance

Use these terms:

- repeatability
- consistency
- positioning standard deviation
- layout self-consistency
- physical consistency
- soft-constraint injection

Avoid unless OptiTrack is present:

- absolute accuracy
- absolute positioning accuracy
- ground-truth error

Say:

- “No OptiTrack ground truth was available.”
- “These are repeatability/consistency metrics.”
- “OptiTrack is required to convert repeatability analysis into absolute accuracy validation.”

Do not over-claim:

- Broadcast does not prove all motion error is eliminated.
- RotoArm/Wand are not replacement ground truth systems.
- FIM is not empirical validation.
- CDHG degradation is not yet fully explained.
- B/E residual structure is suggestive, not proven.

## 22. How To Continue In A New Codex Session

Suggested first prompt on another computer:

```text
Please first read:
autopos_pipeline/offline_test_motice/CODEX_HANDOFF_MEMORY.md
autopos_pipeline/offline_test_motice/ERLANGEN_20260519_BASELINE_CAPTURE_FREEZE.md
autopos_pipeline/offline_test_motice/ERLANGEN_OPTITRACK_COMMANDS.md

Then help me continue the BioSpur UWB / AutoPos Erlangen OptiTrack validation workflow.
Do not flash Master_Anchor or Master_Tag unless I explicitly ask.
Use broadcast SS-TWR scripts under SS-TWR/alt-SS-TWR/broadcast/scripts.
Use the 2026-05-19 TR-only capture command style: --targets ... --tr-hz 10.
Do not use old static/roto/motion profile arguments for current field capture.
```
