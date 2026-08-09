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

## Reset behaviour — MEASURED 2026-08-08, not assumed

J-Link's `connect` does not itself reset a Cortex-M target, but the default can
be changed by a settings file, a script file, or a device-specific default. So
this directory pins it three ways:

1. no reset command in any script;
2. `jlink_settings.ini` sets `ConnectUnderReset = 0`, passed on every call;
3. **G2 measured it** on a healthy board before the probe went near a wedged one.

(3) is the one that counts, and it now has a number. Connect + halt + read +
`go` on BSF6C53 cost **155 ms**; across the session the node's own uptime
advanced **18 011 ms in 18 049 ms of wall clock**, `reset_reason` was unchanged,
and the BLE link did not even drop. See `SWD_BRINGUP_REPORT.md`.

## The residual risk is CONTACT, not configuration

Six attaches were made during G1–G4 and **two failed**, one of them mid-session
with the probe already held. A failed attach is what triggers the undisableable
connect-under-reset fallback, so on a wedged board bad contact *is* the hazard.

**`VTref` does not tell you contact is good — it read `3.300V` in both
failures.** The tell is `InitTarget` duration: 1.58–1.88 ms on every success,
104 ms on the failure. `g3_dump.sh` therefore runs the cheap read-only
`id_target` first and refuses the dump if it fails (exit 8). **Clamp the TC2030
for anything that matters; do not hand-hold it.**

## Scripts

| script | what it does | resets? | halts? | writes? | measured |
|---|---|---|---|---|---|
| `id_target.jlink` | reads FICR `INFO.PART`, `INFO.VARIANT`, `INFO.RAM`, `INFO.FLASH` and `DEVICEID[0..1]` | no | no | no | 0.14 s |
| `attach_noreset.jlink` | connect, halt, read `DEVICEID` while halted, go | no | **yes** | no | 0.16 s |
| `dump_ram.jlink` | halt → registers → `savebin` 256 KiB RAM → go | no | **yes** | no | **1.9 s** |
| `dump_flash.jlink` | `savebin` 1 MiB flash | no | no | no | 7.2 s |
| `verify_flash.jlink` | `savebin` the programmed region for an independent compare | no | no | no | 2.0 s |
| `flash_validation.jlink` | erase + program app and MCUboot, verify | **yes**, deliberately | yes | **yes** | 9.8 s |

`flash_validation.jlink` is the only one that resets, and it is the only one
that may not be pointed at a wedged board.

## Helpers

| tool | what it is for |
|---|---|
| `link_witness.py` | passive reader on the Fusion Master CDC. Turns G2's two operator-watched legs into a measurement by tracking the node's own `node_ms` and `reset_reason`. A reset restarts one and changes the other; a disconnect does neither. |
| `identify_flash_image.py` | names the image a flash dump actually contains, so a RAM dump is parsed against the ELF the board is **running**. Compares the code region and reports signature-TLV differences separately. |
| `bench_g1_g3.sh`, `bench_g3_g4.sh` | run the gates back to back so the probe is held once, with a hard stop at any failure. |

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
