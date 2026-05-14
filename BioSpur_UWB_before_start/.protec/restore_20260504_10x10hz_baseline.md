# 2026-05-04 10x10Hz Baseline Restore Notes

Purpose: restore/test the proven 2026-05-04 broadcast TDMA baseline where Tag
TR throughput reached full-speed 10 Hz per Tag, and Anchor 0..7 all appeared in
Tag-anchor ranging.

## Proven Good Evidence

Primary full-speed stable-gate run:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/tdma_10tag_after_powercycle_stablegate_motion60_20260504_130532
```

Result:

```text
TR actual: 48032 / 48000 over 60 s
All 10 Tags around 10.0 Hz
Anchor preflight: success, anchor role all responder, sent=8 ready=8/8
```

Useful full-sweep comparison run:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/tdma_10tag_motion180_tronly_b66_cmdall_20260504_113353
```

Recomputed from `tr_all.csv`:

```text
total sweeps: 6000
ge7: 3500 = 58.3%
ge8: 2334 = 38.9%
BSF66F anchors_seen: 0,1,2,3,4,5,6,7
BS2DCE anchors_seen: 0,1,2,3,4,5,6,7
BSDC91 anchors_seen: 0,1,2,3,4,5,6,7
```

Current failed state for comparison:

```text
autopos_pipeline/outdoor_20260512/tag_captures/verify_old3_b62_10x10hz_300s_clean_links_20260513_015110_20260513_015110
```

Current failure:

```text
3 Tags are 10.0 Hz, but ge7=0 and ge8=0.
BSF66F/BS2DCE/BSDC91 only see anchors 1,3,4,5,6,7.
Anchors 0 and 2 are completely absent from valid Tag TR.
```

Therefore, row-rate alone is not restore success. Restore success requires
Anchor 0 and 2 to reappear and ge7/ge8 to be comparable to 2026-05-04.

## Firmware / Build Identity

Tag firmware marker from the frozen 2026-05-04 baseline:

```text
alt-bcast-b62-otaprep-silent-g1200-r1000
```

Archived Tag image:

```text
/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start-reserve/build_archive_20260511_224100/repo/SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0
```

Expected Tag image timing:

```text
APP_ALT_SS_TWR_GUARD_US=1200
APP_ALT_SS_TWR_RESP_SPACING_US=1000
TDMA period=10 ms
TDMA active=9 ms
TDMA count=10
```

Archived Master_Tag carrier most directly matching the b62 baseline:

```text
/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start-reserve/build_archive_20260511_224100/repo/SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b62-otaprep-silent-carrier
```

Important caveat:

```text
The archived b62 carrier CMakeCache has APP_MASTER_TDMA_SLOT_PERIOD_MS=40
and APP_MASTER_TDMA_SLOT_ACTIVE_MS=24, but the successful 5/4 capture command
sets motion Hz at runtime through the script. The proven startup behavior is
the stable gate, not just the carrier default.
```

## Master_Anchor / Anchor Runtime Requirement

The 5/4 successful runs did not merely start Tags. They first forced all Anchors
to runtime responder and got an 8/8 ack.

Required preflight evidence from 5/4:

```text
anchor role all responder
anchor role all responder runtime sent=8 ready=8/8
anchor role all responder runtime repeat sent=8 ready=8/8
anchor role all responder runtime final sent=8 ready=8/8 total_sent=24
anchor role rc=0 target=all role=responder
```

5/4 Anchor UUID map used by preflight:

```text
A 4DC6B8187E33803AE8601FB0D7992B96
B B9179575C776C98F1CB132DD6EDC6223
C CEE5A7EFCB35F8A56B430047629F5309
D AB14CCA262A092E70EB26B0ACB0A394B
E A892AF05DD59CF0D0D3408AD74F364A1
F 840C68591E90019821AACFF1B73AAA34
G B3087BC3D87CCCD316AEDC6B71D6677F
H 1EABFBEC28B8053FBB0D5C448112AE93
```

If current preflight cannot reach 8/8, do not claim Tag restore success.
Power-cycle/fix Anchor/Master_Anchor runtime state first. Do not flash
Anchor/Master_Anchor unless explicitly requested.

## Exact 5/4 Successful Command Shape

From:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/tdma_10tag_after_powercycle_stablegate_motion60_20260504_130532/commands.json
```

Original command shape:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast

python3 scripts/run_recv_tdma_capture_with_listener.py \
  --listener-port /dev/serial/by-id/usb-SEGGER_J-Link_000760185886-if00 \
  --listener-extra-s 40 \
  --out-dir logs/tdma_10tag_motion60_$(date +%Y%m%d_%H%M%S) \
  -- \
  --port /dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00 \
  --controller-reset-snr - \
  --duration 60 \
  --targets BSF66F,BS2DCE,BSDC91,BSE88E,BS6F3A,BS8251,BSF8E0,BS1396,BS7724,BS10CE \
  --profiles BSF66F:motion,BS2DCE:motion,BSDC91:motion,BSE88E:motion,BS6F3A:motion,BS8251:motion,BSF8E0:motion,BS1396:motion,BS7724:motion,BS10CE:motion \
  --motion-hz 10 \
  --skip-cm-probe \
  --allow-zero-positions \
  --tag-link-stable-s 8 \
  --anchor-preflight-port /dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00 \
  --anchor-preflight-retries 2 \
  --anchor-preflight-launch-retries 2
```

For only the old three Tags tomorrow, use the same shape but targets:

```text
BSF66F,BS2DCE,BSDC91
```

and still keep:

```text
--tag-link-stable-s 8
--anchor-preflight-port /dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00
--anchor-preflight-retries 2
--anchor-preflight-launch-retries 2
```

## Tomorrow Pass/Fail Criteria

For 3 Tags x 10 Hz:

```text
Rows: about 720 rows / 30 s, 7200 rows / 300 s per Tag group after 8 anchors
Per Tag row rate: about 10 Hz
Required anchors_seen for BSF66F/BS2DCE/BSDC91: 0,1,2,3,4,5,6,7
Reject if anchors 0 or 2 are absent.
Reject if ge7/ge8 remain zero.
```

Do not accept a run merely because row-rate is 10 Hz.
