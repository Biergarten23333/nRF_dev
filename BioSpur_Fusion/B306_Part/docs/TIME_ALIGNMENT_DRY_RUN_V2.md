# Aligner v2 — R4 offline fix round

A listener-backed common axis exists for this R4 session, but the host-only production path is **not validated** because `master_arrival_ms` selects the wrong 110 ms integer for BSFC2CC while the listener gold standard identifies the correct one. Clean-band UWB timing jitter is 74.982–170.004 us σ, 172.975–294.707 us p95, and 370.740–607.269 us maximum across all ten boards. The listener-backed cross-board constants occupy only **197.755 us** from 18,377.624 to 18,575.379 us.

**Verdict: FAIL — the common physical time axis is demonstrated with listener evidence, but the preregistered 10/10 F3=F4 gate fails 9/10 and the host-only production aligner is not released.**

This was strictly offline. Fusion PCBs remained off; no serial port, J-Link
probe, radio, firmware, or pre-existing evidence file was touched. All new
files are confined to this `v2/` directory.

## Inputs and v1 baseline

The immutable R4 Fusion log is 291,707,476 bytes with SHA-256
`5db1033de15509717ce3ebe0377e8b8abbc9d0c478cad253a258f017480c9c35`.
The formal window is `171440.580907324` through `173240.581020483`
monotonic seconds (1800.000113 s). The listener merged index and summary retain
their preregistered hashes `ad1fcab3…` and `3d15525d…`; exact paths, sizes, and
hashes are in [`input_manifest.json`](input_manifest.json) and
[`SOURCE_INPUT_SHA256SUMS`](SOURCE_INPUT_SHA256SUMS).

The v1 report established four facts that this round had to answer:

1. about 10% of kind-1 records formed a +2.0–2.5 ms delivery band;
2. BSFC2CC's public `sweep` counted performed sweeps rather than elapsed epochs;
3. kind-1/kind-3 alone left a 73.035 ms arc of arbitrary board origins and no
   identified cross-board bridge; and
4. the nine valid per-board IMU remaps already placed 99% of intervals within
   0.083 us of 5 ms.

Those are cited from the immutable v1
[`ALIGN_DRYRUN_REPORT.md`](../ALIGN_DRYRUN_REPORT.md); they were not recomputed
as new premises.

## F1 — elapsed epochs reconstructed from time

For each board, consecutive `frame_rx_ts_us` gaps are rounded to the nearest
positive integer multiple of an iteratively refined period. The cumulative
multiples, rather than `sweep`, are the regressor. All deltas meet the
preregistered ±5 ms classification gate: the worst error is 2,963.831 us.

BSFC2CC now spans 16,362 elapsed epochs using 10,579 performed sweeps; 5,784
epochs have no performed record. Its fitted period is 110,001.635835 us
(+14.871 ppm), replacing v1's invalid 169.5 ms/sweep model. Across the fleet,
periods are 110,000.951343–110,001.831402 us (+8.649 to +16.649 ppm), all
crystal-plausible.

## F2 — delivery-band classification and mechanism verdict

Each board's preliminary residuals were split by deterministic two-component
classification. A 95% confidence interval was computed for the separation of
the component means. The fixed serialization hypothesis is exactly
`96 B × 10 bits / 460800 baud = 2,083.333 us`.

| BSF | slot | period us | ppm | worst gap error us | clean σ us | clean p95 us | clean max us | delayed % | delayed offset us (95% CI) | 2,083.333 in CI? |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| BSF3C79 | 1 | 110001.740738 | +15.825 | 2752.259 | 82.376 | 219.052 | 450.922 | 9.998 | 2302.175 [2297.297, 2307.052] | no |
| BSFEC35 | 2 | 110000.951343 | +8.649 | 2752.049 | 80.032 | 180.087 | 418.980 | 9.998 | 2345.179 [2340.400, 2349.957] | no |
| BSF44AD | 3 | 110001.611321 | +14.648 | 2851.389 | 91.415 | 220.382 | 447.460 | 9.998 | 2294.963 [2289.639, 2300.288] | no |
| BSF6C53 | 4 | 110001.831402 | +16.649 | 2963.831 | 85.502 | 216.524 | 442.026 | 9.998 | 2321.282 [2316.280, 2326.285] | no |
| BSF8BC4 | 5 | 110001.698806 | +15.444 | 2752.301 | 74.982 | 172.975 | 370.740 | 9.998 | 2344.076 [2339.532, 2348.621] | no |
| BSF1120 | 6 | 110001.676649 | +15.242 | 2776.677 | 81.699 | 196.437 | 437.678 | 10.000 | 2292.843 [2287.874, 2297.812] | no |
| BSF31CC | 7 | 110001.754828 | +15.953 | 2934.245 | 85.109 | 202.040 | 444.692 | 9.998 | 2323.101 [2318.306, 2327.895] | no |
| BSFAA61 | 8 | 110001.485963 | +13.509 | 2759.486 | 85.843 | 203.096 | 454.277 | 10.004 | 2295.806 [2290.752, 2300.860] | no |
| BSFB165 | 9 | 110001.572180 | +14.293 | 2742.428 | 84.649 | 204.545 | 396.629 | 10.004 | 2317.072 [2312.130, 2322.013] | no |
| BSFC2CC | 10 | 110001.635835 | +14.871 | 2813.364 | 170.004 | 294.707 | 607.269 | **14.869** | 2134.738 [2127.641, 2141.835] | no |

