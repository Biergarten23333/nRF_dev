# Ultrasound Anchor-H Experiment Pipeline

This checklist is for the experimental HC-SR04 Z-offset image. It keeps the frozen Master_Anchor path as the rollback baseline and uses a separate experimental Master_Anchor carrier image for OTA deployment.

## 2026-05-18 Freeze: Validated Experimental State

This is the current validated snapshot. Keep this section as the handoff reference before making the next firmware change.

### Firmware / Payload State

The validated Anchor OTA image is:

```text
fw_marker: us-hc-exp2
anchor build: SS-TWR/alt-SS-TWR/broadcast/build-anchor-unified-ota-us-hc-exp2
Master_Anchor carrier build: SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-us-hc-exp2
Anchor signed bin sha256: ed58640580e4496aec4af16765861a8482ce572d40bd360f7d881a221347d9a1
Anchor dfu zip sha256: 21ab32c12509e9778619ebffc6ad5de49482ef7c84715a56b19128d5cffd3489
```

The experimental Master_Anchor carrier was flashed to:

```text
Master_Anchor SNR: 960148546
current Master_Anchor serial path:
/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00
```

The current H replacement identity is:

```text
Anchor H BS code: BS506D
Anchor H SNR: 760184500
Anchor H UUID: B1E487C2B1FD740D1442206A1857DFA1
```

The validated A-H UUID map is:

```text
A F3BB7A04104F9CB8561DDDACB9E53714
B B9179575C776C98F1CB132DD6EDC6223
C CEE5A7EFCB35F8A56B430047629F5309
D B2B5FA625534A8C617135DCAFC9E036A
E A892AF05DD59CF0D0D3408AD74F364A1
F 840C68591E90019821AACFF1B73AAA34
G B3087BC3D87CCCD316AEDC6B71D6677F
H B1E487C2B1FD740D1442206A1857DFA1
```

### Functional Meaning

The ultrasound feature is not a separate UWB Matrix or Responder mode. It is an Anchor-control feature inside the same unified Anchor firmware. The intended sequence is:

```text
AutoPos SW100 in Matrix mode
-> restore all anchors to runtime responder/control-ready
-> open H ultrasound window with USON 30
-> poll H with US?
-> close H ultrasound with USOFF
-> run normal Tag/Wand capture with anchors acting as responders
```

Ultrasound data are read through the Master_Anchor serial/NUS bridge, not through direct host BlueZ. The PC talks to Master_Anchor, Master_Anchor sends `USON`, `US?`, or `USOFF` to H over Anchor-control, and the response returns through the Master_Anchor serial port.

### Three-Cycle Regression Result

The three-cycle regression was run at:

```text
autopos_pipeline/offline_test_motice/test_18052026/us_triplet_exp2_retry3
finished_at: 2026-05-18T20:19:37
```

All three cycles passed:

```text
SW100 -> US30s -> BSF66F 120s motion
SW100 -> US30s -> BSF66F 120s motion
SW100 -> US30s -> BSF66F 120s motion
```

Ultrasound results:

| Cycle | H ultrasound result |
| --- | --- |
| 1 | 300/300 ok, timeout 0, median 895 mm, mean 901 mm |
| 2 | 300/300 ok, timeout 0, median 900 mm, mean 902 mm |
| 3 | 300/300 ok, timeout 0, median 895 mm, mean 901 mm |

BSF66F motion capture results:

| Cycle | TR rows | Valid rows | ge7 | ge8 |
| --- | ---: | ---: | ---: | ---: |
| 1 | 9608 | 8997 | 99.9% | 49.2% |
| 2 | 9600 | 9134 | 100.0% | 61.2% |
| 3 | 9608 | 8974 | 100.0% | 47.2% |

The motion raw logs were checked for `US;`, `USON`, `USOFF`, `DIST;`, and `ULTRASOUND`; no residual ultrasound output was found in the motion sessions.

### 3-Tag Wand Responder Smoke Test

After enabling three Wand Tags, the active Wand Tag IDs were:

```text
BSCCF4
BS955A
BS9336
```

The 60 s 3-Tag responder smoke test was run at:

```text
autopos_pipeline/offline_test_motice/test_18052026/wand3_us_image_responder_smoke_60s_wand_targets
```

Results:

| Tag | TR rows | Valid rows | ge7 | ge8 |
| --- | ---: | ---: | ---: | ---: |
| BSCCF4 | 4808 | 4604 | 99.7% | 66.4% |
| BS955A | 4800 | 4319 | 98.7% | 21.2% |
| BS9336 | 4800 | 4736 | 98.7% | 90.7% |
| Overall | 14408 | 13659 | 99.0% | 59.4% |

The Wand capture raw log was also checked for `US;`, `USON`, `USOFF`, `DIST;`, and `ULTRASOUND`; no residual ultrasound output was found.

