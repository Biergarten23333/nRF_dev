# Anchor A18 Freeze - 2026-05-08

This record freezes the current known-good Broadcast Anchor image. Do not
modify or rebuild this image in-place. Any future logic change must use a new
explicit version marker.

## Frozen Image

Firmware marker:

```text
alt-bcast-a18-ledrole-g1200-r1000
```

Common Anchor image:

```text
SS-TWR/alt-SS-TWR/broadcast/build-anchor-unified-ota-alt-bcast-a18-ledrole-g1200-r1000/merged.hex
```

Master_Anchor B120 carrier image:

```text
SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-anchor-lfrc-alt-bcast-a18-ledrole-g1200-r1000-carrier/zephyr/merged_domains.hex
```

Active OTA payload lock:

```text
SS-TWR/alt-SS-TWR/broadcast/apps/master_ota/generated/active_ota_payload.json
```

## Build Properties

- Broadcast Alt SS-TWR enabled.
- `APP_ALT_SS_TWR_GUARD_US=1200`
- `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
- `APP_ANCHOR_RESP_DELAY_UUS=1200`
- `APP_UWB_HW_FRAME_FILTER_ENABLE=1`
- `APP_ANCHOR_RESPONDER_BLUE_LED_ENABLE=1`
- Responder role: DWM1001C blue LED stays on.

## Replacement Anchor UUIDs

| Anchor | BLE name | J-Link SNR | UUID | Runtime id | UWB short addr |
|---|---|---:|---|---:|---:|
| D | `ANCHOR-D-BS20AC` | `760184974` | `B2B5FA625534A8C617135DCAFC9E036A` | `3` | `0xA103` |
| H | `ANCHOR-H-BSB77F` | `760184753` | `CF12E703AC1A118F6AB440AB05B0BA23` | `7` | `0xA107` |

## 100-Set AutoPos Sweep Evidence

Log directory:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/autopos_anchor_sweep_100set_20260508_190915/
```

Result:

- All masters `A-H` completed `100/100` SW sets.
- No reconnect retry rounds.
- No slow switch rounds.
- Total elapsed time: `153.761 s`.
- This validates that the current A-H UUID mapping is usable for AutoPos,
  including replacement D/H.

Per-master summary:

| Master | SW sets | Min quality | Reconnect retry | Total round time |
|---|---:|---:|---|---:|
| A | 100/100 | 90 | no | 13.322 s |
| B | 100/100 | 100 | no | 13.622 s |
| C | 100/100 | 94 | no | 13.622 s |
| D | 100/100 | 94 | no | 13.622 s |
| E | 100/100 | 100 | no | 13.623 s |
| F | 100/100 | 92 | no | 13.823 s |
| G | 100/100 | 96 | no | 13.623 s |
| H | 100/100 | 95 | no | 13.623 s |

## Rollout Checklist

- Verify `Master_Anchor` B120 carrier uses internal LFRC before flash.
- Flash `Master_Anchor` SNR `960148546` with the carrier above.
- Use the active A18 OTA payload to OTA anchors `A-H`.
- Post-verify all anchors report marker `alt-bcast-a18-ledrole-g1200-r1000`.
