# Agent Handoff - Restore No-RFD Baseline After RFD/Diagnostics Experiments

Date: 2026-06-27

This handoff is for the next agent. The user wants the system restored to the
known pre-RFD baseline and wants clear separation between stable ranging and
experimental diagnostics. Do not continue random experiments. The immediate
job is recovery and verification.

## Objective

Restore the BioSpur UWB system to the pre-RFD/no-diagnostics firmware set, then
rebuild confidence from the smallest possible no-RFD baseline upward:

1. Restore firmware freeze from `firmware_freeze/autopos_full_system_20260625_0245`.
2. Verify no-RFD high-rate ranging first.
3. Prove 1-tag, 2-tag, 3-tag baseline before attempting 6-tag.
4. Do not re-enable RFD/RF diagnostics until no-RFD baseline is healthy again.

The user is angry because too much time was spent testing RFD/diagnostic paths
without first pinning a minimal stable baseline. Do not argue with that. The
correct recovery stance is conservative and evidence-driven.

## Critical Update - 3-Tag 10 Hz High ge7/ge8 Is Solved

Later testing on 2026-06-27 resolved the earlier apparent 3-tag capacity
failure. Do not continue investigating TDMA capacity, RFD diagnostics, or "too
many tags" as the root cause of the bad 3-tag result.

The root cause was a Tag receive-window timing budget bug plus a Tag/Anchor
spacing mismatch:

- Anchor 7 is always the last responder because the responder rank is fixed by
  anchor id. There is no rank rotation.
- Anchor 7 transmits at `1200 + 7 * spacing`.
- The anchors were effectively using flat `1000 us` response spacing.
- `TAIL_COMPRESS_ENABLE=0`, so the supposed `r800` tail spacing was not active.
- The Tags were built with `RESP_SPACING_US=800`, so the Tag-side response
  receive window was calculated too short.
- The bad Tag window was about `6765 us`.
- Anchor 7's frame completed around `8.45 ms`.
- The Tag receive window closed about `1.4 ms` too early, so A7 was dropped
  repeatedly.
- With A7 systematically missing, ge8 was impossible and ge7 was artificially
  capped.

Measured before/after on a 120 s clean visible-3 capture with no RFD and no CIR:

```text
overall ge7: 0.774 -> 0.978
overall ge8: 0.245 -> 0.967
A7 valid for the three tags: 0.00/0.33/0.57 -> 0.98/0.98/0.98
sweeps: 669/1059/357 -> 1095/1050/1070
```

Minimal fix:

```text
SS_TWR_INIT_ALT_BCAST_TAIL_MARGIN_US: 300 -> 800
Tag RESP_SPACING_US: 800 -> 1000
Anchor firmware unchanged
RFD remained off
```

The source location noted during debugging was:

```text
SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c:425
```

Important interpretation:

- The freeze README statement that "`800` is only tail spacing" was misleading
  for this failure path. The Tag used `RESP_SPACING_US` as the basis for its RX
  window length.
- The three Tags involved in the earlier confusing baseline were running an
  older May image, `stable10x9-tr12-bdbs-20260512`, not the intended freeze
  `r800` image.
- After rebuilding/flashing the corrected Master_Tag path, the Master_Tag CDC
  path changed to:

```text
/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
```

Use that port for current post-fix capture commands unless the device
enumeration changes again.

The updated conclusion is:

```text
3-tag 10 Hz high ge7/ge8 is achievable and has been demonstrated.
The earlier failure was a receive-window timing bug, not system capacity.
Keep RFD off unless explicitly testing diagnostics.
```

## Hard Rules

- Do not direct J-Link flash deployed Tag bodies.
- Do not direct J-Link flash deployed Anchor bodies.
- Tag bodies are OTA-only for routine restore.
- Anchor bodies are OTA-only for routine restore.
- Master_Tag and Master_Anchor B120 controllers may be flashed with repository
  J-Link scripts when needed.
- Never use `nrfjprog`.
- B120 master-control images must be LFRC/internal oscillator builds.
- Before any B120 flash, run:

```bash
scripts/assert_b120_internal_osc_build.sh <build-or-image-path>
```