**Mechanism verdict:** none of the ten confidence intervals contains
2,083.333 us. The delayed component is therefore not consistent with exactly
one serialized 96-byte UART frame. Per the preregistered branch it is excluded,
not constant-corrected, in the final clock fits. Nine boards retain the
expected ~10% band; BSFC2CC's 14.869% is a separate measured deviation. All ten
clean-band p95 values pass 1 ms. Machine-readable values are in
[`table1_epoch_band_fits.csv`](table1_epoch_band_fits.csv).

## F3 — bounded DK-stamp integer choice

`master_arrival_ms` was used only once: after subtracting reconstructed nominal
epoch time and the configured slot, its session median selected one relative
110 ms integer per board. No fractional value, slope, drift, or sample time
comes from this stamp; it is then discarded.

The apparent rounding remainder is 0–13 ms, giving a nominal safety margin of
42–55 ms inside the ±55 ms boundary. That margin looks comfortable in
isolation, but the F4 audit proves it incomplete. Relative to BSF3C79,
BSFC2CC's observed median difference is −105 ms, so F3 rounds to −1. F4 proves
the true difference is −2; relative to that truth, the same statistic is
displaced by 115 ms. Modulo 110 ms it still leaves a reassuring 5 ms
remainder, which is why a modulo-only margin cannot detect this failure.

## F4 — listener gold standard and F3 audit

The raw collector summary says `pass=false`, but its only complaints are
missing LPD/LRD records from devices whose actual roles do not produce them:

| SNR | key | deployed role | records actually provided | use here |
|---|---|---|---|---|
| 760184545 | LHIGH | MAIN | LBTX + LBSTAT | main witness; not a passive poll observer |
| 760181725 | LCG | SLAVED | LBD + LBSTAT | sub witness; not a passive poll observer |
| 760184548 | LBF | OBSERVER | LBD + LPD + LRD + LSTAT | epoch labels |
| 760184753 | LAE | OBSERVER | LBD + LPD + LRD + LSTAT | epoch labels |
| 760184767 | LMID | OBSERVER | LBD + LPD + LRD + LSTAT | epoch labels |
| 760184784 | LDH | OBSERVER | LBD + LPD + LRD + LSTAT | epoch labels |
| 760184964 | LLOW | OBSERVER | LBD + LPD + LRD + LSTAT | epoch labels |

Thus the two collector failures are a role-validation quirk, not missing gold
data. Each passive observer's LBD beacon counter was fitted against that same
observer's unwrapped DW ticks; its LPD polls were then assigned absolute beacon
epochs and phases in the same clock domain. Measured observer periods are
110,000.251–110,000.548 us. Full counts and role evidence are in
[`listener_role_audit.json`](listener_role_audit.json).

The on-air Poll sequence and public sweep are independent counters. The relay8
source publishes `tag_relay6_public_sweep(local_sweep)`
(`UWB_Part/relay8-workspace/src/include/tag_relay6.h:22-29` and
`UWB_Part/relay8-workspace/src/src/ss_twr_init.c:385-396,3795-3804`), while the
Poll frame carries `ss_twr_init_frame_seq_nb`, incremented independently after
the RX window (`ss_twr_init.c:4087-4103,4400-4404,4669-4673`). Nine dense
boards therefore use the exact Poll-sequence seed. BSFC2CC has a fixed public
sweep minus Poll-sequence offset of +1 and a sparse 5,784-epoch suppression
fingerprint; matching that independently reconstructed pattern to listener
absolute epochs selects 1,631,475 with 8,290 overlaps, versus 6,965 for the
nearest wrong candidate 1,631,477.

