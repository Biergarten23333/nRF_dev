# Calibration Wand Mode

This mode is for the three CaliWand tags. It is currently a safe BLE
control-plane mode: it lets the Master_Tag identify the three wand nodes,
put them into a quiet wand state, and assign temporary roles before direct
Tag-to-Tag ranging is wired into the UWB runtime.

## Build Flag

Enable with:

```bash
APP_TAG_WAND_MODE_ENABLE=1
```

Optional naming:

```bash
APP_TAG_BLE_NAME_PREFIX=Wand
```

The build exposes `wand=1` in the `VERSION` response.

## BLE Commands

| Command | Response | Purpose |
|---|---|---|
| `WAND?` | `WAND_STATUS ...` | Read wand state, label, role, BS id, and direct-UWB support flag. |
| `WAND START <A|B|C>` | `WAND_OK ... STREAM=0` | Enter wand mode and silence normal BLE status streaming. |
| `WAND ROLE <IDLE|INIT|RESP>` | `WAND_OK ROLE=...` | Assign a transient role for the future internal sweep controller. |
| `WAND STOP` | `WAND_OK ... STREAM=1` | Leave wand mode and restore normal status streaming. |
| `WAND_SWEEP` | `WAND_UNSUPPORTED ...` | Reserved command; direct Tag-to-Tag UWB is not connected yet. |

If the build does not enable `APP_TAG_WAND_MODE_ENABLE`, all `WAND...`
commands return `WAND_DISABLED`.

## Current Limitation

`DIRECT_UWB=0` is intentional. The existing Tag app runs the normal SS-TWR
initiator loop continuously, and the existing responder loop is anchor-addressed.
Starting a second direct-ranging loop from BLE would race the same DW1000 radio.

The next firmware step is to add a radio-safe wand runtime hook:

1. Pause the normal Tag initiator loop while wand sweep is active.
2. Let a tag responder bind to its tag short address (`0xB1xx`), not an anchor
   address.
3. Let an initiator tag unicast polls to the other two wand tag short addresses.
4. Emit `WR;...` records for AB/AC/BC ranges and a summary block.

Until that radio hook exists, use this mode only to identify and prepare the
three Wand tags without disturbing normal TDMA/OTA behavior.
