# BioSpur UWB / CIR System Status Check

Generated: 2026-06-15 15:54 CEST  
Workspace: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start`

## 1. Current Device / Port Status

### Anchor Master

Anchor Master application CDC is online.

Observed port:

```text
/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00 -> /dev/ttyACM11
```

Master_Anchor J-Link is also visible:

```text
SNR 960148546 -> /dev/ttyACM12, /dev/ttyACM13, /dev/ttyACM14
```

Direct command smoke result:

```text
autopos status
AUTOPOS: mode=RECV state=idle staged=- last_success=- sets=0 cir=0 error=-

autopos cir 0
AUTOPOS cir mode set: 0

autopos status
AUTOPOS: mode=RECV state=idle staged=- last_success=- sets=0 cir=0 error=-
```

Interpretation: Anchor Master is healthy enough for command/control. AutoPos is idle. Runtime CIR mode is currently off.

### Tag Master

Tag Master application CDC is not online.

Master_Tag J-Link is visible:

```text
SNR 1050070698 -> /dev/ttyACM1, /dev/ttyACM2
```

But no `Master_Tag_BioSpur_BLE_Control` CDC device is currently enumerated. This means the physical board/J-Link is present, but the B120 Tag Master application USB CDC is not running or not enumerating.

### Other visible devices

One Gesture Recognition B120 CDC is present and must not be confused with UWB control:

```text
/dev/serial/by-id/usb-BioSpur-GR_BioSpur-GR_51D4A5716A4C5551-if00 -> /dev/ttyACM0
```

Several deployed anchor J-Link CDC ports are also visible:

```text
000760184500, 000760184781, 000760184974, 000760185876,
000760185878, 000760185889, 000760185904, 000760186124
```

## 2. Runtime CIR Mode Design

The Anchor firmware supports three runtime modes:

```text
CIR=0
CIR=COMPACT
CIR=FULL
```

The command interface exposes this through:

```text
anchor role <A..H|UUID32|all> <master|matrix|responder> [cir <0|compact|full>]
```

Source confirms parsing accepts:

```text
0 / OFF / NONE
COMPACT / FEATURE / 1
FULL / RAW / 2
```

### CIR=0

No CIR output. This should be the default for normal AutoPos sweep, because it avoids CIR bandwidth and timing overhead.

### CIR=COMPACT

Outputs one compact diagnostic line per received UWB packet:

```text
ACRX;...
```

Fields include receiver anchor, source anchor/tag, raw distance, RX timestamp, carrier integrator, first-path index, first-path amplitudes, maxGrowthCIR, preamble count, stdNoise, maxNoise.

Current intended use:

- Fast CIR risk scoring during AutoPos sweep.
- Generate heatmaps: FP amplitude, SNR proxy, peak/FP, raw-distance stability, suspicion score.
- Build pair weights for solver experiments.

Important limitation:

- COMPACT does not contain raw accumulator samples.
- It cannot reconstruct the full CIR waveform/envelope plot.

Channel:

- The anchor CIR build supports compact output to BLE and/or CDC depending on build.
- The runtime-modes build used for OTA had compact BLE enabled. Some local builds also enabled compact CDC.

### CIR=FULL

Outputs full DW1000 accumulator frames:

```text
ACIRM;...     # metadata / frame header
ACIRD;...     # accumulator chunks
ACIRE;...     # frame end
```

Accumulator length observed in smoke test:

```text
4064 bytes = 1016 complex CIR samples
```

Current intended use:

- Offline waveform inspection.
- Multipath tail/secondary-peak analysis.
- Ground-truthing which COMPACT metrics are actually useful.

Channel:

- FULL is designed to go through USB/CDC/J-Link logging, not BLE.
- FULL over BLE is not practical.

Cost:

- Very high bandwidth. Sweep10 FULL CIR smoke took 531.4 s / 8.9 min.
- Therefore FULL should not be used for normal Sweep1000 unless scoped to selected links or a separate targeted window.

## 3. Relevant Firmware Images / Builds

### Anchor body OTA CIR images

Relevant builds:

```text
SS-TWR/alt-SS-TWR/broadcast/build-anchor-unified-ota-cir
SS-TWR/alt-SS-TWR/broadcast/build-anchor-unified-ota-cir-20260602
SS-TWR/alt-SS-TWR/broadcast/build-anchor-unified-ota-cir-usbdata
```

Important CMake settings from available build caches:

`build-anchor-unified-ota-cir`:

```text
APP_ANCHOR_CIR_FEATURE_OUTPUT_ENABLE=1
APP_ANCHOR_CIR_FEATURE_OUTPUT_BLE_ENABLE=1
APP_ANCHOR_CIR_FEATURE_OUTPUT_CDC_ENABLE=1
APP_ANCHOR_CIR_FULL_OUTPUT_ENABLE=1
APP_ANCHOR_CIR_FULL_OUTPUT_CDC_ENABLE=1
APP_ANCHOR_CIR_FULL_CHUNK_BYTES=48
APP_ANCHOR_FW_MARKER=anchor-cir-runtime-modes-20260602-215211
BOARD=decawave_dwm1001_dev/nrf52832
CONF_FILE=prj.conf;prj_ota.conf
CONFIG_BOOTLOADER_MCUBOOT=y
CONFIG_BT=y
CONFIG_MCUMGR_TRANSPORT_BT=y
```

`build-anchor-unified-ota-cir-20260602`:

```text
APP_ANCHOR_CIR_FEATURE_OUTPUT_ENABLE=1
APP_ANCHOR_CIR_FEATURE_OUTPUT_BLE_ENABLE=1
APP_ANCHOR_CIR_FEATURE_OUTPUT_CDC_ENABLE=0
APP_ANCHOR_CIR_FULL_OUTPUT_ENABLE=1
APP_ANCHOR_CIR_FULL_OUTPUT_CDC_ENABLE=1
APP_ANCHOR_FW_MARKER=anchor-cir-full-ble-20260602
```

`build-anchor-unified-ota-cir-usbdata`:

```text
APP_ANCHOR_CIR_FEATURE_OUTPUT_ENABLE=1
APP_ANCHOR_CIR_FEATURE_OUTPUT_BLE_ENABLE=0
APP_ANCHOR_CIR_FEATURE_OUTPUT_CDC_ENABLE=1
APP_ANCHOR_CIR_FULL_OUTPUT_ENABLE=1
APP_ANCHOR_CIR_FULL_OUTPUT_CDC_ENABLE=1
APP_ANCHOR_FW_MARKER=anchor-cir-usbdata-20260602-1725
```

Interpretation:

- Anchor body OTA-capable builds exist.
- Runtime CIR modes exist.
- Different builds route COMPACT differently:
  - BLE compact build: good for normal master-collected AutoPos sweep features.
  - CDC compact build: good if every anchor is individually connected and logged over USB/J-Link.
- FULL is CDC/USB oriented.

### Anchor Master B120 images

Relevant builds:

```text
SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-anchor-autopos-cir-full-apply-20260602-224830
SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-anchor-lfrc-b120-master-anchor-anchor-cir-full-ble-20260602-gatefix
```

Observed B120 clock configuration is correct for the workstation hardware rule:

```text
CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y
CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC_CALIBRATION=y
CONFIG_CLOCK_CONTROL_NRF_K32SRC_XTAL is not set
CONFIG_CLOCK_CONTROL_NRF_K32SRC_SYNTH is not set
```

USB CDC config for the gatefix build:

```text
CONFIG_USB_DEVICE_MANUFACTURER="Master_Anchor"
CONFIG_USB_DEVICE_PRODUCT="BioSpur_BLE_Control"
CONFIG_USB_DEVICE_VID=0x2FE3
CONFIG_USB_DEVICE_PID=0x1002
CONFIG_USB_CDC_ACM=y
```

Current online Anchor Master enumerates as:

```text
BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02
```

So the online build may not be the latest `Master_Anchor_...` naming build, but functionally it responds to the current AutoPos command set.

### Tag images

Relevant full-CIR USB build:

```text
SS-TWR/alt-SS-TWR/broadcast/build-tag-b70-full-cir-usb-all8-static-20260607
```

Important settings:

```text
APP_TAG_FW_MARKER=b70-full-cir-usb-all8-static-20260607
APP_TAG_BLE_ENABLE=0
APP_TAG_MCUBOOT_ENABLE=0
APP_TAG_CIR_FEATURE_OUTPUT_ENABLE=1
APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE=0
APP_TAG_CIR_FULL_OUTPUT_ENABLE=1
APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE=1
APP_TAG_CIR_FULL_PRIORITY_MASK=0xFF
APP_TAG_CIR_FULL_PRIORITY_ONLY_SWEEP=1
```

Interpretation:

- This is a direct/full USB CIR image.
- It is not OTA-capable because BLE and MCUBOOT are disabled.
- It should not be deployed as normal Tag firmware unless explicitly doing direct recovery / full-CIR USB experiment.

Relevant OTA-capable compact-CIR tag image:

```text
SS-TWR/alt-SS-TWR/broadcast/build-tag-ota-b70-cir-feature-a8-static-20260607
```

Important settings:

```text
APP_TAG_FW_MARKER=b70-ota-cir-feature-a8-static-20260607
APP_TAG_BLE_ENABLE=1
APP_TAG_BLE_OTA_ENABLE=1
APP_TAG_MCUBOOT_ENABLE=1
APP_TAG_CIR_FEATURE_OUTPUT_ENABLE=1
APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE=1
APP_TAG_CIR_FULL_OUTPUT_ENABLE=0
```

Interpretation:

- This can output compact CIR features over BLE.
- It cannot output full raw CIR.
- It remains OTA-capable.

Likely latest deployed BSF66F static tag image:

```text
SS-TWR/alt-SS-TWR/broadcast/build-tag-codex-tr2-static-calpmode-a8-20260608-fix2
```

Important settings:

```text
APP_TAG_FW_MARKER=codex-tr2-static-calpmode-a8-20260608-fix2
APP_TAG_BLE_ENABLE=1
APP_TAG_BLE_OTA_ENABLE=1
APP_TAG_MCUBOOT_ENABLE=1
APP_TAG_CIR_FEATURE_OUTPUT_ENABLE=0
APP_TAG_CIR_FULL_OUTPUT_ENABLE=0
```

Interpretation:

- This is the current static/calmode style tracking image.
- It is OTA-capable.
- It does not output compact or full CIR.

## 4. Existing CIR Captures / Analysis Results

### FULL CIR AutoPos Sweep10 smoke

Summary file:

```text
autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118/analysis_summary.md
```

Key results:

```text
Mode: full
Sweep result: 8/8 masters successful, each 10/10 sweep lines
Total elapsed: 531.4 s = 8.9 min
Full CIR frames reconstructed: 958 / expected 1120 = 85.5%
Accumulator length: 4064 bytes
Median sample count: 1016
```

Most suspicious full-CIR multipath-tail links:

```text
C<-G
G<-C
F<-G
E<-H
G<-F
H<-E
F<-A
A<-F
E<-F
F<-E
```

Conclusion:

- FULL USB path works.
- It is too slow for normal large Sweep1000.
- Use it for targeted waveform inspection or short calibration windows.

### COMPACT Sweep10 smoke

Summary file:

```text
autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/sweep_SW01_10_prewarm0_COMPACT_SMOKE_20260603_001605/compact_cir_analysis/analysis_summary.md
```

Key results:

```text
ACRX compact samples: 952
Directed links observed: 56
```

Top compact-suspicious directed links:

```text
A<-C
C<-A
C<-G
G<-C
A<-B
E<-H
H<-E
B<-C
C<-H
E<-F
F<-E
G<-F
```

Conclusion:

- COMPACT works as a fast risk/scoring signal.
- It cannot generate full CIR waveform plots.
- It overlaps partially with FULL suspicious links, especially C/G and E/H.

## 5. Solver Injection Status

Existing solver/CIR files found in the repo:

```text
SS-TWR/alt-SS-TWR/broadcast/scripts/prepare_autopos_v3_box.py
SS-TWR/alt-SS-TWR/broadcast/scripts/run_cir_weighted_layout_compare.py
```

Design intent already present:

- CIR analysis produces `cir_pair_weights.csv` and `cir_pair_weights.json`.
- Layout solver can consume optional CIR-derived pair weights.
- FULL CIR is for waveform inspection and calibration.
- COMPACT/FULL-derived pair weights are what the solver consumes.

Existing mainline CIR-weighted result from previous work:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/mainline_autopos_with_cir_compact_1000_20260608_overnight/layout_compare/cir_weighted_layout_comparison.md
```

