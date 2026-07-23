# Remote unattended reset and preflight

These procedures replace the physical button-reset dependency. Never run them
during a capture. The B306 target is always the exact BLE identity `BSF3C79`,
and the only directly flashable probe is Fusion Master DK `683234364`.

## Installed B306 image

- Marker: `b306-remote-ready-v10`
- OTA file: `B306_Part/builds/b306-remote-ready-v10/firmware/zephyr/zephyr.signed.bin`
- OTA file SHA-256: `f374b14535feff76de40dcf49e4419845d0c9b51a7f8d588698e52b4088dd76c`
- First-flash-only `merged.hex` SHA-256:
  `ba3066d7b5e3e18127936d47fd7ef92e1e8e78d26e5d375f01cf97e945e7c28e`

The image extends free-running TIMER2 across natural 2^32-us boundaries,
reports the wrap count, and runs a 30-second hardware watchdog fed by the
one-second health/telemetry path. It also reports the retained reset reason.

## B306 OTA and proof

The maintained fast updater automatically uploads, schedules, resets,
reconnects, and reads MCUboot image state. Success is only
`OTA_STATE:post_verify_passed detail=reconnected_hash_active_confirmed` for the
embedded expected digest. Merely sending the reset command is not success.

DK updater artifact:

- `B306_Part/builds/dk-ota-remote-ready-v10/merged.hex`
- SHA-256: `d16dac405a51a320d673554fb64e31f2cf1d26e27c89eb266876059fede1fe36`

Flash it only with explicit selection:

```bash
PYTHONNOUSERSITE=1 \
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR=/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk \
/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/bin/python3 -m west \
  flash --skip-rebuild \
  -d B306_Part/builds/dk-ota-remote-ready-v10 \
  -r jlink --dev-id 683234364
```

For a later reboot-only preflight, use
`B306_Part/builds/dk-reset-preflight-remote-ready-v10/merged.hex` (SHA-256
`71921e5105815c5279686e8b3dc4c4c057796f377d97f3698936e2e4eb19f417`).
It first proves the current digest, sends the OS reset, reconnects, and proves
the same digest active and confirmed again.

Restore the capture Fusion Master afterward using the same explicit probe:

```bash
PYTHONNOUSERSITE=1 \
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR=/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk \
/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/bin/python3 -m west \
  flash --skip-rebuild \
  -d B306_Part/builds/dk-fusion-remote-ready-v10 \
  -r jlink --dev-id 683234364
```

The Fusion Master artifact SHA-256 is
`c1eba5b73f59baa3970e1e98955f13b6d1b58f06526420edbe5332c55d1fc65e`.
Fresh-boot acceptance requires `node_ms < 300000`, increasing
`watchdog_feeds`, the expected `reset_reason`, and no malformed telemetry.

## DWM1001C reset and proof

The DWM firmware already has a cold `REBOOT` command, so no second reset
protocol is introduced. After stopping all capture processes, run:

```bash
python3 UWB_Part/tools/remote_dwm_reboot_preflight.py \
  --target BS065F \
  --marker tag-fusion-link-v2-absdeadline3
```

The tool refuses transient tty names, opens the exact Master Tag by-id port
with DTR/RTS inactive, and requires the pre-reset marker. `REBOOTING` is the
preferred acknowledgement, but the notification can be lost when the tag
drops BLE for reset. In that case the tool passes only if it sees a new Master
Tag connection event and the same exact tag returns with the expected marker;
it never sends a second reboot merely because the acknowledgement was absent.
A TDMA schedule must be sent cleanly after this reset and before capture starts.
