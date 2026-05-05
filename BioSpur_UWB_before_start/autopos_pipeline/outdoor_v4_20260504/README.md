# Outdoor V4 Test - 2026-05-04

Purpose: outdoor AutoPos/V4 validation using clean inter-anchor sweep plus TR-only tag captures.

Current firmware baseline:
- Anchors A-H: alt-bcast-a18-ledrole-g1200-r10
- Tags: alt-bcast-b65-tr3-ledpos-tronly-g1200-r1000
- Master_Anchor: a18-femp3 carrier, LFRC, BLE 2M
- Master_Tag: b65-femp3 carrier, LFRC, BLE 2M

Directory layout:
- sweeps/: inter-anchor sweep logs and pairs_all.csv
- tr_captures/: TR-only motion/static capture logs
- v4_data/: prepared V4 JSON inputs
- solves/: V4/inter-only solve outputs
- reports/: analysis summaries
- notes/: run notes and manual observations

Recommended sequence:
1. Run 100/500-set inter-anchor sweep while Tags/tripods stay outside the anchor volume.
2. Extract pairs_all.csv into sweeps/<run>/pairs_all.csv.
3. Place Tags in the real test volume and run TR-only capture.
4. Prepare V4 data from sweep pairs + TR capture.
5. Solve inter-only/Huber first, then V4 joint.

Completed runs:
- 2026-05-04 18:50:11: 500-set inter-anchor sweep
  - Sweep dir: `sweeps/inter_anchor_500set_20260504_185011`
  - Pairs CSV: `sweeps/inter_anchor_500set_20260504_185011/pairs_all.csv`
  - Analysis: `reports/inter_anchor_500set_20260504_185011_analysis.md`
  - Result: A-H all completed 500/500, no reconnect retry, no slow switch, final responder ready=8/8.
