# Wand / Old 3 Tag Status - 2026-05-09

## Old 3 Tag 10 Hz Baseline

Master_Tag was restored to the mature TR-only baseline image:

- Build: `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-tronly-no-cx-b65timing-noblediag-blepause-master40ms-20260508`
- SNR: `1050070698` (`Master_Tag`)
- LFRC assertion: passed before flash

Fresh 30 s recheck:

- Session: `SS-TWR/alt-SS-TWR/broadcast/logs/old3_10hz_recheck_after_master_restore_20260509_20260509_055333`
- Output mode: TR only. No TS/TF/CX rows.

| Tag | Unique sweeps | Valid sweeps | Rate over 30 s | Anchors per sweep |
|---|---:|---:|---:|---|
| BS2DCE | 300 | 300 | 10.000 Hz | A-H complete |
| BSDC91 | 300 | 300 | 10.000 Hz | A-H complete |
| BSF66F | 301 | 301 | 10.033 Hz | A-H complete |

This confirms the old 3 Tag baseline requirement: each old tag can produce at
least 10 Hz TR output.

## Wand OTA Recovery Probe

The Master_Tag recovery build was tested with explicit UUID-only matching for
the Wand device that lost its `Wand-X-BSxxxx` / `BSxxxx` name.

Observed Wand advertiser:

- Address: `D3:FD:93:FE:02:17`
- UUID: `FEF12FE64F06D3255B57884EE6AC7889`
- Advertising name: none
- BS token: none
- Advertised NUS: no
- Advertised DFU SMP: no

The recovery Master did successfully match the UUID and queue/connect the
device:

```text
SCAN hit ... uuid=FEF12FE64F06D3255B57884EE6AC7889 target=tag uuid_ok=1
CONNECT queue ... link=nus uuid=FEF12FE64F06D3255B57884EE6AC7889 target=tag
Connected ... D3:FD:93:FE:02:17 name=- bs=-
```

But GATT discovery failed:

```text
NUS service not found
NUS discovery retry exhausted
```

Conclusion: the visible Wand device can be found by UUID, but it does not expose
NUS or DFU SMP over BLE in its current state. Master_Tag cannot OTA it back
without a usable BLE service. Physical J-Link / direct recovery is required for
that Wand unless it later reboots into a service-bearing image.

## Wand Mode Contract

Wand Mode is a Master_Tag/script-side scheduling mode, not a new UWB ranging
protocol on the Wand tags.

- The Wand tags run normal Tag TR firmware.
- The Wand tags do not know PMODE.
- Master_Tag/script filters discovery to `Wand-*` devices and extracts the
  `BSxxxx` suffix for TDMA roster assignment.
- Ordinary `BSxxxx` tags are put into AOTA / quiet mode before Wand capture so
  they do not consume TDMA slots.
- Output remains TR-only. No TS/TF/CX output is required for Wand calibration.

Scheduling model:

- One full broadcast ranging sweep over 8 anchors is designed to fit inside a
  10 ms tag slot.
- With three Wand tags, the intended high-rate pattern is:
  `Wand-A 10ms -> Wand-B 10ms -> Wand-C 10ms -> repeat`.
- The theoretical per-Wand target is therefore about 33 Hz. The practical
  requirement is at least 10 Hz per Wand, with 30 Hz per Wand as the stress
  target.

## Wand v011 30 Hz Target Test

Unified Wand image:

- Tag OTA build:
  `SS-TWR/alt-SS-TWR/broadcast/build-wand-b65timing-multislot-v011-tag-ota-20260509`
- Firmware marker:
  `wand-b65timing-g1200-r1000-multislot-v011-20260509`
- Sign version used for the v011 recovery build: `0.0.11+1778325692`
- Master_Tag build carrying the v011 payload:
  `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-b65-master10ms-wandpayload-v011-prefixfix-20260509`

OTA status:

| Wand | BS code | OTA result | Post marker |
|---|---|---|---|
| Wand-A | BSCCF4 | success | `wand-b65timing-g1200-r1000-multislot-v011-20260509` |
| Wand-B | BS9336 | success | `wand-b65timing-g1200-r1000-multislot-v011-20260509` |
| Wand-C | BS955A | success | `wand-b65timing-g1200-r1000-multislot-v011-20260509` |

60 s capture:

- Session:
  `SS-TWR/alt-SS-TWR/broadcast/logs/wand3_b65_multislot_v011_motion30_20260509_20260509_133148`
- Command target profile:
  `Wand-A-BSCCF4:motion`, `Wand-B-BS9336:motion`, `Wand-C-BS955A:motion`
- Requested rate: `motion=30 Hz`
- Output mode: TR-only. `cm/cs/cr/cf/tf` rows all zero.

Measured unique-sweep rates:

| Wand | BS code | Unique sweeps | Valid unique sweeps | Span | Rate by span | Rate over 60 s | Status summary |
|---|---|---:|---:|---:|---:|---:|---|
| Wand-A | BSCCF4 | 757 | 615 | 59.525 s | 12.701 Hz | 12.617 Hz | O=3898, T=995, R=29 |
| Wand-B | BS9336 | 1725 | 9 | 59.660 s | 28.897 Hz | 28.750 Hz | O=10720, T=475, R=19 |
| Wand-C | BS955A | 657 | 641 | 59.675 s | 10.993 Hz | 10.950 Hz | O=4025, T=244, R=3 |

Conclusion:

- All three Wand tags are now above the 10 Hz minimum.
- Wand-B is close to the 30 Hz stress target.
- Wand-A and Wand-C meet the minimum but still need quality/timeout tuning if
  the goal is stable 30 Hz per Wand under the same placement.
