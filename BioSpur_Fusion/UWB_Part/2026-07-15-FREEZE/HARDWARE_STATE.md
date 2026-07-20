# HARDWARE_STATE — 2026-07-15-FREEZE rig

Real physical/runtime state of the fleet at freeze time. **Stable identity = SNR**;
`/dev/ttyACM*` ports are `/dev/serial/by-id`-derived and shift on replug — always
resolve by SNR (`ls -l /dev/serial/by-id/`). Firmware set + hashes: `firmware/README.md`.

> **⚠️ Live-state note (drifts):** at freeze the 3 wand tags are **software-silenced**
> (0 Hz, still connected + `MODE=RUN`) via the CFG epoch-slot trick — see FREEZE_STATE.md
> "REAL way to silence". `cmd_all REBOOT` over Master_Tag restores free-run ranging.
> Master_Anchor sits `mode=AUTOPOS pending=0` (idle). These are runtime states, not
> the frozen config.

---

## Anchors — 8, A–H (BLE-OTA'd, addressed by UUID/letter via Master_Anchor)

**Layer naming (do not infer height from a single name):** **ABCD = LOWER layer,
EFGH = UPPER layer.** Faces AEDH / BFCG. Any height label must reference a *same-layer*
anchor pair (an upper device is "between E–H", never "between A–D").

- Firmware: **markers MIXED but binaries byte-identical.** A/B/C = `anchor-freeze-clean-20260716`;
  D–H = `anchor-freeze-20260715`. Only the embedded marker string differs (batch6b added
  a compile-time `#error` guard, no emitted code).
- Anchors are **BLE-OTA'd from Master_Anchor** (`ota_deploy_anchor_set.py`), not
  JLink-flashed per unit, so per-anchor JLink SNRs are not operationally tracked here —
  identity is the A–H UUID. Physical: ABCD lower ring, EFGH upper ring.
- **Post-any-anchor-OTA:** MUST send `anchor role all responder` via Master_Anchor, else
  anchors non-responder → `valid_mask=0xf8` (5/8) → ge7=0.
- Anchor-OTA has a firmware limitation (warm-reboot can't reconnect; needs cold JLink
  reset) + a script fix — see FREEZE_STATE.md residual #2 and `experiments/anchor_ota_diagnosis/`.

## Wand tags — 3, shared power supply

| Board | On-air src | logical tag_id | notes |
|---|---|---|---|
| **BS9336** | `0xb136` | 54 | on-air addr = `0xb1` + last-2-hex of board name |
| **BS955A** | `0xb15a` | 90 | cleanest link; others phase-beat-collapse to ~1 Hz at 3 tags |
| **BSCCF4** | `0xb1f4` | 244 | |

- Firmware: **`tag-freeze-clean-20260716`** — DIAG default OFF → literal `TR;2` (no `;D1`/`;TP`);
  DIAG on → `TR;3`+`;D1` (runtime toggle only). BLE-OTA'd from Master_Tag (`ota_deploy_tag_set.py`).
- **All 3 on ONE shared power supply** — a power-cycle hits all three together; cutting it
  is the only *total* on-air silence.
- Verified freeze quality: ge7 0.979 / ge8 0.955 / valid% 97.6.

## Masters — 2× B120 (nRF5340)