| BSF | F3 relative integer | F4 relative integer | equal | F3 remainder us | nominal margin us | unique Fusion records backed by listener |
|---|---:|---:|---|---:|---:|---:|
| BSF3C79 | 0 | 0 | PASS | 0 | 55000 | 16363 |
| BSFEC35 | 0 | 0 | PASS | 3000 | 52000 | 83 |
| BSF44AD | 0 | 0 | PASS | 0 | 55000 | 674 |
| BSF6C53 | −1 | −1 | PASS | 8000 | 47000 | 16339 |
| BSF8BC4 | −1 | −1 | PASS | 3000 | 52000 | 16363 |
| BSF1120 | −7 | −7 | PASS | 13000 | 42000 | 15775 |
| BSF31CC | −1 | −1 | PASS | 0 | 55000 | 1626 |
| BSFAA61 | −1 | −1 | PASS | 3000 | 52000 | 1363 |
| BSFB165 | −1 | −1 | PASS | 0 | 55000 | 4160 |
| **BSFC2CC** | **−1** | **−2** | **FAIL** | 5000 | 50000 | 8290 |

This is a first-class mismatch, as preregistered; it is not reconciled by
changing either integer after seeing the result. Exact absolute offsets,
overlap alternatives, match lag distributions, and Poll-sequence offsets are
in [`alignment_v2_results.json`](alignment_v2_results.json); the compact audit
is [`f3_f4_integer_audit.csv`](f3_f4_integer_audit.csv).

## Listener-backed cross-board constants

For each clean paired record, the independently measured listener Poll phase
was combined with the B306 frame-versus-strobe delta scaled by the fitted local
clock, then the configured slot was removed. Multiple observers of one Poll
were reduced to one median so listener coverage did not reweight records.

| BSF | slot | on-air source | C median us | σ us | p95 abs us | clean listener-backed records | Poll phase median us |
|---|---:|---|---:|---:|---:|---:|---:|
| BSF3C79 | 1 | 0xB101 | 18430.460 | 94.758 | 243.834 | 14727 | 13900.996 |
| BSFEC35 | 2 | 0xB10A | 18545.331 | 85.660 | 193.850 | 76 | 23963.628 |
| BSF44AD | 3 | 0xB103 | 18391.251 | 103.986 | 257.825 | 609 | 33898.161 |
| BSF6C53 | 4 | 0xB104 | 18453.105 | 98.340 | 256.712 | 14704 | 43906.257 |
| BSF8BC4 | 5 | 0xB105 | 18575.379 | 84.835 | 215.662 | 14727 | 53975.578 |
| BSF1120 | 6 | 0xB106 | 18436.822 | 79.597 | 208.815 | 14193 | 63907.845 |
| BSF31CC | 7 | 0xB107 | 18511.965 | 136.998 | 318.523 | 1465 | 73963.165 |
| BSFAA61 | 8 | 0xB108 | 18426.152 | 120.892 | 324.109 | 1237 | 83883.311 |
| BSFB165 | 9 | 0xB109 | 18442.814 | 104.967 | 267.343 | 3731 | 93920.252 |
| BSFC2CC | 10 | 0xB102 | 18377.624 | 174.530 | 319.601 | 7071 | 103874.009 |

The minimum is BSFC2CC at 18,377.624 us; the maximum is BSF8BC4 at
18,575.379 us; spread = **197.755 us**. This closes v1's 73.035 ms
identifiability problem when listener evidence is available. Raw table:
[`table2_listener_constants.csv`](table2_listener_constants.csv).

## F5 — IMU remap, global figure, and module tests