Observed outcome from prior status:

```text
baseline same-sweep RMS: 112.994 mm
CIR-weighted RMS:        114.611 mm
```

Static BSF66F comparison from prior status:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/static_bsf66f_cal_static_no_cir_vs_cir_20260608_fix2/NO_CIR_VS_CIR_RESULT.md

no-CIR layout static RMS:       66.42 mm
CIR-weighted layout static RMS: 70.51 mm
```

Interpretation:

- The software injection path exists.
- The first weighting policy did not improve layout/static repeatability.
- The next research question is not "can CIR be injected?" but "which CIR-derived metric predicts harmful ranging bias well enough to improve the solver?"

## 6. Current Practical Capability Matrix

| Device side | Mode | Output | Channel | Practical use |
|---|---|---|---|---|
| Anchor | CIR=0 | none | none | normal AutoPos sweep |
| Anchor | CIR=COMPACT | `ACRX` features | BLE or CDC depending on image | fast link scoring / pair weights |
| Anchor | CIR=FULL | `ACIRM/ACIRD/ACIRE` full accumulator | USB/CDC/J-Link logging | waveform study, multipath inspection |
| Tag current static/calmode | no CIR | normal tag ranging/status | BLE | BSF66F static repeatability |
| Tag OTA compact-CIR image | compact features | BLE | BLE | tag-side CIR feature experiments |
| Tag full-CIR USB image | full accumulator | USB/J-Link only | USB/CDC | tag-side waveform study; not OTA-capable |

## 7. Experimental Recommendation

For Claude / analysis planning, the defensible workflow is:

1. Run normal AutoPos sweep with `CIR=0` to get baseline range matrix and layout.
2. Run short targeted FULL CIR windows, not full Sweep1000 FULL CIR.
3. Run COMPACT CIR during AutoPos sweep only if the extra time is acceptable; COMPACT is the production-style feature channel.
4. Use FULL CIR to validate which COMPACT metrics correlate with actual multipath:
   - low FP amplitude,
   - high peak/FP,
   - high noise,
   - high raw-distance variance,
   - directed-link asymmetry,
   - FULL tail/main ratio where available.
5. Convert validated metrics into pair weights or robust loss scales.
6. Compare layout and static BSF66F repeatability with and without CIR weights.

Current caveat:

- The first CIR-weighted solver run worsened RMS slightly. CIR is not automatically helpful. It needs a better weight policy and probably directed-link handling instead of simple pair-level downweighting.

## 8. Immediate Open Problems

1. Tag Master application CDC is offline. If Tag OTA is needed, recover/check Tag Master before proceeding.
2. Current online Anchor Master responds, but its USB name is old generic `BioSpur_BLE_Control`, not the newer `Master_Anchor_BioSpur_BLE_Control`.
3. FULL CIR is functional but too slow for large sweeps.
4. COMPACT CIR is fast enough for broad experiments but cannot produce waveform plots.
5. Existing CIR weighting did not improve solver metrics; the analysis needs to focus on metric validity and weighting strategy.

