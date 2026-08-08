# SWD scripts for BSF6C53

Every command that touches the target lives in a committed `.jlink` file here.
Nothing is improvised at the bench. `run_jlink.sh` is the only entry point.

## The two rules that are not negotiable

**1. Never invoke `JLinkExe` bare.** A bare invocation with more than one probe
attached opens a GUI probe-selection dialog. Every call goes through
`run_jlink.sh`, which always passes `-NoGui 1 -ExitOnError 1 -SelectEmuBySN`.

**2. No reset command, anywhere, except where a step deliberately needs one.**
`r`, `RSetType`, `ResetTarget` and `rx` are BANNED by a grep in `run_jlink.sh`
that refuses to run a script containing them. A reset destroys `.noinit`, the
trajectory ring and the wedge state, irreversibly, and on a wedged board that is
the entire evidence.

## Reset behaviour, stated rather than assumed

J-Link's `connect` does not itself reset a Cortex-M target, but the default can
be changed by a settings file, a script file, or a device-specific default. So
this directory pins it three ways:

1. no reset command in any script;
2. `jlink_settings.ini` sets `ConnectUnderReset = 0`, passed on every call;
3. **G2 measures it empirically** on a healthy board, three independent ways,
   before the probe is ever allowed near a wedged one.

(3) is the one that counts. (1) and (2) make it likely; only (3) makes it known.

## Scripts

| script | what it does | resets? | halts? | writes? |
|---|---|---|---|---|
| `id_target.jlink` | reads FICR `INFO.PART`, `INFO.VARIANT`, `INFO.RAM`, `INFO.FLASH` and `DEVICEID[0..1]` | no | no | no |
| `attach_noreset.jlink` | connect, halt, read `DEVICEID` while halted, go | no | **yes** | no |
| `dump_ram.jlink` | halt → registers → `savebin` 256 KiB RAM → go | no | **yes** | no |
| `dump_flash.jlink` | `savebin` 1 MiB flash | no | no | no |
| `flash_validation.jlink` | erase + program app and MCUboot, verify | **yes**, deliberately | yes | **yes** |

`flash_validation.jlink` is the only one that resets, and it is the only one
that may not be pointed at a wedged board.

## Device selection, and why `id_target` uses a generic core

BSF6C53 has **two** SWD contact sets: the NINA-B306's nRF52840 and the
DWM1001C's nRF52832. Naming `NRF52840_XXAA` on the wrong pads gets a device
mismatch instead of an answer, so `id_target.jlink` connects as a plain
`CORTEX-M4` — which both parts are — and lets FICR say which one it is.
Every other script names `NRF52840_XXAA` and will refuse the wrong pads,
which is the behaviour you want once the pads are known.

## The board-identity check nobody asked for and everybody wants

`INFO.PART` says which *chip*. It does not say which *board*, and the fleet has
ten of them. `id_target.jlink` also reads `FICR.DEVICEID[0..1]`, and
`decode_target_id.py` folds them with the firmware's own
`bsl_identity_from_ficr()` (`B306_Part/include/biospur_link.h:314-320`). The
result must be `0x6C53`. Same read, two extra words, and it turns "an nRF52840"
into "**this** nRF52840".