| BSF | samples | mean interval us | interval σ us | p99 abs from 5 ms us | max abs from 5 ms us | intervals >7.5 ms |
|---|---:|---:|---:|---:|---:|---:|
| BSF3C79 | 359991 | 5000.107 | 79.100 | 0.079 | 33745.387 | 2 |
| BSFEC35 | 359967 | 5000.345 | 116.402 | 0.043 | 36056.645 | 4 |
| BSF44AD | 359995 | 5000.023 | 57.935 | 0.073 | 34760.418 | 1 |
| BSF6C53 | 359969 | 5000.481 | 138.365 | 0.083 | 37005.301 | 6 |
| BSF8BC4 | 359962 | 5000.492 | 139.413 | 0.077 | 36075.366 | 6 |
| BSF1120 | 360185 | 5000.124 | 84.892 | 0.076 | 36442.368 | 2 |
| BSF31CC | 360000 | 5000.021 | 60.381 | 0.080 | 36228.342 | 1 |
| BSFAA61 | 360004 | 5000.032 | 59.927 | 0.068 | 35956.447 | 1 |
| BSFB165 | 360010 | 5000.026 | 58.688 | 0.071 | 35213.425 | 1 |
| **BSFC2CC** | **359980** | **5000.210** | **98.679** | **0.074** | **35407.399** | **3** |

BSFC2CC is healed: its mean mapped interval is 5,000.210 us rather than v1's
3,244.779 us. The large maxima and ordinary standard deviations preserve
discrete health-reset discontinuities instead of smoothing them. Machine data:
[`table3_imu_remap.csv`](table3_imu_remap.csv).

![Ten UWB and ten IMU streams on the listener-backed global axis](aligned_global_timeline.png)

The figure uses F4 absolute beacon counters and measured `C_i`; it is not a
per-row normalization. Blue marks are UWB records and orange points are IMU
samples. The configured slot structure is visibly interleaved on one common
three-second axis.

The reusable implementation is [`time_aligner_v2.py`](time_aligner_v2.py).
Its output explicitly carries both the provisional F3 integer and the F4 audit
result; a mismatch remains machine-readable rather than being hidden. The
three mandated tests pass:

- performed-sweep-counter replay with 110/220 ms gaps;
- two-component delivery-band classification; and
- integer disambiguation under synthetic BLE delay.

Transcript: [`unit_tests.txt`](unit_tests.txt). Source:
[`test_time_aligner_v2.py`](test_time_aligner_v2.py).

## Acceptance matrix

| Preregistered gate | Result | Evidence |
|---|---|---|
| Every consecutive gap classifies within ±5 ms | PASS, worst 2.964 ms | Table 1 |
| Clean residual p95 <1 ms on all ten | PASS, worst 294.707 us | Table 1 |
| Band mechanism stated and per-board fractions reported | PASS; 2.083333 ms rejected 10/10; band excluded | Table 1 |
| F3 margin comfortably inside ±55 ms | Nominal PASS, minimum 42 ms; **invalidated for one node by gold audit** | F3 section |
| F3 integer equals F4 integer on every board | **FAIL, 9/10; BSFC2CC differs by one 110 ms epoch** | F3/F4 audit |
| Cross-board `C_i` spread quantified with listener backing | PASS, 197.755 us | Table 2 |
| BSFC2CC IMU remap healed | PASS, mean 5,000.210 us | Table 3 |
| Three required module tests | PASS, 3/3 | `unit_tests.txt` |

The preregistered PASS sentence is therefore **not earned**. The correct
closeout is: **listener instrumentation proves that a fusion time axis existed
in R4, but this v2 host-only production method cannot recover it reliably from
the production stream.**

## UNKNOWN / open notes

1. What displaced BSFC2CC's session-median DK arrival statistic by one complete
   epoch while leaving a small modulo remainder. The dataset proves the error;
   it does not isolate its queueing or service-layer cause.
2. What mechanism produces the delayed delivery component. All ten confidence
   intervals reject exactly one 96-byte UART serialization; no alternate cause
   is inferred here.
3. Why BSFC2CC alone has a 14.869% delayed-band fraction and roughly twice the
   clean-band σ of most peers.
4. BSFEC35 and BSF44AD have fewer listener-backed clean pairs (76 and 609), but
   their independent observer phases agree with the common-axis solution. The
   present dataset does not increase their RF coverage.
5. **Firmware implication note only:** an end-to-end carried epoch identifier
   would make the integer observable without the DK-arrival heuristic. This is
   not a relay9 proposal and no firmware work is authorized by this report.

## Evidence integrity

[`SHA256SUMS`](SHA256SUMS) covers every generated v2 artifact, including this
report. [`SOURCE_INPUT_SHA256SUMS`](SOURCE_INPUT_SHA256SUMS) covers the
immutable Fusion input, all seven listener streams, the listener indexes, R4
state/analysis, and the v1 baseline artifacts. No hardware-derived input was
created during this run.
