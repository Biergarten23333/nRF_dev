# R4 time-alignment dry run

Host-only alignment from the mandated kind-1/kind-3 fields does **not** pass on this real-data set: per-board IMU↔UWB scaling works for nine boards, but the common cross-board beacon epoch is not identifiable and BSFC2CC's sweep cadence is not 110 ms. The nine nominal boards measure UART-arrival residual standard deviation 0.692–0.708 ms, p95 2.277–2.331 ms, and maximum 2.702–2.767 ms, with an exact approximately 10% multi-ms tail. The slot-subtracted circular constants span 73.035 ms, not sub-ms, and because kind-1 has no superframe index that spread contains arbitrary TIMER2/sweep origins rather than a usable physical calibration.

**Verdict: FAIL — do not release the hardware phase on the strength of this dry run.** This is an offline measurement verdict only; no hardware, serial port, J-Link probe, radio, firmware, or pre-existing evidence file was touched.

## Inputs and method

The immutable 291,707,476-byte R4 CDC log has SHA-256
`5db1033de15509717ce3ebe0377e8b8abbc9d0c478cad253a258f017480c9c35`.
The formal interval was taken from R4's own analysis boundary:
`171440.580907324` through `173240.581020483` monotonic seconds (1800.000113 s).
The slot map was read from the matching run state. See
[`input_manifest.json`](input_manifest.json), [`qa.md`](qa.md), and the exact
saved [`PROMPT.md`](PROMPT.md).

For each board the module unwraps the uint32 sweep counter, preserves forward
integer gaps, and performs a Huber IRLS fit of 64-bit `frame_rx_ts_us` against
the unwrapped sweep. Residual statistics include every record; multi-ms points
are not removed. Drift is relative to the commanded 110,000 us grid pitch.
`master_arrival_ms` is never used by the fit.

The formal-window extraction reconciles exactly with all 20 R4 node-table
counts. Nine boards contribute 16,363–16,370 UWB records each; BSFC2CC
contributes its recorded 10,579. Each board contributes 359,962–360,185 IMU
samples. Full reconciliation is in
[`extraction_counts.csv`](extraction_counts.csv).

## Table 1 — per-board TIMER2 versus sweep fit

| BSF | slot | slope us/sweep | drift ppm | residual σ us | residual p95 abs us | residual max abs us | >2 ms | epochs | gaps | p95 <1 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BSF3C79 | 1 | 110001.740739 | 15.825 | 695.678 | 2287.991 | 2745.105 | 9.998% | 16363 | 0 | FAIL |
| BSFEC35 | 2 | 110000.951362 | 8.649 | 708.218 | 2327.444 | 2749.645 | 9.998% | 16363 | 0 | FAIL |
| BSF44AD | 3 | 110001.611293 | 14.648 | 694.676 | 2280.400 | 2708.709 | 9.986% | 16363 | 0 | FAIL |
| BSF6C53 | 4 | 110001.831414 | 16.649 | 701.719 | 2298.751 | 2766.653 | 9.991% | 16364 | 0 | FAIL |
| BSF8BC4 | 5 | 110001.698783 | 15.443 | 707.313 | 2330.594 | 2736.060 | 9.991% | 16364 | 0 | FAIL |
| BSF1120 | 6 | 110001.676656 | 15.242 | 692.911 | 2276.435 | 2744.612 | 10.000% | 16370 | 0 | FAIL |
| BSF31CC | 7 | 110001.754851 | 15.953 | 702.156 | 2312.086 | 2702.924 | 9.991% | 16364 | 0 | FAIL |
| BSFAA61 | 8 | 110001.485978 | 13.509 | 694.376 | 2278.646 | 2702.112 | 9.998% | 16364 | 0 | FAIL |
| BSFB165 | 9 | 110001.572200 | 14.293 | 700.543 | 2304.712 | 2727.233 | 10.004% | 16364 | 0 | FAIL |
| BSFC2CC | 10 | 169512.722122 | 541024.747 | 1857607.000 | 3701744.454 | 6068595.706 | 99.905% | 10579 | 0 | FAIL |

The nine nominal slopes are crystal-plausible (+8.649 to +16.649 ppm), but the
residual distribution is structured rather than Gaussian: approximately 90%
of records form a narrow band around the fit while approximately 10% occupy a
+2.0 to +2.5 ms delayed band. That is a measured UART RX_RDY/delivery-path
effect and fails the preregistered p95 gate. BSFC2CC is a separate failure:
its counter remains consecutive, but consecutive records advance TIMER2 by an
average 169.513 ms, so sweep cannot represent the 110 ms beacon grid for that
board in this run. Machine-readable values, including fit reference points and
both >1 ms and >2 ms fractions, are in
[`table1_clock_fits.csv`](table1_clock_fits.csv).

## Table 2 — slot-subtracted constants modulo 110 ms

| BSF | slot | slot offset us | C modulo 110 ms, us | within-board phase σ us |
|---|---:|---:|---:|---:|
| BSF3C79 | 1 | 10000 | 84044.466 | 695.678 |
| BSFEC35 | 2 | 20000 | 34907.529 | 708.218 |
| BSF44AD | 3 | 30000 | 103326.679 | 694.676 |
| BSF6C53 | 4 | 40000 | 41278.376 | 701.719 |
| BSF8BC4 | 5 | 50000 | 89549.008 | 707.313 |
| BSF1120 | 6 | 60000 | 81302.432 | 692.911 |
| BSF31CC | 7 | 70000 | 44337.022 | 702.156 |
| BSFAA61 | 8 | 80000 | 101362.992 | 694.376 |
| BSFB165 | 9 | 90000 | 19335.407 | 700.543 |
| BSFC2CC | 10 | 100000 | 37493.877 | 31748.465 |