| Role | SNR | Control CDC (**resolve by-id — port # is NOT stable**) | Boot profile | Flash |
|---|---|---|---|---|
| **Master_Tag** | `1050070698` | `by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00` | `tag` → RECV, holds `BS*` | JLink (`flash_b120_master_freeze.sh`) |
| **Master_Anchor** | `960148546` **PROTECTED** | `by-id/usb-Master_Anchor_Master_Anchor_Control_87EA2F4A526C5A02-if00` | `anchor` → AUTOPOS, rejects tags | JLink + `BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1` |

- **⚠️ Two CDCs per master — the console is the App CDC, NOT the J-Link VCOM.** Each B120 shows up
  twice in by-id: the **App CDC** = console (`*Master_Tag_Control*` / `*Master_Anchor_Control*`,
  serial = FICR DEVICEID) and the **J-Link OB VCOM** (`SEGGER_J-Link_<SNR>`). **Send console commands
  only to the App CDC; never open the J-Link VCOM — DTR can reset the master (→ drops tags).**
- **⚠️ ALWAYS resolve masters via `/dev/serial/by-id/…`, never a hardcoded `/dev/ttyACM<n>`.**
  A power cycle **renumbers** the CDC (2026-07-19: the App CDC was `ttyACM0` → became `ttyACM2`, and
  the J-Link VCOM took `ttyACM0` — so a hardcoded `ttyACM0` now hits the J-Link VCOM, opens but no
  response). Any `ttyACM<n>` written elsewhere in this doc is a point-in-time snapshot. Script port
  handling + which need explicit `--port`: `../docs/DEPLOYMENT.md` §8.2.
- **⚠️ Stuck master BLE recovery (nRF5340 dual-core):** if a master sees the tags' adv names but
  `conn=0/3` (or `scan_running=0`, no discovery) while the tags are fine (ranging, listeners hear
  them), the nRF5340 **NET core (BLE controller) is stuck. A J-Link reset only resets the APP core
  and does NOT recover it — do a FULL USB power cycle of the B120.** (Verified 2026-07-19.) Anchors
  are nRF52832 single-core and unaffected. Full detail: `../docs/DEPLOYMENT.md` §8.1.
- Both carry the **6a boot banner** (`=== MASTER BOOT: profile=… mode=… target=… wand tags: … ===`).
  Master_Anchor prints `wand tags: rejected`; Master_Tag `wand tags: WILL HOLD BS*`.
- B120 flash: JLink **`recover`** required (CTRL-AP mass-erase + APPROTECT), not plain `erase`.
- Master_Anchor is ALSO the anchor-OTA master (push anchor firmware from it); the 52840 dongle
  is a BLE sniffer, not an OTA device.

## Listeners — 9 (`listener-freeze-20260715`, MODE_LISTEN, CIR=1, id=255, CONFIG_BT=0, passive)

USB J-Link, full `recover` (PANS-cleared) 2026-07-15. **EXCLUDED / never flash: SNR
`760185886` = legacy Geiger air monitor (盖格) at `/dev/ttyACM6`, VCOM 460800 baud.**

| SNR | Port | Position | Height / layer ref |
|---|---|---|---|
| 760184753 | ttyACM17 | A–E anchor-pair midpoint | mid |
| 760184548 | ttyACM18 | B–F anchor-pair midpoint | mid |
| 760181725 | ttyACM20 | C–G anchor-pair midpoint | mid |
| 760184784 | ttyACM23 | D–H anchor-pair midpoint | mid |
| 760184964 | ttyACM19 | vertical-profile LOW | low |
| 760184767 | ttyACM16 | vertical-profile MID | mid |
| 760184545 | ttyACM21 | vertical-profile HIGH ~2.3 m | high |
| 760181879 | ttyACM22 | AEDH face, between **E–H, UPPER** | upper (EFGH) |
| 760186115 | ttyACM24 | BFCG face, between **B–C, LOWER** | lower (ABCD) — **BSF66F** repurposed retired tag, tag-active confirmed OFF |

- Listener VCOM baud = **460800** (not 115200). Passivity is structural (`MODE=LISTEN` has no
  TX path, `CONFIG_BT=0` no BLE). Full record: `SS-TWR/alt-SS-TWR/broadcast/experiments/listener_freeze_audit/LISTENER_FREEZE.md`.
- The Geiger overhears all on-air traffic and prints `UF;`/`UL;` records (why it's the on-air
  monitor used for the TDMA experiment in FREEZE_STATE.md).

## Serial safety

- **Never `cat` an nRF CDC** (DTR-resets the board). Use pyserial `dtr=False, rts=False` or
  `stty -hupcl`. Master_Tag control commands go to `/dev/ttyACM0` at 115200.
- Master_Anchor SNR `960148546` is `.protec`-guarded — flashing needs the explicit env override.
- Never JLink-flash the Geiger SNR `760185886`.