- For Master_Anchor SNR `960148546`, the protected-flash override is required:

```bash
BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1
```

- Do not confuse Tag OTA with J-Link. A Tag J-Link not being visible is not an
  OTA blocker. For Tags, the real visibility gate is BLE advertising/connect
  through Master_Tag.

## Current Device / Port State

Last checked on 2026-06-27 around 14:24 CEST:

```text
Master_Tag CDC:
  /dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00

Master_Anchor CDC:
  /dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00

BLE advertising listener dongle:
  /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Listener_760AE3DFC3CD8F38-if00

Master_Tag J-Link:
  SNR 1050070698

Master_Anchor J-Link:
  SNR 960148546

Listener E J-Link:
  SNR 760184767

Old irrelevant listener J-Link:
  SNR 760185886
```

Historical note: during an earlier freeze-restore attempt the Master_Tag USB
product string appeared as generic-ish `Master_Tag_BioSpur_BLE_Control`. After
the later corrected no-RFD timing build/test, the current reported Master_Tag
CDC path is:

```text
/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
```

Use the serial suffix `6918E0384172A49F` and/or SNR mapping if the product name
changes again.

## Firmware Freeze To Restore

Authoritative freeze directory:

```text
firmware_freeze/autopos_full_system_20260625_0245
```

Freeze README:

```text
firmware_freeze/autopos_full_system_20260625_0245/README.md
```

Selected firmware set in that freeze:

### Tag body image

```text
Build:
  firmware_freeze/autopos_full_system_20260625_0245/builds/build-tag-ble-unified-tdmafix-nodiag-r800-20260624

Marker:
  compact-sampled-tdmafix-nodiag-r800-20260624

DFU zip:
  firmware_freeze/autopos_full_system_20260625_0245/builds/build-tag-ble-unified-tdmafix-nodiag-r800-20260624/dfu_application.zip
```

Targets covered by freeze evidence:

```text
BSF66F, BS2DCE, BSDC91, BS9336, BS955A, BSCCF4
```

### Master_Tag B120 image

```text
Build:
  firmware_freeze/autopos_full_system_20260625_0245/builds/build-master-control-b120-m1-master-tag-lfrc-tdma-explicit-nodiag-r800-20260624

Image used for flash:
  firmware_freeze/autopos_full_system_20260625_0245/builds/build-master-control-b120-m1-master-tag-lfrc-tdma-explicit-nodiag-r800-20260624/zephyr/merged_domains.hex

SNR:
  1050070698
```

### Anchor body image

```text
Build:
  firmware_freeze/autopos_full_system_20260625_0245/builds/build-anchor-unified-ota-cir-us-r800-20260624

Marker:
  anchor-cir-us-r800-20260624

DFU zip:
  firmware_freeze/autopos_full_system_20260625_0245/builds/build-anchor-unified-ota-cir-us-r800-20260624/dfu_application.zip
```

Freeze README notes that this name contains `r800`, but stable base timing is
still guard `1200 us` and responder spacing `1000 us`; `800 us` is a tail
responder spacing detail. Do not reinterpret this as a global `800 us` spacing
change without checking the build config.

### Master_Anchor B120 image

```text
Build:
  firmware_freeze/autopos_full_system_20260625_0245/builds/build-master-control-b120-m1-master-anchor-lfrc-cir-us-r800-20260624

Image to flash:
  firmware_freeze/autopos_full_system_20260625_0245/builds/build-master-control-b120-m1-master-anchor-lfrc-cir-us-r800-20260624/zephyr/merged_domains.hex

SNR:
  960148546
```

## What Has Already Been Restored

Only one restore action has been completed:

```text
Master_Tag B120 was flashed back to:
  build-master-control-b120-m1-master-tag-lfrc-tdma-explicit-nodiag-r800-20260624/zephyr/merged_domains.hex
```

Command that succeeded:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast

B120_SNR=1050070698 scripts/flash_master_control_b120_m1_noninteractive.sh \
  /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/firmware_freeze/autopos_full_system_20260625_0245/builds/build-master-control-b120-m1-master-tag-lfrc-tdma-explicit-nodiag-r800-20260624/zephyr/merged_domains.hex
