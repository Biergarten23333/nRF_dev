# AutoPos Restore Checkpoint - a14

Date: 2026-05-02

## Goal

Restore the known-good AutoPos inter-anchor sweep behavior while preserving the
validated broadcast ranging baseline.

## Diagnosis

The known-good dual-master AutoPos sweeps used Anchor firmware
`anchor-runtime-force-20260426_2` and completed A-H successfully. Current
deployed broadcast Anchor firmware `alt-bcast-a13-nosleep-hotpath-g1200-r1000`
kept broadcast ranging stable, but AutoPos sweep repeatedly failed after SW-A:

- SW-A produced partial/low-quality rows.
- SW-B entered `autopos apply already running`.
- Power cycle and known-good host parameters did not fix it.

Code comparison showed the AutoPos state-machine code was largely unchanged.
The concrete broadcast regression was in `src/ss_twr_anchor_init.c`:

- `UWB_MSG_ALT_POLL_FRAME_LEN` is 17 in the broadcast branch.
- AutoPos anchor-master TX poll buffer was still 13 bytes.
- `uwb_ss_twr_build_poll_frame()` writes up to `UWB_MSG_ALT_POLL_FRAME_LEN`,
  causing static-buffer overrun during every AutoPos poll build.
- The broadcast branch also enabled DW1000 hardware frame filtering in the
  AutoPos matrix-sweep path, whereas the known-good runtime-force path ran the
  inter-anchor sweep without hardware frame filtering.

## Fix

File changed:

- `src/ss_twr_anchor_init.c`

Changes:

- `ss_twr_anchor_init_tx_poll_msg` is now sized as
  `UWB_MSG_ALT_POLL_FRAME_LEN`.
- AutoPos anchor-master radio setup now explicitly disables DW1000 hardware
  frame filtering, matching the known-good runtime-force behavior.
- Broadcast responder hot path in `ss_twr_resp.c` is unchanged and still uses
  `APP_UWB_HW_FRAME_FILTER_ENABLE=1`.

## Build Artifacts

Anchor marker:

- `alt-bcast-a14-autoposfix-g1200-r1000`

Anchor OTA build:

- `build-anchor-unified-ota-alt-bcast-a14-autoposfix-g1200-r1000`
- `build-anchor-unified-ota-alt-bcast-a14-autoposfix-g1200-r1000/dfu_application.zip`
- `build-anchor-unified-ota-alt-bcast-a14-autoposfix-g1200-r1000/anchor/zephyr/zephyr.signed.bin`

Master_Anchor B120 carrier:

- `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a14-autoposfix-g1200-r1000-carrier`
- `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a14-autoposfix-g1200-r1000-carrier/zephyr/merged_domains.hex`

Verification:

- Anchor OTA payload kind check passed.
- Active OTA payload lock is `kind=anchor`, marker
  `alt-bcast-a14-autoposfix-g1200-r1000`.
- B120 internal LFRC assert passed for the Master_Anchor carrier.

## Deployment Status

No hardware was flashed or OTA-deployed during this checkpoint.

Recommended staged validation:

1. Flash Master_Anchor B120 carrier to SNR `960148546`.
2. OTA Anchor A only to a14.
3. Verify responder ready.
4. Run AutoPos A/B probe.
5. If SW-A quality improves and SW-B starts producing rows, OTA B-H.
6. Re-run full A-H sweep with `sw_sets=10`, `prewarm_sw_sets=10`.

## 2026-05-02 Staged Anchor A Result

- Master_Anchor B120 was flashed once with explicit SNR `960148546` using the
  LFRC-asserted carrier:
  `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a14-autoposfix-g1200-r1000-carrier/zephyr/merged_domains.hex`
- Anchor A OTA upload completed successfully:
  `logs/alt_bcast_a14_autoposfix_anchorA_ota_20260502_170824/`
- Post OTA responder runtime verify returned `ready=8/8`.
- VERSION readback remained `actual=-`, same known control-plane readback issue.
- A/B AutoPos probe was started with old successful parameters:
  `sw_sets=10`, `prewarm_sw_sets=10`, `warmup_min_quality=90`.
- Probe did not reach SW data collection. It failed in SW-A precheck after
  `anchor reset all autopos`.
