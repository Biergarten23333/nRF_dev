# Broadcast b51 Validation

## 2026-05-02 b51 Tag g1200 validation checkpoint

Build/deploy:
- Tag marker: `alt-bcast-b51-8anc-fastrx-g1200-r1000`
- Tag build: `build-alt-bcast-b51-8anc-fastrx-tag-g1200-r1000-raw0-cont0`
- Tag OTA: `logs/alt_bcast_b51_tag_ota_20260502_013749`
- OTA match: BSF66F=true, BS2DCE=true, BSDC91=true
- Anchor fleet stayed on a13 nosleep hotpath g1200/r1000.
- Anchor responder force before capture: ready=8/8.

60s probe #1, accidental Master default TDMA 40/24:
- Capture: `logs/alt_bcast_b51_3tag_motion_probe60_20260502_014411`
- Master runtime TDMA: period=40ms active=24ms
- positions_all=329, tf_all=0
- Per tag: BSF66F=147, BS2DCE=107, BSDC91=75
- A/rank0 present: yes, A appears in 244 position rows
- RXG: mask=0xff pc=8; slot_to_txdone_us median=579 p95=732 max=915; txdone_to_rxstart_us median=61 p95=91 max=183
- Verdict: UWB/rank0 OK, but throughput too low because Master was still on old 40ms scheduler.

60s probe #2, corrected Master_Tag TDMA 10/9 carrier:
- Master_Tag carrier: `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b51-8anc-fastrx-g1200-r1000-tdma10-carrier`
- Capture: `logs/alt_bcast_b51_3tag_motion_probe60_tdma10_20260502_014916`
- Master runtime TDMA confirmed: period=10ms active=9ms; final assignment slot_count=10, slots 0/3/7
- positions_all=277, tf_all=175
- Per tag positions: BSF66F=81, BS2DCE=118, BSDC91=78
- Per tag filtered: BSF66F=42, BS2DCE=133, BSDC91=0; filter_reason=rms for all TF rows
- A/rank0 present: yes, A appears in 208 accepted position rows; TF rows were all ABCD and all include A
- Anchor distribution in accepted positions: A=208, B=205, C=215, D=113, E=109, F=151, G=207, H=22
- RXG: mask=0xff pc=8; win=8165us; slot_to_txdone_us median=579 p95=671 max=976; txdone_to_rxstart_us median=61 p95=91 max=152
- Listener: uf=873, ul=147; parsed UL mostly H=145, G=2. Listener parser remains incomplete versus Tag-side anchor usage.

Conclusion:
- b51 aligned Tag guard to g1200 and all three Tags matched successfully.
- Anchor a13 hotpath + Tag b51 fastrx are not losing rank0; A is present in position output.
- Tag hot path is healthy: txdone_to_rxstart p95 ~91us.
- Throughput target is NOT validated: 277 positions/60s is far below the 120s success target extrapolation, and tdma10 produced many RMS rejects.
- Do not run 120s as validation yet; next blocker is position-output/BLE/reporting or scheduler/solver behavior under 10ms TDMA, not guard/rank0.
## 2026-05-02 b52 RMS-off Diagnostic

- Tag marker: `alt-bcast-b52-8anc-fastrx-g1200-r1000-rms0`
- Anchor marker: `alt-bcast-a13-nosleep-hotpath-g1200-r1000`
- Master_Tag carrier: b52 payload, TDMA `period=10ms active=9ms`
- Tag OTA: BSF66F/BS2DCE/BSDC91 all `match=True`
- Anchor preflight: `ready=8/8`
- Capture: `logs/alt_bcast_b52_3tag_motion_probe60_rms0_20260502_020611`

Result:
- `positions_all=367`
- `tf_all=0`
- Per-tag positions: BSF66F=153, BS2DCE=119, BSDC91=95
- Raw TS notify lines: 409
- A/rank0 present in accepted positions: 212
- Anchor use counts: A=212, B=288, C=260, D=141, E=197, F=273, G=303, H=23
- RMS per tag:
  - BSF66F: median=20mm, p95=65mm, max=118mm
  - BS2DCE: median=14mm, p95=54mm, max=84mm
  - BSDC91: median=14mm, p95=55mm, max=92mm
- RXG timing:
  - `mask=0xff`, `pc=8`, `win=8165us`
  - `slot_to_txdone_us`: median=579, p95=671, max=885
  - `txdone_to_rxstart_us`: median=61, p95=91, max=183

Interpretation:
- RMS gate was active in b51 and is now disabled correctly: `tf_all=0`.
- Throughput is not caused by RMS filtering. Total solved/output positions remain low (`367/60s = 6.1Hz total = 2.0Hz/tag average`) versus the 30Hz total target.
- UWB timing is healthy: rank0 is present, 8-anchor broadcast mask is active, and Tag RX opens quickly.
- The next likely bottleneck is CPU-side position solve/output. The current full-sweep policy uses `UWB_TAG_LOC_SUBSET_POLICY_MIN4`, which evaluates many anchor subsets for 8-anchor solves. On nRF52832 this likely dominates the ~350-500ms `motion_dt_ms` between TS rows.
- Next test: b53 fast 8-anchor solver path using all valid anchors once, with RMS gate still off, to separate solver CPU cost from UWB/BLE transport.

## 2026-05-02 b53 Fast-Loc Diagnostic

- Code change: added `UWB_TAG_LOC_SUBSET_POLICY_ALL_VALID` and build flag `APP_TAG_LOC_FAST_ALL_VALID_ENABLE`.
- Tag marker: `alt-bcast-b53-fastloc-8anc-g1200-r1000-rms0`
- Anchor marker: unchanged `alt-bcast-a13-nosleep-hotpath-g1200-r1000`
- Master_Tag carrier: b53 payload, TDMA `period=10ms active=9ms`, LFRC assert passed before flash.
- Tag OTA: BSF66F/BS2DCE/BSDC91 all `match=True`
- Anchor preflight: `ready=8/8`
- Capture: `logs/alt_bcast_b53_fastloc_3tag_motion_probe60_20260502_022126`

Result:
- `positions_all=346`
- `tf_all=0`
- Per-tag positions: BSF66F=147, BS2DCE=75, BSDC91=124
- Raw TS notify lines: 385
- RXG lines: 171
- Listener: UF=994, UL=166
- Motion interval remained high:
  - BSF66F: median `motion_dt_ms=393`, p95=454
  - BS2DCE: median `motion_dt_ms=950`, p95=1065
  - BSDC91: median `motion_dt_ms=402`, p95=947
- Sweep deltas between TS rows were mostly 1, so output rows are not being obviously dropped after solve; the Tag is only completing/reporting solved sweeps every hundreds of ms.

Interpretation:
- b53 did not improve throughput over b52. Fast all-valid solver alone is not the primary limiter, or the limiter is before/around solve scheduling rather than subset scoring.
- UWB hot timing remains healthy; no TDMA guard diagnostics, no slot guard, no sweep budget cut, no BLE tx pool exhaustion, and `tf_all=0`.
- Next diagnosis should instrument timing inside the Tag loop:
  - slot wait duration (`broadcast_tdma_wait_next_slot_start`)
  - burst/collector duration
  - range post-processing duration
  - localization solve duration
  - BLE publish/print duration
  - time from `ss_twr_init_alt_finish_sweep()` to next slot wait
- This should be a diag-only b54 build before more architecture changes.