```

The script reported:

```text
[flash-guard] SNR=1050070698 role=Master_Tag
[ok] B120 internal LFRC verified
tool=flash_master_control_b120_m1_noninteractive action=ok snr=1050070698
```

The user then said `stop`. Therefore:

- Master_Anchor has not been restored.
- Tags have not been OTA-restored.
- Anchors have not been OTA-restored.
- No post-restore no-RFD baseline has been run after the Master_Tag flash.

## Current Worktree State

The worktree is dirty. Last known modified files relevant to this recovery:

```text
SS-TWR/alt-SS-TWR/broadcast/apps/master/src/master_multi_app.c
SS-TWR/alt-SS-TWR/broadcast/apps/master_ota/generated/active_ota_payload.json
SS-TWR/alt-SS-TWR/broadcast/apps/master_ota/generated/anchor_ota_manifest.json
SS-TWR/alt-SS-TWR/broadcast/apps/master_ota/generated/ota_image.inc
SS-TWR/alt-SS-TWR/broadcast/scripts/run_6tag_nordiag_baseline_candidate.sh
SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py
docs/rf_diag_overnight_20260625.md
```

Do not assume these modifications are good. Some were useful instrumentation,
but the recovery target is the freeze behavior, not the current modified code.

If reverting source changes becomes necessary, do not use destructive broad
commands. Inspect diffs first and preserve logs/docs unless the user explicitly
asks to clean them.

## What Went Wrong / Lessons

### 1. RFD diagnostics hurt baseline

The user’s hard requirement became:

```text
Priority 1: preserve 7/8 and 8/8 anchor success rate.
Priority 2: diagnostics only through paths that do not affect ranging.
```

Tag-side RFD/diagnostic output degraded `ge7`/valid rate and did not provide a
safe immediate path. It must stay off for baseline recovery.

Do not re-enable:

```text
tag_cir compact
tag_cir full
RFD output in the main ranging stream
range diagnostic rows in the hot path
```

unless no-RFD baseline has first been restored and the user explicitly asks for
a controlled diagnostic experiment.

### 2. J-Link vs OTA confusion

One earlier mistake was treating missing Tag J-Link visibility as meaningful
for Tag recovery. That is wrong.

Correct:

- Tags restore via BLE OTA/NUS through Master_Tag.
- Anchor bodies restore via BLE OTA through Master_Anchor.
- A Tag J-Link not being visible does not prove it cannot be OTA-restored.
- For Tags, use BLE visibility from Master_Tag as the gate.

### 3. Legacy/no-touch captures are not capacity tests

Some captures used:

```text
--legacy-no-touch-tags
--legacy-keep-tdma-state
--legacy-skip-link-ready-wait
--allow-legacy-tdma-show-only
--no-cleanup
```

Those are useful for preserving or observing a resident runtime state. They are
not clean capacity tests. Do not use them to claim what 3 tags or 6 tags can do.

For a clean test, reset/rebuild the session:

```text
cmd_all MODE IDLE
mode recv
tdma hold 1
tdma clear
tdma freq motion 10
tdma roster <only targets>
wait for target links
tdma hold 0
tdma rebalance
capture
cleanup
```

### 4. Current runtime state before restore was not trustworthy

Multiple recent captures showed uneven sweep counts and unstable resident
state. That makes the current state unsuitable for conclusions about hardware
capacity.

Example clean visible-3 test before freeze restore:

```text
Log:
  SS-TWR/alt-SS-TWR/broadcast/logs/clean_visible3_norfd_10hz_ge7_20260627_141608_20260627_141608/summary.json

Targets:
  BSF66F, BS9336, BSCCF4

RFD:
  0

tag_cir:
  off

TDMA:
  CFG verified, actual_hz=10.0 for each target

Result:
  overall ge7 = 0.774101
  overall ge8 = 0.245084
