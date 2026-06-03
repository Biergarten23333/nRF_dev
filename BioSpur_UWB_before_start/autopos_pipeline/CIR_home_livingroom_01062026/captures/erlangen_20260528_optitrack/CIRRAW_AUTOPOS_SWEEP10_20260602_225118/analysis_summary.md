# Sweep10 FULL CIR Smoke Analysis

- Sweep dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/sweep_SW01_10_prewarm0_FULLCIR_SMOKE_20260602_225118`
- CIR dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118`
- Mode: `full`
- Sweep result: 8/8 masters successful, each `10/10` sweep lines
- Total elapsed: 531.4 s (8.9 min)
- Full CIR frames reconstructed: 958 / expected 1120 = 85.5%
- Accumulator length: median 4064 bytes, sample count median 1016

## Main Findings

1. FULL CIR output path works: `ACIRM/ACIRD/ACIRE` were present on USB and 958 complete accumulator frames were rebuilt into `.bin` files.
2. Ordinary sweep ranging also worked: all A-H rounds completed with `sw_count=10` and no reconnect retry.
3. The FULL-CIR capture is bandwidth-heavy: Sweep10 took about 8.9 minutes. This is expected because every full accumulator is 4064 bytes and many directed links are emitted over eight USB CDC ports.
4. Coverage is not perfect: expected directed-link full-CIR count is about 1120 frames; recovered complete frames are 958. Missing frames are likely from USB/CDC logging pressure or interleaved serial loss, not from AutoPos sweep failure.
5. Several first sweep lines have zero quality for individual peers, especially D in early G-round and C/G in first H-round. Because later lines recover, this looks like startup/role-switch settling rather than a permanently dead anchor.

## Frame Counts

By receiver USB/anchor:

|Anchor|Frames|
|---|---:|
|A|123|
|B|122|
|C|126|
|D|111|
|E|123|
|F|114|
|G|120|
|H|119|

By source anchor:

|Anchor|Frames|
|---|---:|
|A|120|
|B|123|
|C|123|
|D|113|
|E|119|
|F|125|
|G|116|
|H|119|

Lowest directed-link counts, expected about 20 per directed pair:

|rx|src|n|snr_med|fp_amp_med|tail_med|
|---|---|---|---|---|---|
|D|G|12|39.4|15662.5|0.0|
|G|D|13|39.9|14487.0|0.0|
|F|H|14|37.8|13779.5|0.1|
|A|E|14|40.0|21516.0|0.0|
|E|A|14|40.3|21433.5|0.0|
|H|G|15|38.7|20699.0|0.1|
|A|D|15|39.0|19672.0|0.0|
|G|B|15|39.9|18879.0|0.0|
|H|C|15|39.9|18978.0|0.0|
|D|E|15|41.3|20687.0|0.0|
|D|B|15|41.7|18773.0|0.0|
|F|G|16|35.8|14799.0|0.2|

Weakest SNR-proxy directed links:

|rx|src|n|snr_med|fp_amp_med|noise_med|tail_med|
|---|---|---|---|---|---|---|
|F|G|16|35.8|14799.0|48.0|0.2|
|H|E|17|36.1|15318.0|60.0|0.2|
|E|H|17|36.2|15234.0|64.0|0.2|
|E|F|17|36.3|14439.0|52.0|0.1|
|G|F|18|36.7|15487.0|52.0|0.2|
|G|C|18|36.9|13910.5|52.0|0.2|
|F|E|16|36.9|14009.5|50.0|0.1|
|C|G|17|37.0|11173.0|56.0|0.2|
|C|B|19|37.5|17050.0|52.0|0.0|
|F|H|14|37.8|13779.5|44.0|0.1|
|D|F|19|38.2|16717.0|48.0|0.0|
|H|F|17|38.3|15476.0|48.0|0.1|

Highest multipath-tail proxy links:

|rx|src|n|snr_med|fp_amp_med|tail_med|raw_med|
|---|---|---|---|---|---|---|
|C|G|17|37.0|11173.0|0.2|1568.0|
|G|C|18|36.9|13910.5|0.2|1588.0|
|F|G|16|35.8|14799.0|0.2|3408.0|
|E|H|17|36.2|15234.0|0.2|2773.0|
|G|F|18|36.7|15487.0|0.2|3370.5|
|H|E|17|36.1|15318.0|0.2|2751.0|
|F|A|18|39.7|17593.0|0.1|4577.0|
|A|F|19|39.6|17371.0|0.1|4608.0|
|E|F|17|36.3|14439.0|0.1|4162.0|
|F|E|16|36.9|14009.5|0.1|4240.0|
|B|F|17|39.4|20029.0|0.1|1512.0|
|F|B|17|39.0|20238.0|0.1|1564.0|

## Figures

- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118/analysis/frame_count_heatmap.png`
- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118/analysis/sweep_quality_heatmap.png`
- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118/analysis/snr_proxy_heatmap.png`
- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118/analysis/fp_amp_heatmap.png`
- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118/analysis/tail_ratio_heatmap.png`
- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_AUTOPOS_SWEEP10_20260602_225118/analysis/receiver_envelope_overview.png`

## Interpretation

For this smoke test, the strongest conclusion is system-level: FULL CIR over USB works and can be synchronized with AutoPos Sweep10. The data is good enough for link-level inspection, but not yet ideal for a production Sweep1000 run because FULL CIR makes the sweep very slow and still drops some complete frames under logging pressure.

The most useful next engineering step is to reduce FULL capture scope: either capture FULL only for selected links/anchors, or keep AutoPos sweep at `CIR=COMPACT` and run separate targeted FULL windows. For normal AutoPos layout, leave `CIR=0`; for feature learning, `COMPACT`; for waveform studies, `FULL` over USB.