Conclusion at this freeze point: the `us-hc-exp2` Anchor image preserves normal AutoPos Matrix sweep, normal single-Tag responder capture, and normal 3-Tag Wand responder capture. The ultrasound feature can be opened after AutoPos and closed before Tag/Wand capture without contaminating the capture stream.

## 0. Baseline Rule

Do not overwrite the frozen Anchor or frozen Master_Anchor artifacts. The experimental flow is:

1. Build an Anchor OTA image with `APP_ANCHOR_ULTRASOUND_ENABLE=1`.
2. Build an experimental B120 Master_Anchor carrier that embeds that Anchor OTA payload.
3. Flash only the experimental Master_Anchor carrier when ready.
4. OTA the ultrasound Anchor image to the anchors.
5. Run the three-cycle validation pipeline.
6. Roll back with the frozen Master_Anchor/frozen Anchor image if anything behaves strangely.

## 1. Build Experimental Carrier

From the broadcast directory:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast

scripts/build_experimental_ultrasound_anchor_carrier_b120.sh "$(date +%Y%m%d_%H%M%S)"
```

The script builds artifacts only. It does not flash. It also restores `apps/master_ota/generated/` back to the pre-build frozen state after the experimental Master_Anchor carrier is built.

The important output is printed at the end:

```text
experimental Master_Anchor carrier:
  .../build-master-control-b120-anchor-us-hcsr04-.../zephyr/merged_domains.hex
```

## 2. Flash Experimental Master_Anchor Carrier

Only do this after confirming the build output path. Use SNR `960148546` for `Master_Anchor`.

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast

scripts/assert_b120_internal_osc_build.sh build-master-control-b120-anchor-us-hcsr04-REPLACE_ME

BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1 \
B120_SNR=960148546 \
scripts/flash_master_control_b120_m1_noninteractive.sh \
  build-master-control-b120-anchor-us-hcsr04-REPLACE_ME/zephyr/merged_domains.hex
```

Do not use `nrfjprog`.

## 3. OTA Experimental Anchor Image

After the experimental Master_Anchor is running, use the existing anchor OTA deployment flow. The new H hardware identity is:

```text
Anchor H: BS506D, SNR 760184500, UUID B1E487C2B1FD740D1442206A1857DFA1
```

Before running the three-cycle test, verify that `US?` is supported on H through the Master_Anchor serial bridge. Direct host BlueZ access is not the validated path.

```bash
python3 - <<'PY'
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path("scripts").resolve()))
from run_autopos_ultrasound_motion_triplet import master_anchor_us_cmd

port = "/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00"
h_uuid = "B1E487C2B1FD740D1442206A1857DFA1"
log_dir = pathlib.Path("/tmp/h_us_probe")
rc, resp = master_anchor_us_cmd(port, h_uuid, "US?", log_dir, "us_probe")
print("rc=", rc)
print(resp)
raise SystemExit(rc)
PY
```

Expected after OTA:

```text
US;IDLE;...
```

If the response is `ERR:US_DISABLED` or `US;DISABLED`, the anchors are still on the frozen image or on a non-ultrasound build.

## 4. Three-Cycle Validation Pipeline

This script performs the required chain:

```text
SW100 -> US30 -> check output -> 120s BSF66F motion capture
SW100 -> US30 -> check output -> 120s BSF66F motion capture
SW100 -> US30 -> check output -> 120s BSF66F motion capture
```

It writes each ultrasound capture to `ultrasound_H.csv`, forces `USOFF` before motion capture, and checks that the post-off status is not running.

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
source .protec/biospur_ports.env

cd SS-TWR/alt-SS-TWR/broadcast

out="$PWD/logs/ultrasound_triplet_$(date +%Y%m%d_%H%M%S)"

python3 scripts/run_autopos_ultrasound_motion_triplet.py \
  --anchor-port "$BIOSPUR_ANCHOR_PORT" \
  --tag-port "$BIOSPUR_TAG_PORT" \
  --anchor-snr 960148546 \
  --tag-snr 1050070698 \
  --h-uuid B1E487C2B1FD740D1442206A1857DFA1 \
  --tag BSF66F \
  --cycles 3 \
  --sw-sets 100 \
  --us-duration-s 30 \
  --motion-duration-s 120 \
  --tr-hz 10 \
  --out-dir "$out" 2>&1 | tee "$out.console.log"
```

Expected final result:

```text
"success": true
```

For each cycle, check:

```bash
find "$out" -name ultrasound_H.csv -print -exec tail -n 5 {} \;
find "$out" -name summary.json -print
```

## 5. What The Pipeline Checks

The pipeline checks three things:

1. SW100 still works with the experimental Anchor image.
2. H can run a 30 s ultrasound capture and return `US;DONE`.
3. After `USOFF`, a normal 120 s BSF66F motion capture still passes, proving that the ultrasound thread is not contaminating or blocking normal responder response to Tag polls.

The ultrasound data are not printed into the motion capture serial stream. They are queried over BLE and stored separately as CSV.