```

Per-tag:

```text
BSF66F: ge7=0.850523, ge8=0.002990
BS9336: ge7=0.683664, ge8=0.308782
BSCCF4: ge7=0.899160, ge8=0.509804
```

This is not a high-ge7 baseline and is not acceptable as recovery success.
However, this evidence is now superseded by the Critical Update above: the bad
result was explained by the Tag RX-window spacing/margin bug and older Tag
image mismatch, not by 3-tag capacity.

### 5. Wand-3 visibility was unstable

Wand tags:

```text
BSCCF4
BS9336
BS955A
```

At one point a gate saw all three, but a clean Wand-3 capture then failed link
readiness:

```text
Log:
  SS-TWR/alt-SS-TWR/broadcast/logs/clean_wand3_visible_norfd_10hz_ge7_20260627_141252_20260627_141252

Result:
  interrupted during link setup
  ready=BS9336,BSCCF4 (2/3)
  BS955A did not become stable-ready
```

Do not claim Wand-3 is currently stable until it passes a clean link gate and
capture.

## Important Logs / Evidence

High-ish 5-tag legacy/no-touch evidence before later chaos:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/stable_slots_mastertag_visible5_ref6roster_norfd_legacy_notouch_120s_20260627_005734_20260627_005734/summary.json
```

This had approximately:

```text
overall ge7 around 0.94
RFD=0
tag_cir=off
```

But it was not six-tag and used legacy/no-touch discipline. Treat it as useful
evidence that no-RFD can be healthy, not as final recovery proof.

Bad/insufficient clean visible-3 evidence:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/clean_visible3_norfd_10hz_ge7_20260627_141608_20260627_141608/summary.json
```

Superseded interpretation: this was a useful failure witness for the Tag
RX-window bug, not proof of a 3-tag capacity limit.

Earlier resident/no-touch 3-tag probe, not a clean capacity test:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/three_tag_online_norfd_10hz_ge7_20260627_135330_20260627_135330/summary.json
```

Do not use it as proof that 3-tag capacity is bad; it was a resident/no-touch
probe.

Long running narrative/log notes:

```text
docs/rf_diag_overnight_20260625.md
```

This file is useful but messy. It includes both correct conclusions and
corrected mistakes. Read carefully.

## Recommended Recovery Plan

### Phase 0 - Stop experimenting

Do not run RFD tests. Do not run more random 3-tag/6-tag captures from the
current mixed state.

### Phase 1 - Finish freeze restoration

Master_Tag is already flashed back.

Next restore Master_Anchor:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast

scripts/assert_b120_internal_osc_build.sh \
  /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/firmware_freeze/autopos_full_system_20260625_0245/builds/build-master-control-b120-m1-master-anchor-lfrc-cir-us-r800-20260624

B120_SNR=960148546 BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1 \
  scripts/flash_master_control_b120_m1_noninteractive.sh \
  /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/firmware_freeze/autopos_full_system_20260625_0245/builds/build-master-control-b120-m1-master-anchor-lfrc-cir-us-r800-20260624/zephyr/merged_domains.hex