- 2026-05-04 19:19:30: ID01 static TR capture, center low height
  - Capture dir: `tr_captures/ID01_static_center_low_20260504_191930`
  - Session dir: `tr_captures/ID01_static_center_low_20260504_191930/recv_20260504_191931`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4364/4808 valid (90.8%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 600/601, B 601/601, C 601/601, D 598/601, E 600/601, F 601/601, G 601/601, H 162/601.
  - Disposition: valid BSF66F ID01 sample. H is weak from this placement and should be downweighted or handled by robust loss in V4.
- 2026-05-04 19:26:43: ID02 static TR capture, center mid height
  - Capture dir: `tr_captures/ID02_static_center_mid_20260504_192643`
  - Session dir: `tr_captures/ID02_static_center_mid_20260504_192643/recv_20260504_192644`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4375/4808 valid (91.0%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 599/601, B 600/601, C 601/601, D 601/601, E 599/601, F 601/601, G 601/601, H 173/601.
  - Disposition: valid BSF66F ID02 sample. H remains weak from this center placement.
- 2026-05-04 19:34:19: ID03 static TR capture, center high height
  - Capture dir: `tr_captures/ID03_static_center_high_20260504_193419`
  - Session dir: `tr_captures/ID03_static_center_high_20260504_193419/recv_20260504_193420`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4408/4808 valid (91.7%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 601/601, B 601/601, C 601/601, D 601/601, E 601/601, F 601/601, G 601/601, H 201/601.
  - Disposition: valid BSF66F ID03 sample. H remains the only weak center-placement anchor, but improves slightly at high height.
- 2026-05-04 19:39:07: ID04 static TR capture, near ABEF face low height
  - Capture dir: `tr_captures/ID04_static_abef_low_20260504_193907`
  - Session dir: `tr_captures/ID04_static_abef_low_20260504_193907/recv_20260504_193908`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4358/4800 valid (90.8%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 600/600, B 600/600, C 600/600, D 600/600, E 600/600, F 600/600, G 600/600, H 158/600.
  - Disposition: valid BSF66F ID04 sample. H remains weak near the ABEF face at low height.
- 2026-05-04 19:43:25: ID05 static TR capture, near ABEF face mid height
  - Capture dir: `tr_captures/ID05_static_abef_mid_20260504_194325`
  - Session dir: `tr_captures/ID05_static_abef_mid_20260504_194325/recv_20260504_194326`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4373/4808 valid (91.0%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 600/601, B 599/601, C 600/601, D 601/601, E 600/601, F 599/601, G 599/601, H 175/601.
  - Disposition: valid BSF66F ID05 sample. H remains weak near the ABEF face at mid height.
- 2026-05-04 19:49:15: ID06 static TR capture, near ABEF face high height
  - Capture dir: `tr_captures/ID06_static_abef_high_20260504_194915`
  - Session dir: `tr_captures/ID06_static_abef_high_20260504_194915/recv_20260504_194916`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4383/4808 valid (91.2%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 601/601, B 601/601, C 601/601, D 601/601, E 601/601, F 598/601, G 601/601, H 179/601.
  - Disposition: valid BSF66F ID06 sample. H remains weak near the ABEF face at high height.
- 2026-05-04 19:53:44: ID07 static TR capture, near BCGF face low height
  - Capture dir: `tr_captures/ID07_static_bcgf_low_20260504_195344`
  - Session dir: `tr_captures/ID07_static_bcgf_low_20260504_195344/recv_20260504_195345`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4362/4808 valid (90.7%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 601/601, B 598/601, C 600/601, D 595/601, E 601/601, F 601/601, G 600/601, H 166/601.
  - Disposition: valid BSF66F ID07 sample. H remains weak near the BCGF face at low height.
- 2026-05-04 19:59:49: ID08 static TR capture, near BCGF face mid height
  - Capture dir: `tr_captures/ID08_static_bcgf_mid_20260504_195949`
  - Session dir: `tr_captures/ID08_static_bcgf_mid_20260504_195949/recv_20260504_195950`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4338/4808 valid (90.2%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 601/601, B 601/601, C 599/601, D 601/601, E 601/601, F 599/601, G 601/601, H 135/601.
  - Disposition: valid BSF66F ID08 sample. H is weakest so far near the BCGF face at mid height.
- 2026-05-04 20:04:57: ID09 static TR capture, near BCGF face high height
  - Capture dir: `tr_captures/ID09_static_bcgf_high_20260504_200457`
  - Session dir: `tr_captures/ID09_static_bcgf_high_20260504_200457/recv_20260504_200458`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4345/4808 valid (90.4%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 601/601, B 601/601, C 601/601, D 601/601, E 601/601, F 601/601, G 601/601, H 138/601.
  - Disposition: valid BSF66F ID09 sample. H remains weak near the BCGF face at high height.
- 2026-05-04 20:09:34: ID10 static TR capture, near CDHG face low height
  - Capture dir: `tr_captures/ID10_static_cdhg_low_20260504_200934`
  - Session dir: `tr_captures/ID10_static_cdhg_low_20260504_200934/recv_20260504_200935`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4405/4808 valid (91.6%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 601/601, B 601/601, C 601/601, D 601/601, E 600/601, F 601/601, G 601/601, H 199/601.
  - Disposition: valid BSF66F ID10 sample. H improves near the CDHG face at low height but is still the weakest anchor.
- 2026-05-04 20:14:11: ID11 static TR capture, near CDHG face mid height
  - Capture dir: `tr_captures/ID11_static_cdhg_mid_20260504_201411`
  - Session dir: `tr_captures/ID11_static_cdhg_mid_20260504_201411/recv_20260504_201412`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4377/4808 valid (91.0%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 601/601, B 601/601, C 601/601, D 600/601, E 601/601, F 601/601, G 601/601, H 171/601.
  - Disposition: valid BSF66F ID11 sample. H remains the only weak anchor at this setup, useful for one-anchor-degraded offline solver robustness testing.
- 2026-05-04 20:20:45: ID12 static TR capture, near CDHG face high height
  - Capture dir: `tr_captures/ID12_static_cdhg_high_20260504_202045`
  - Session dir: `tr_captures/ID12_static_cdhg_high_20260504_202045/recv_20260504_202046`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4307/4808 valid (89.6%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 600/601, B 601/601, C 601/601, D 598/601, E 600/601, F 601/601, G 601/601, H 105/601.
  - Disposition: valid BSF66F ID12 sample. H is much weaker at CDHG high height, making this a strong one-anchor-degraded robustness sample.
- 2026-05-04 20:28:35: ID13 static TR capture, near ADHE face low height
  - Capture dir: `tr_captures/ID13_static_adhe_low_20260504_202835`
  - Session dir: `tr_captures/ID13_static_adhe_low_20260504_202835/recv_20260504_202836`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4335/4808 valid (90.2%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 599/601, B 601/601, C 601/601, D 601/601, E 600/601, F 601/601, G 601/601, H 131/601.
  - Disposition: valid BSF66F ID13 sample. H remains weak even near the ADHE face at low height; A-G stay essentially complete.
- 2026-05-04 20:34:38: ID14 static TR capture, near ADHE face mid height
  - Capture dir: `tr_captures/ID14_static_adhe_mid_20260504_203438`
  - Session dir: `tr_captures/ID14_static_adhe_mid_20260504_203438/recv_20260504_203439`
  - Result: anchor preflight ready=8/8, TR-only capture completed. Initial cleanup verify saw 8 trailing TR rows; a manual follow-up `cmd_all MODE AOTA` returned `MODE_OK MODE=AOTA`, followed by `tdma hold 1`.
  - BSF66F TR: 3968/4320 valid (91.9%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 538/540, B 536/540, C 538/540, D 538/540, E 539/540, F 539/540, G 536/540, H 204/540.
  - Disposition: valid BSF66F ID14 sample with lower total sweep count than prior 60s captures. H is still the weakest anchor but improves to 37.8% at ADHE mid height.
- 2026-05-04 20:39:35: ID15 static TR capture, near ADHE face high height
  - Capture dir: `tr_captures/ID15_static_adhe_high_20260504_203935`
  - Session dir: `tr_captures/ID15_static_adhe_high_20260504_203935/recv_20260504_203936`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4479/4800 valid (93.3%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 598/600, B 600/600, C 599/600, D 598/600, E 599/600, F 599/600, G 600/600, H 286/600.
  - Disposition: valid BSF66F ID15 sample. H is still weakest but reaches its best static-capture rate so far at ADHE high height.
- 2026-05-04 20:51:52: ID16 static TR capture, center mid height, Tag faces ABEF
  - Capture dir: `tr_captures/ID16_static_center_mid_abef_20260504_205152`
  - Session dir: `tr_captures/ID16_static_center_mid_abef_20260504_205152/recv_20260504_205154`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4467/4808 valid (92.9%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 601/601, B 600/601, C 601/601, D 600/601, E 601/601, F 601/601, G 601/601, H 262/601.
  - Disposition: valid BSF66F ID16 sample. First known-orientation center-mid capture; H is 43.6% with Tag facing ABEF.
- 2026-05-04 20:57:05: ID17 static TR capture, center mid height, Tag faces BCGF
  - Capture dir: `tr_captures/ID17_static_center_mid_bcgf_20260504_205705`
  - Session dir: `tr_captures/ID17_static_center_mid_bcgf_20260504_205705/recv_20260504_205706`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed. Capture had a slow start with near-zero TR for the first several seconds.
  - BSF66F TR: 3826/4192 valid (91.3%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 523/524, B 519/524, C 519/524, D 522/524, E 520/524, F 523/524, G 523/524, H 177/524.
  - Disposition: valid BSF66F ID17 sample with lower total sweep count caused by startup delay. H drops to 33.8% with Tag facing BCGF.
- 2026-05-04 21:01:27: ID18 static TR capture, center mid height, Tag faces CDHG
  - Capture dir: `tr_captures/ID18_static_center_mid_cdhg_20260504_210127`
  - Session dir: `tr_captures/ID18_static_center_mid_cdhg_20260504_210127/recv_20260504_210128`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed.
  - BSF66F TR: 4451/4808 valid (92.6%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 600/601, B 600/601, C 600/601, D 599/601, E 601/601, F 601/601, G 601/601, H 249/601.
  - Disposition: valid BSF66F ID18 sample. H is 41.4% with Tag facing CDHG, close to ID16 ABEF and better than ID17 BCGF.
- 2026-05-04 21:06:57: ID19 static TR capture, center mid height, Tag faces ADHE
  - Capture dir: `tr_captures/ID19_static_center_mid_adhe_20260504_210657`
  - Session dir: `tr_captures/ID19_static_center_mid_adhe_20260504_210657/recv_20260504_210658`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed. BSF66F link setup was delayed long enough that the listener finished before TDMA capture began, so this sample has Tag-side TR but no useful listener UL/UF evidence.
  - BSF66F TR: 4749/4808 valid (98.8%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 600/601, B 601/601, C 601/601, D 601/601, E 601/601, F 601/601, G 601/601, H 543/601.
  - Disposition: valid BSF66F ID19 Tag-side TR sample. H jumps to 90.3% with Tag facing ADHE, a strong orientation-dependence signal.
- 2026-05-04 21:22:36: ID20 static TR capture, center low height, Tag faces ABEF
  - Capture dir: `tr_captures/ID20_static_center_low_abef_20260504_212236`
  - Session dir: `tr_captures/ID20_static_center_low_abef_20260504_212236/recv_20260504_212238`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed. Listener was online and completed normally.
  - BSF66F TR: 4493/4808 valid (93.4%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 599/601, B 593/601, C 599/601, D 595/601, E 599/601, F 595/601, G 598/601, H 315/601.
  - Disposition: valid BSF66F ID20 sample. This retest replaces the earlier ID20 capture, which was likely placed too high. The old folder was moved to `notes/superseded_ID20_static_center_low_abef_20260504_211158` and must not be used for V4 ingestion. The new ID20 median range fingerprint is close to ID21 low (about 161 mm vector distance) and far from the superseded ID20 (about 871 mm).
- 2026-05-04 21:16:32: ID21 static TR capture, center low height, Tag faces BCGF
  - Capture dir: `tr_captures/ID21_static_center_low_bcgf_20260504_211632`
  - Session dir: `tr_captures/ID21_static_center_low_bcgf_20260504_211632/recv_20260504_211633`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed. Listener was online and captured UL/UF evidence during the run.
  - BSF66F TR: 4595/4808 valid (95.6%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 599/601, B 597/601, C 598/601, D 601/601, E 601/601, F 600/601, G 601/601, H 398/601.
  - Disposition: valid BSF66F ID21 sample. At center low height with Tag facing BCGF, H is 66.2%.
- 2026-05-04 21:28:04: ID22 static TR capture, center low height, Tag faces CDHG
  - Capture dir: `tr_captures/ID22_static_center_low_cdhg_20260504_212804`
  - Session dir: `tr_captures/ID22_static_center_low_cdhg_20260504_212804/recv_20260504_212805`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed. Listener was online and captured UL/UF evidence during the run.
  - BSF66F TR: 4628/4808 valid (96.3%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 601/601, B 600/601, C 600/601, D 600/601, E 601/601, F 601/601, G 601/601, H 424/601.
  - Disposition: valid BSF66F ID22 sample. At center low height with Tag facing CDHG, H is 70.5%, currently the strongest H coverage in the low known-orientation group.
- 2026-05-04 21:32:40: ID23 static TR capture, center low height, Tag faces ADHE
  - Capture dir: `tr_captures/ID23_static_center_low_adhe_20260504_213240`
  - Session dir: `tr_captures/ID23_static_center_low_adhe_20260504_213240/recv_20260504_213241`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed. Listener was online and captured UL/UF evidence during the run.
  - BSF66F TR: 4581/4808 valid (95.3%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 600/601, B 601/601, C 601/601, D 601/601, E 601/601, F 599/601, G 601/601, H 377/601.
  - Disposition: valid BSF66F ID23 sample. At center low height with Tag facing ADHE, H is 62.7%, below ID22 CDHG but still strong enough for V4 range use.
- 2026-05-04 21:36:44: ID24 static TR capture, center high height, Tag faces ABEF
  - Capture dir: `tr_captures/ID24_static_center_high_abef_20260504_213644`
  - Session dir: `tr_captures/ID24_static_center_high_abef_20260504_213644/recv_20260504_213645`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed. BSF66F link setup was delayed long enough that the listener finished before TDMA capture began, so this sample has Tag-side TR but no useful listener UL/UF evidence.
  - BSF66F TR: 4710/4808 valid (98.0%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 599/601, B 601/601, C 600/601, D 601/601, E 601/601, F 601/601, G 599/601, H 508/601.
  - Disposition: valid BSF66F ID24 Tag-side TR sample. At center high height with Tag facing ABEF, H is 84.5%, much stronger than the low-height ABEF sample.
- 2026-05-04 21:41:58: ID25 static TR capture, center high height, Tag faces BCGF
  - Capture dir: `tr_captures/ID25_static_center_high_bcgf_20260504_214158`
  - Session dir: `tr_captures/ID25_static_center_high_bcgf_20260504_214158/recv_20260504_214159`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed. Listener was online and captured UL/UF evidence during the run.
  - BSF66F TR: 4578/4808 valid (95.2%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 601/601, B 599/601, C 601/601, D 599/601, E 599/601, F 598/601, G 599/601, H 382/601.
  - Disposition: valid BSF66F ID25 sample. At center high height with Tag facing BCGF, H is 63.6%, lower than high ABEF but still good for V4 range use.
- 2026-05-04 21:45:43: ID26 static TR capture, center high height, Tag faces CDHG
  - Capture dir: `tr_captures/ID26_static_center_high_cdhg_20260504_214543`
  - Session dir: `tr_captures/ID26_static_center_high_cdhg_20260504_214543/recv_20260504_214544`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed. Listener was online and captured UL/UF evidence during the run.
  - BSF66F TR: 4645/4800 valid (96.8%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 598/600, B 600/600, C 600/600, D 600/600, E 600/600, F 600/600, G 600/600, H 447/600.
  - Disposition: valid BSF66F ID26 sample. At center high height with Tag facing CDHG, H is 74.5%, between high BCGF and high ABEF.
- 2026-05-04 21:49:18: ID27 static TR capture, center high height, Tag faces ADHE
  - Capture dir: `tr_captures/ID27_static_center_high_adhe_20260504_214918`
  - Session dir: `tr_captures/ID27_static_center_high_adhe_20260504_214918/recv_20260504_214920`
  - Result: anchor preflight ready=8/8, cleanup returned Tag to AOTA, TR-only capture completed. Listener was online and captured UL/UF evidence during the run.
  - BSF66F TR: 4724/4800 valid (98.4%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - Per-anchor valid rows: A 600/600, B 598/600, C 598/600, D 598/600, E 600/600, F 599/600, G 599/600, H 532/600.
  - Disposition: valid BSF66F ID27 sample. At center high height with Tag facing ADHE, H is 88.7%, the strongest H coverage in the high known-orientation group.
- 2026-05-04 21:55:44: ID28 Roto TR capture, small tilt, antenna faces ABEF
  - Capture dir: `tr_captures/ID28_roto_small_abef_2roto_20260504_215544`
  - Session dir: `tr_captures/ID28_roto_small_abef_2roto_20260504_215544/recv_20260504_215545`
  - Result: anchor preflight ready=8/8, cleanup returned Tags to AOTA, TR-only capture completed. F66F was powered off, so the first three-Tag attempt was interrupted and archived at `notes/interrupted_ID28_roto_small_abef_20260504_215449_f66f_off`; the valid ID28 sample uses the two RotoTag devices only.
  - Total TR: 18292/19224 valid (95.2%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - BS2DCE TR: 9486/9600 valid (98.8%). Per-anchor valid rows: A 1197/1200, B 1196/1200, C 1197/1200, D 1197/1200, E 1196/1200, F 1196/1200, G 1194/1200, H 1113/1200.
  - BSDC91 TR: 8806/9624 valid (91.5%). Per-anchor valid rows: A 1201/1203, B 1199/1203, C 1199/1203, D 1195/1203, E 1200/1203, F 1202/1203, G 1200/1203, H 410/1203.
  - Listener evidence: `ul.csv` 451 rows, `uf.csv` 2846 rows. The wrapper terminated the listener after capture completion (`returncode=-15`), but listener files contain useful evidence.
  - Disposition: valid two-RotoTag ID28 sample. BS2DCE is excellent and BSDC91 is usable overall, but BSDC91-H is weak (34.1%) and should be monitored in V4 residual heatmaps.
- 2026-05-04 22:00:09: ID29 Roto TR capture, small tilt, antenna faces BCGF
  - Capture dir: `tr_captures/ID29_roto_small_bcgf_2roto_20260504_220009`
  - Session dir: `tr_captures/ID29_roto_small_bcgf_2roto_20260504_220009/recv_20260504_220010`
  - Result: anchor preflight ready=8/8, cleanup returned Tags to AOTA, TR-only capture completed. F66F remained powered off, so this sample also uses the two RotoTag devices only.
  - Total TR: 18304/19208 valid (95.3%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - BS2DCE TR: 9454/9600 valid (98.5%). Per-anchor valid rows: A 1195/1200, B 1193/1200, C 1192/1200, D 1195/1200, E 1195/1200, F 1191/1200, G 1197/1200, H 1096/1200.
  - BSDC91 TR: 8850/9608 valid (92.1%). Per-anchor valid rows: A 1200/1201, B 1195/1201, C 1200/1201, D 1200/1201, E 1192/1201, F 1195/1201, G 1197/1201, H 471/1201.
  - Listener evidence: `ul.csv` 679 rows, `uf.csv` 2889 rows. The wrapper terminated the listener after capture completion (`returncode=-15`), but listener files contain useful evidence.
  - Disposition: valid two-RotoTag ID29 sample. BS2DCE remains excellent; BSDC91-H improves slightly versus ID28 (39.2% vs 34.1%) but is still the weakest per-anchor channel.
- 2026-05-04 22:05:50: ID30 Roto TR capture, small tilt, antenna faces CDHG
  - Capture dir: `tr_captures/ID30_roto_small_cdhg_2roto_20260504_220550`
  - Session dir: `tr_captures/ID30_roto_small_cdhg_2roto_20260504_220550/recv_20260504_220551`
  - Result: anchor preflight ready=8/8, cleanup returned Tags to AOTA, TR-only capture completed. F66F remained powered off, so this sample also uses the two RotoTag devices only.
  - Total TR: 18160/19208 valid (94.5%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - BS2DCE TR: 9459/9600 valid (98.5%). Per-anchor valid rows: A 1199/1200, B 1194/1200, C 1195/1200, D 1195/1200, E 1196/1200, F 1193/1200, G 1199/1200, H 1088/1200.
  - BSDC91 TR: 8701/9608 valid (90.6%). Per-anchor valid rows: A 1199/1201, B 1196/1201, C 1198/1201, D 1197/1201, E 1198/1201, F 1199/1201, G 1197/1201, H 317/1201.
  - Listener evidence: `ul.csv` 610 rows, `uf.csv` 3052 rows. The wrapper terminated the listener after capture completion (`returncode=-15`), but listener files contain useful evidence.
  - Disposition: valid two-RotoTag ID30 sample. BS2DCE remains excellent; BSDC91-H is weaker in this orientation (26.4%) and should be treated carefully in V4 residual heatmaps.
- 2026-05-04 22:10:28: ID31 Roto TR capture, small tilt, antenna faces ADHE
  - Capture dir: `tr_captures/ID31_roto_small_adhe_2roto_20260504_221028`
  - Session dir: `tr_captures/ID31_roto_small_adhe_2roto_20260504_221028/recv_20260504_221029`
  - Result: anchor preflight ready=8/8, cleanup returned Tags to AOTA, TR-only capture completed. F66F remained powered off, so this sample also uses the two RotoTag devices only.
  - Total TR: 18142/19216 valid (94.4%), all anchors seen, TS/TF=0 as expected for b65 tronly.
  - BS2DCE TR: 9461/9608 valid (98.5%). Per-anchor valid rows: A 1199/1201, B 1191/1201, C 1190/1201, D 1195/1201, E 1191/1201, F 1190/1201, G 1195/1201, H 1110/1201.
  - BSDC91 TR: 8681/9608 valid (90.4%). Per-anchor valid rows: A 1199/1201, B 1193/1201, C 1197/1201, D 1193/1201, E 1201/1201, F 1197/1201, G 1197/1201, H 304/1201.
  - Listener evidence: `ul.csv` 498 rows, `uf.csv` 2958 rows. The wrapper terminated the listener after capture completion (`returncode=-15`), but listener files contain useful evidence.
  - Disposition: valid two-RotoTag ID31 sample. This completes the four small-tilt face directions. BS2DCE remains excellent; BSDC91-H is weakest in the CDHG/ADHE directions and should be robustly downweighted if needed.

Post-run placement consistency check:
- ID19 range fingerprint matches the center-mid known-orientation group.
- ID20 was retested after a suspected placement error. The current ID20 folder
  is the only valid ID20 sample in `tr_captures/`; the superseded capture is
  archived under `notes/` and should be ignored by solvers.
- ID20 and ID21 now form a consistent center-low known-orientation pair.
- ID32 was started but stopped because rain made the setup impractical. The
  partial folder is archived at
  `notes/rain_aborted_ID32_roto_mid_abef_2roto_20260504_221506` and must not be
  used for V4 ingestion.

## TR Capture Worklist

Principle: each ID is one clear physical setup. Do not bundle low/mid/high
or multiple faces into one capture. This keeps the V4 residual analysis
traceable.

Important labeling note: setup words such as `center`, `low`, `mid`,
`high`, `near ABEF face`, and similar labels are approximate human placement
labels, not precise ground-truth coordinates. The Tags are positioned by hand,
so these labels only mean "placed as accurately as practical for that category".
They must be used for grouping and diagnostics, not as exact constraints in
the solver.

Even repeated labels are not identical physical coordinates. For example,
different `high` captures may still have small height/angle/placement
differences because the Tag is manually placed each time. Treat each capture
directory as its own measurement condition, with the label used only as a
human-readable grouping hint.

The same caveat applies to all `mid` orientation captures. Even when the label
says `center mid height`, the actual Tag coordinates are not guaranteed to be
identical across captures because BSF66F is manually rotated and re-placed.
These captures are intended to compare approximate orientation effects, not to
represent a perfectly fixed point rotation around one exact axis.

Execution flow:
1. User prepares the physical setup for the next ID.
2. User says `done`.
3. Codex runs the capture and stores it under `tr_captures/ID##_...`.
4. Codex records the result here and then moves to the next ID.

Static captures use the 3 old Tags (`BSF66F`, `BS2DCE`, `BSDC91`) with
TR-only output. Roto captures use the RotoTag setup when available.

### Static Tag Captures

| ID | Type | Setup | Duration |
|---:|---|---|---:|
| 01 | Static | Center, low height, Tag antenna orientation unknown | 60s |
| 02 | Static | Center, mid height, Tag antenna orientation unknown | 60s |
| 03 | Static | Center, high height, Tag antenna orientation unknown | 60s |
| 04 | Static | Near ABEF face, low height | 60s |
| 05 | Static | Near ABEF face, mid height | 60s |
| 06 | Static | Near ABEF face, high height | 60s |
| 07 | Static | Near BCGF face, low height | 60s |
| 08 | Static | Near BCGF face, mid height | 60s |
| 09 | Static | Near BCGF face, high height | 60s |
| 10 | Static | Near CDHG face, low height | 60s |
| 11 | Static | Near CDHG face, mid height | 60s |
| 12 | Static | Near CDHG face, high height | 60s |
| 13 | Static | Near ADHE face, low height | 60s |
| 14 | Static | Near ADHE face, mid height | 60s |
| 15 | Static | Near ADHE face, high height | 60s |
| 16 | Static | Center mid height, Tag faces ABEF | 60s |
| 17 | Static | Center mid height, Tag faces BCGF | 60s |
| 18 | Static | Center mid height, Tag faces CDHG | 60s |
| 19 | Static | Center mid height, Tag faces ADHE | 60s |
| 20 | Static | Center low height, Tag faces ABEF | 60s |
| 21 | Static | Center low height, Tag faces BCGF | 60s |
| 22 | Static | Center low height, Tag faces CDHG | 60s |
| 23 | Static | Center low height, Tag faces ADHE | 60s |
| 24 | Static | Center high height, Tag faces ABEF | 60s |
| 25 | Static | Center high height, Tag faces BCGF | 60s |
| 26 | Static | Center high height, Tag faces CDHG | 60s |
| 27 | Static | Center high height, Tag faces ADHE | 60s |

### RotoTag Captures

| ID | Type | Setup | Duration | Status |
|---:|---|---|---:|---|
| 28 | Roto | Small tilt, antenna faces ABEF | 120s | Done |
| 29 | Roto | Small tilt, antenna faces BCGF | 120s | Done |
| 30 | Roto | Small tilt, antenna faces CDHG | 120s | Done |
| 31 | Roto | Small tilt, antenna faces ADHE | 120s | Done |
| 32 | Roto | Mid tilt, antenna faces ABEF | 120s | Not used; rain-aborted partial run archived under `notes/` |
| 33 | Roto | Mid tilt, antenna faces BCGF | 120s | Not run |
| 34 | Roto | Mid tilt, antenna faces CDHG | 120s | Not run |
| 35 | Roto | Mid tilt, antenna faces ADHE | 120s | Not run |
| 36 | Roto | High tilt, antenna faces ABEF | 120s | Not run |
| 37 | Roto | High tilt, antenna faces BCGF | 120s | Not run |
| 38 | Roto | High tilt, antenna faces CDHG | 120s | Not run |
| 39 | Roto | High tilt, antenna faces ADHE | 120s | Not run |
| 40 | Roto | Extra pass for the worst/most suspect direction | 120s | Not run |

Outdoor capture ended after ID31 because rain made further RotoTag placement
impractical. For this data set, only Roto IDs 28-31 should be ingested. IDs 32
and later are intentionally absent from `tr_captures/`.

Capture naming convention:
- `tr_captures/ID01_static_center_low_<timestamp>`
- `tr_captures/ID16_static_center_mid_abef_<timestamp>`
- `tr_captures/ID28_roto_small_abef_<timestamp>`

After the worklist has enough coverage, build V4 data from:
- `sweeps/inter_anchor_500set_20260504_185011/pairs_all.csv`
- selected `tr_captures/ID##_...` directories