- Key log:
  `autopos_pipeline/logs/a14_anchorA_AB_probe_20260502_171048/round_A/master.log`
- Before reset, Anchor A (`4DC6B8187E33803AE8601FB0D7992B96`) appeared as
  connected/ready on peer slot 5.
- After reset/config-window handling, SW-A repeatedly reported:
  `AUTOPOS wait anchor ready timeout: uuid=4DC6B8187E33803AE8601FB0D7992B96`
- Recovery responder verify after abort only reached `ready=7/8`; the missing
  UUID was Anchor A.

Conclusion: a14 is not safe to deploy beyond Anchor A. The buffer/frame-filter
fix may still be necessary, but this staged test exposed a separate blocker:
Anchor A on a14 does not reliably return to BLE control ready after AutoPos
reset/role handoff. Do not OTA B-H until that handoff issue is fixed or A is
recovered.

## 2026-05-02 a15 Buffer-Only Staged Result

Purpose: isolate the definite AutoPos poll buffer overflow fix from the a14
frame-filter behavior change.

Code delta from a13:

- `src/ss_twr_anchor_init.c`
- `ss_twr_anchor_init_tx_poll_msg` sized to `UWB_MSG_ALT_POLL_FRAME_LEN`.
- AutoPos radio setup restored to the a13 frame-filter/PAN/address behavior.

Build artifacts:

- Anchor marker: `alt-bcast-a15-autoposbuf-g1200-r1000`
- Anchor build:
  `build-anchor-unified-ota-alt-bcast-a15-autoposbuf-g1200-r1000`
- Master_Anchor carrier:
  `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a15-autoposbuf-g1200-r1000-carrier`

Deployment:

- Master_Anchor SNR `960148546` was flashed with the LFRC-asserted a15 carrier.
- Anchor A only was OTA-updated to a15:
  `logs/alt_bcast_a15_autoposbuf_anchorA_ota_20260502_173316/`
- OTA upload succeeded.
- Post responder runtime verify returned `ready=8/8`.
- VERSION readback remained `actual=-`, same known readback issue.

A/B AutoPos probe:

- Log directory:
  `autopos_pipeline/logs/a15_anchorA_AB_probe_20260502_173530/`
- Session role guard succeeded: matrix `ready=8/8`.
- Probe did not reach SW data collection.
- It stalled in SW-A precheck after `anchor reset all autopos`.
- Key failure pattern:
  - `AUTOPOS anchor A busy; requesting runtime stop`
  - `STOP` unsupported or timed out (`rc=-116`)
  - fallback reboot/config-window path
  - `AUTOPOS wait anchor ready timeout` for Anchor A UUID
    `4DC6B8187E33803AE8601FB0D7992B96`
- The same handoff class then appeared for other anchors during the all-reset
  precheck path.

Recovery:

- The stuck probe process was interrupted.
- Anchor A was direct-recovered to the frozen a13 baseline using explicit SNR
  `760186071`:
  `build-anchor-unified-ota-alt-bcast-a13-nosleep-hotpath-g1200-r1000/merged.hex`
- Master_Anchor was restored to the a13 LFRC carrier:
  `build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a13-nosleep-hotpath-g1200-r1000-carrier/zephyr/merged_domains.hex`
- Active OTA payload lock was restored to:
  `alt-bcast-a13-nosleep-hotpath-g1200-r1000`
- Final responder verify succeeded:
  `logs/anchor_ready_after_restore_master_a13_carrier_20260502_174029/verify.log`
  with `ready=8/8`.

Conclusion:

- a15 buffer-only does not fix the current AutoPos blocker.
- The blocker is not SW matrix ranging data yet; it occurs earlier, in the
  AutoPos reset/runtime-stop/config-window handoff.
- The broadcast baseline has been restored: A-H on a13 responder firmware,
  Master_Anchor on a13 carrier, active OTA payload lock on a13, responder
  runtime `ready=8/8`.
- Do not deploy a14/a15 to B-H.
- Next AutoPos work should compare the old successful AutoPos reset/handoff
  state machine against the current broadcast branch and restore that behavior,
  while keeping the b55/a13 broadcast ranging baseline unchanged.