- The previous Wand-B zero-output issue was fixed by forcing a higher semantic
  OTA sign version (`0.0.11`), so MCUBoot accepted and swapped the image.

## Wand 20 Hz Per-Tag Retest

To reduce BLE pressure, the three Wand tags were retested with a 20 Hz target
per tag, i.e. total requested Wand output of 60 Hz.

- Session:
  `SS-TWR/alt-SS-TWR/broadcast/logs/wand3_b65_multislot_v011_motion20_20260509_20260509_221910`
- Requested rate: `motion=20 Hz`
- Final TDMA CFG:
  - `BS9336 mask=0x0021`, 2 slots / 10, target 20 Hz
  - `BS955A mask=0x0084`, 2 slots / 10, target 20 Hz
  - `BSCCF4 mask=0x0108`, 2 slots / 10, target 20 Hz
- Output mode: TR-only. `cm/cs/cr/cf/tf` rows all zero.

Measured unique-sweep rates:

| Wand | BS code | Unique sweeps | Valid unique sweeps | Span | Rate by span | Rate over 60 s | Status summary |
|---|---|---:|---:|---:|---:|---:|---|
| Wand-A | BSCCF4 | 584 | 584 | 59.863 s | 9.739 Hz | 9.733 Hz | O=3710, T=86 |
| Wand-B | BS9336 | 585 | 585 | 59.480 s | 9.818 Hz | 9.750 Hz | O=3725, T=79 |
| Wand-C | BS955A | 604 | 604 | 59.888 s | 10.069 Hz | 10.067 Hz | O=3782, T=144 |

Conclusion:

- Reducing the requested total rate from 90 Hz to 60 Hz made the three Wand
  outputs very fair.
- However, the tags still produced about 10 Hz each even though the final TDMA
  CFG assigned 2 slots per tag for a 20 Hz target.
- This points away from pure BLE throughput as the only bottleneck and toward
  tag-side multi-slot mask execution / TR output pacing as the next item to
  inspect.

## Wand v012 / v013 TR Bundle Follow-Up

v012 changed the Tag-side BLE output path so `TR;` lines are eligible for the
BLE bundle queue. This is still normal Tag ranging firmware; the Wand-specific
part is the BLE name/marker plus Master_Tag/script filtering.

- v012 marker:
  `wand-b65timing-g1200-r1000-multislot-trbundle-v012-20260509`
- v012 Tag OTA build:
  `SS-TWR/alt-SS-TWR/broadcast/build-wand-b65timing-multislot-trbundle-v012-tag-ota-20260509`
- v012 Master_Tag payload build:
  `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-b65-master10ms-wandpayload-trbundle-v012-20260509`

v012 20 Hz target capture:

- Session:
  `SS-TWR/alt-SS-TWR/broadcast/logs/wand3_v012_trbundle_motion20_20260509_20260509_223339`
- Final TDMA CFG:
  - `BS9336 mask=0x0021`, 2 slots / 10, target 20 Hz
  - `BS955A mask=0x0084`, 2 slots / 10, target 20 Hz
  - `BSCCF4 mask=0x0108`, 2 slots / 10, target 20 Hz
- Output mode: TR-only. `cm/cs/cr/cf/tf` rows all zero.

Measured unique-sweep rates:

| Wand | BS code | Unique sweeps | Valid unique sweeps | Span | Rate by span | Valid rate | Status summary |
|---|---|---:|---:|---:|---:|---:|---|
| Wand-A | BSCCF4 | 1043 | 1043 | 59.401 s | 17.542 Hz | 17.542 Hz | O=4759, T=1812, R=211 |
| Wand-B | BS9336 | 1134 | 1103 | 59.938 s | 18.903 Hz | 18.386 Hz | O=7087, T=285 |
| Wand-C | BS955A | 1086 | 877 | 59.474 s | 18.243 Hz | 14.729 Hz | O=2861, T=3966, R=233 |

v013 experiment:

- v013 marker:
  `wand-b65timing-g1200-r1000-multislot-trbundle-v013-flush110-20260509`
- Only intended change from v012: increase
  `APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS` from 50 ms to 110 ms.
- OTA to all three Wand tags succeeded, then a 20 Hz target capture was run.
- Session:
  `SS-TWR/alt-SS-TWR/broadcast/logs/wand3_v013_trbundle_flush110_motion20_20260509_20260509_232023`

v013 measured unique-sweep rates:

| Wand | BS code | Unique sweeps | Valid unique sweeps | Span | Rate by span | Valid rate | Status summary |
|---|---|---:|---:|---:|---:|---:|---|
| Wand-A | BSCCF4 | 578 | 578 | 59.675 s | 9.669 Hz | 9.669 Hz | O=3681, T=77 |
| Wand-B | BS9336 | 1151 | 917 | 59.969 s | 19.176 Hz | 15.274 Hz | O=3510, T=3910, R=60 |
| Wand-C | BS955A | 905 | 905 | 59.757 s | 15.128 Hz | 15.128 Hz | O=4199, T=1664, R=19 |

Conclusion:

- v013 was worse than v012, especially for Wand-A. Increasing flush to 110 ms
  introduced extra queue/latency pressure instead of improving throughput.
- The system was restored to v012 after the failed v013 experiment:
  `SS-TWR/alt-SS-TWR/broadcast/logs/ota_wand_b65timing_multislot_trbundle_v012_restore_20260509`
- Post-restore VERSION matched v012 on all three Wand tags.
- Current usable Wand image is therefore v012, not v013.
- The remaining 18-19 Hz ceiling is not a simple "make BLE flush longer" issue.
  The next likely bottleneck is tag-side multi-slot execution / output pacing
  and UWB timeout behavior under 2-slot-per-cycle operation.