The minimum circular arc containing these constants is **73,034.590 us**.
This is not evidence for 73 ms of physical tag/UART skew. It is the expected
identifiability failure when ten independent TIMER2 origins and ten tag-owned
sweep origins are compared without a carried beacon/superframe index. Adding
or subtracting any integer number of 110 ms cycles from one board is invisible
to its fit, and the excluded DK arrival clock was the only recorded cross-board
observation. Consequently A3 cannot turn these values into per-board physical
calibration constants. Raw table: [`table2_cross_board_constants.csv`](table2_cross_board_constants.csv).

## Table 3 — IMU timestamps mapped through each board's fit

| BSF | samples | mean Δt us | Δt σ us | p99 \|Δt−5 ms\| us | max \|Δt−5 ms\| us | Δt >7.5 ms | span/UWB span | hreset |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BSF3C79 | 359991 | 5000.107 | 79.100 | 0.079 | 33745.387 | 2 | 1.000094 | 2 |
| BSFEC35 | 359967 | 5000.345 | 116.402 | 0.043 | 36056.645 | 4 | 1.000074 | 4 |
| BSF44AD | 359995 | 5000.023 | 57.935 | 0.073 | 34760.418 | 1 | 1.000088 | 1 |
| BSF6C53 | 359969 | 5000.481 | 138.365 | 0.083 | 37005.301 | 6 | 1.000046 | 6 |
| BSF8BC4 | 359962 | 5000.492 | 139.413 | 0.077 | 36075.366 | 6 | 1.000029 | 6 |
| BSF1120 | 360185 | 5000.124 | 84.892 | 0.076 | 36442.368 | 2 | 1.000209 | 2 |
| BSF31CC | 360000 | 5000.021 | 60.381 | 0.080 | 36228.342 | 1 | 1.000040 | 1 |
| BSFAA61 | 360004 | 5000.032 | 59.927 | 0.068 | 35956.447 | 1 | 1.000054 | 1 |
| BSFB165 | 360010 | 5000.026 | 58.688 | 0.071 | 35213.425 | 1 | 1.000069 | 1 |
| BSFC2CC | 359980 | 3244.779 | 64.036 | 1755.406 | 21221.513 | 3 | 1.000084 | 3 |

For the nine valid-slope boards, 99% of intervals sit within 0.083 us of the
nominal 5 ms after scale correction. Their ordinary standard deviations are
inflated by one to six discrete recovery discontinuities per board; the large
maxima are preserved rather than smoothed. The event counts match R4's hreset
deltas. BSFC2CC maps 5 ms local IMU cadence to 3.245 ms because its UWB sweep
fit is invalid; this is a model failure, not a claim that its IMU ran at 308 Hz.
Machine-readable table: [`table3_imu_remap.csv`](table3_imu_remap.csv).

## Figure

![Diagnostic aligned streams](aligned_streams_diagnostic.png)

[`aligned_streams_diagnostic.png`](aligned_streams_diagnostic.png) shows three
seconds of UWB ticks and IMU samples. Because the required common integer
beacon epoch is absent, each row is explicitly normalized to that board's
first formal UWB record and offset by its configured slot for visualization.
It demonstrates the per-board remap and makes BSFC2CC's irregular UWB cadence
visible, but it is **not** the requested proof of one identified global axis.

## Module and tests

Callable API:

```python
result = align_log(log_path, slots, formal_start_s, formal_end_s)
# result.models[name]             robust per-board ClockModel
# result.aligned_uwb_us[name]     board-sweep-coordinate UWB timestamps
# result.aligned_imu_us[name]     IMU timestamps in the same board coordinate
```

The implementation is [`time_aligner.py`](time_aligner.py). Its API states the
remaining integer-epoch ambiguity instead of silently calling the board-local
coordinate global. Tests in [`test_time_aligner.py`](test_time_aligner.py)
cover uint32 natural wrap, forward gaps, and recovery of a known +23.5 ppm
slope in the presence of large outliers. All three pass; transcript:
[`unit_tests.txt`](unit_tests.txt). Fitted models and the machine-readable
identifiability flag are in [`models.json`](models.json).

## Acceptance matrix

| Gate | Result | Evidence |
|---|---|---|
| Extraction reconciles with R4 | PASS, 20/20 counts exact | `extraction_counts.csv` |
| Per-board residual p95 <1 ms | **FAIL, 10/10** | `table1_clock_fits.csv` |
| Cross-board C spread quantified | PASS as measurement: 73.035 ms; **not identifiable as calibration** | `table2_cross_board_constants.csv`, `qa.md` |
| IMU interval consistent with 200 Hz | PASS for nine valid fits; **FAIL for BSFC2CC model** | `table3_imu_remap.csv` |
| Unit tests | PASS, 3/3 | `unit_tests.txt` |
| Host-only pipeline validated unchanged | **FAIL** | all above |

## UNKNOWN / deliberately unresolved

1. Which scheduling/DMA/host-service mechanism produces the exact 10% delayed
   UART-arrival band. The dry run measures it but does not instrument its cause.
2. The physical cross-board constants `C_i`. They cannot be separated from
   independent TIMER2 and tag-owned sweep origins in the allowed fields.
3. The beacon epoch associated with any individual kind-1 record. It is not on
   the wire; no value was inferred from DK arrival time.
4. Why BSFC2CC emitted consecutive public sweeps at an average 169.5 ms in this
   R4 window. Existing data establish the behavior, not its cause.
5. Exact hreset transition time within a one-second telemetry-report interval.
   Discontinuities are counted and preserved, but telemetry only brackets them.

No firmware or hardware proposal is made here, as required by scope.