```

Then restore generated OTA payload/manifests from the freeze snapshot or use
the freeze build's embedded payload through the flashed master image. Be
careful here: the current `apps/master_ota/generated/*` files are dirty and may
point at later RFD/A26 images.

Freeze snapshots:

```text
firmware_freeze/autopos_full_system_20260625_0245/generated/active_ota_payload.current.json
firmware_freeze/autopos_full_system_20260625_0245/generated/tag_ota_manifest.current.json
firmware_freeze/autopos_full_system_20260625_0245/generated/anchor_ota_manifest.current.json
```

Freeze per-master embedded payload manifests:

```text
firmware_freeze/autopos_full_system_20260625_0245/builds/build-master-control-b120-m1-master-tag-lfrc-tdma-explicit-nodiag-r800-20260624/active_ota_payload.json
firmware_freeze/autopos_full_system_20260625_0245/builds/build-master-control-b120-m1-master-anchor-lfrc-cir-us-r800-20260624/active_ota_payload.json
```

Deploy Tag freeze OTA via Master_Tag:

```bash
python3 scripts/ota_deploy_tag_set.py \
  --port /dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00 \
  --out-dir logs/restore_freeze_tag_nodiag_20260627 \
  --targets BSF66F,BS2DCE,BSDC91,BS9336,BS955A,BSCCF4 \
  --expected-fw-marker compact-sampled-tdmafix-nodiag-r800-20260624
```

Before running this, verify that the Master_Tag active payload is actually the
freeze Tag payload. If not, fix payload generation/selection first. Do not OTA
the wrong image.

Deploy Anchor freeze OTA via Master_Anchor:

```bash
python3 scripts/ota_deploy_anchor_set.py \
  --port /dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00 \
  --out-dir logs/restore_freeze_anchor_cir_us_r800_20260627 \
  --order ABCDEFGH \
  --max-attempts 2
```

Again, verify the active payload is the freeze anchor payload before deploying.

### Phase 2 - Verify firmware versions

After OTA restore, query versions. Required markers:

```text
Tags:
  compact-sampled-tdmafix-nodiag-r800-20260624

Anchors:
  anchor-cir-us-r800-20260624
```

Do not proceed to baseline testing until markers are confirmed for the devices
that are actually visible.

### Phase 3 - Minimal no-RFD baseline ladder

Do not jump directly to 6 tags.

Run clean no-RFD captures:

1. One known-good tag, 60-120 s.
2. Two tags, 120 s.
3. Three visible tags, 120 s.
4. Only if 3-tag is healthy, attempt six tags.

Clean test requirements:

```text
RFD=0
tag_cir=off
tr_diag_all=0
tdma_config_failed=false
TDMA CFG verified
target links ready before release
cleanup enabled unless there is a specific reason not to
```

Do not use legacy/no-touch for these clean baseline tests.

Suggested command template for 3 visible tags:

```bash
python3 scripts/run_recv_tdma_capture.py \
  --port /dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00 \
  --skip-anchor-preflight \
  --duration 120 \
  --targets <TAG1>,<TAG2>,<TAG3> \
  --tdma-roster-targets <TAG1>,<TAG2>,<TAG3> \
  --tr-hz 10 \
  --tag-cir off \
  --skip-target-cir-command \
  --tag-link-timeout-s 90 \
  --tag-link-stable-s 5 \
  --tdma-config-retries 3 \
  --known-bs-tags BSF66F,BS2DCE,BSDC91,BSCCF4,BS9336,BS955A \
  --no-silence-non-target-tags \
  --out-dir logs/clean_visible3_post_freeze_norfd_10hz_<STAMP> \
  --no-tr-timeout-s 20
```

For a clean capacity test, expected signs of health:

```text
per-tag sweeps should be roughly balanced
ge7 should be high, target at least ~0.90 before claiming recovery
rfd_all=0
tr_diag_all=0
```

If per-tag sweeps are badly imbalanced, do not call the test a clean capacity
result even if TDMA CFG says `actual_hz=10.0`.

## Things Not To Do

- Do not re-enable RFD.
- Do not interpret BLE listener dongle silence as proof connected tags are down.
  That dongle sees advertising (`BADV/BSTAT`), not connected NUS traffic.
- Do not count profile/static TDMA entries as live tag visibility.
- Do not use missing J-Link for a Tag as an OTA blocker.
- Do not claim 3-tag capacity failure from a resident/no-touch capture.
- Do not claim six-tag success from a five-tag capture.
- Do not continue from dirty generated OTA payload without verifying what image
  it points to.

## Current Status Summary

As of this handoff:

```text
Master_Tag:
  current post-fix path enumerates as:
  /dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00

Master_Anchor:
  no new status recorded in this handoff update

Tags:
  at least the tested visible-3 set has been brought to a corrected no-RFD
  timing configuration

Anchors:
  unchanged for the 3-tag fix; anchors were already using effective 1000 us
  flat response spacing

No-RFD baseline:
  3-tag 10 Hz high ge7/ge8 is now proven after the Tag RX-window timing fix
  latest reported clean visible-3 result:
    overall ge7 = 0.978
    overall ge8 = 0.967

RFD:
  should remain disabled
```

The next agent should start from the "Critical Update" section above. The old
failed 3-tag experiments are preserved as evidence of the failure mode, not as
the current state.
