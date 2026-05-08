# Anchor A19 Common Rollout - 2026-05-08

## Common Anchor Image

Source tree:

```text
SS-TWR/alt-SS-TWR/broadcast
```

Firmware marker:

```text
a19-led-g1200-r1000
```

Anchor build:

```text
SS-TWR/alt-SS-TWR/broadcast/build-anchor-unified-ota-a19-led-g1200-r1000
```

Master_Anchor carrier build:

```text
SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-anchor-a19-led-g1200-r1000
```

Key build settings:

```text
APP_ALT_SS_TWR_ENABLE=1
APP_ALT_SS_TWR_BCAST_ENABLE=1
APP_ALT_SS_TWR_GUARD_US=1200
APP_ALT_SS_TWR_RESP_SPACING_US=1000
APP_UWB_HW_FRAME_FILTER_ENABLE=1
APP_ANCHOR_RESP_DELAY_UUS=1200
APP_ANCHOR_RESPONDER_BLUE_LED_ENABLE=1
```

B120 clock policy:

```text
CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y
CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC_CALIBRATION=y
CONFIG_CLOCK_CONTROL_NRF_K32SRC_XTAL is not set
CONFIG_CLOCK_CONTROL_NRF_K32SRC_SYNTH is not set
```

Evidence:

```text
Build / flash log root:
SS-TWR/alt-SS-TWR/broadcast/logs/anchor_a19_common_20260508_123300

Payload verify:
SS-TWR/alt-SS-TWR/broadcast/logs/anchor_a19_common_20260508_123300/verify_payload_before_flash.log

B120 LFRC assert:
SS-TWR/alt-SS-TWR/broadcast/logs/anchor_a19_common_20260508_123300/assert_b120_internal_osc.log

Master_Anchor flash:
SS-TWR/alt-SS-TWR/broadcast/logs/anchor_a19_common_20260508_123300/flash_master_anchor_960148546.log
```

## Master_Anchor Status

`Master_Anchor` SNR `960148546` was flashed with the A19 carrier image:

```text
SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-anchor-a19-led-g1200-r1000/zephyr/merged_domains.hex
```

The carrier embeds the A19 common Anchor OTA payload in `ota_image.inc`.

## D / H Replacement Anchors

| Anchor | J-Link SNR | UUID | BLE name | Runtime id | UWB short addr | A19 OTA result |
|---|---:|---|---|---:|---:|---|
| D | `760184974` | `B2B5FA625534A8C617135DCAFC9E036A` | `ANCHOR-D-BS20AC` | `3` | `0xA103` | Not completed: SMP gate timeout after DFU ready |
| H | `760184753` | `CF12E703AC1A118F6AB440AB05B0BA23` | `ANCHOR-H-BSB77F` | `7` | `0xA107` | Not completed: SMP gate timeout after DFU ready |

OTA test evidence:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/anchor_a19_common_20260508_123300/ota_DH_test_20260508_123806
```

D result:

```text
phase_a_ok=true
phase_b_ok=true
dfu_ready_seen=true
ota_success_seen=false
reason=ota_gate_failed_after_dfu_ready
blocker=request did not reach anchor BLE SMP transport
log=.../D_ANCHOR-D-BS20AC_retry1/single_shot.log
```

H result:

```text
phase_a_ok=true
phase_b_ok=true
dfu_ready_seen=true
ota_success_seen=false
reason=ota_gate_failed_after_dfu_ready
blocker=request did not reach anchor BLE SMP transport
log=.../H_ANCHOR-H-BSB77F/single_shot.log
```

Interpretation:

- `Master_Anchor` can scan and strict-UUID target both replacement anchors.
- BLE anchor-control connection is working for both D and H.
- OTA mode target restore is working for both UUIDs.
- BLE DFU/SMP service is discovered on both anchors.
- Upload does not start because the first MCUmgr image-state read times out.
- This is not yet a successful A19 OTA to D/H.

## Rollout Hold

Do not OTA the remaining six anchors until the D/H SMP gate issue is resolved
or the user explicitly accepts this failure mode and gives a new instruction.

Remaining anchors pending:

```text
A, B, C, E, F, G
```
