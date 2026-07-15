# UWB Four-Piece Command Reference — Full Runtime Command Surface

**Date:** 2026-07-15
**Scope:** Every runtime command that a human or host script can send to the system — TAG, ANCHOR, MASTER (carrier console + OTA), LISTENER, and the HOST scripts that wrap them.
**Method:** Static, read-only source audit (six parallel enumerations + a master↔peer / host↔firmware cross-check). No build, no flash, no hardware.

**Path convention:** every `file:line` below is **relative to the broadcast tree root**
`/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast`.
Per the project rule, the `broadcast/` tree is the only live firmware tree; the repo-root `src/` is dead. Line numbers are a snapshot of this date and will drift with edits.

**Status legend:**

| status | meaning |
|---|---|
| **ACTIVE** | in current production use (or a documented manual operator verb) |
| **DEBUG** | runtime-debug / diagnostic tool (TXPWR, DIAG, CIR, ultrasound, MSTAT, probes) |
| **ORPHAN** | handler exists but nothing sends it, **or** a sender exists with no matching handler — flagged in §D |
| **REMOVED** | a command that existed before and whose remnants are still visible |

**Transport planes:** TAG = BLE Nordic UART Service (NUS RX). ANCHOR = BLE custom GATT control characteristic `…d3f4` **and** on-board UART console. MASTER = host USB-CDC/UART console (relays to peers over BLE). LISTENER = USB-CDC. OTA = BLE MCUmgr SMP-DFU + pre-OTA NUS. See §A for the flow map.

---

## 1. TAG

Single dispatcher: `ble_received()` in `apps/tag/src/uwb_tag_ble.c` (lines 1540–2061). `tag_app.c` / `main.c` contain **no** command parsing — NUS RX is the tag's only runtime command surface. Every verb below is also reachable manually through the master's `cmd <raw>` / `cmd_all <raw>` passthrough.

| command + exact syntax | handled by (file:line) | what it does | side effects / persistence | default/initial state | status |
|---|---|---|---|---|---|
| `PING` | uwb_tag_ble.c:1564 | Replies `PONG` (liveness). | none | — | ACTIVE |
| `STATUS` | uwb_tag_ble.c:1569 | Returns cached last status line or `NO_STATUS`. | read-only | empty until first stream | ACTIVE |
| `TDMA_STATUS` | uwb_tag_ble.c:1585 | Prints live TDMA slot/mask/source/period/gen. | read-only | — | **ORPHAN** (not in HELP; no sender; superseded by `MODE?`/`CFG_STATUS`) |
| `TXPWR <MAX\|M3\|M6\|M12\|POR>` | uwb_tag_ble.c:1604 → ss_twr_init.c:704 | Writes DW1000 `TX_POWER` reg (0x1E); replies `TXPWR_OK VAL=0x..`. | live radio reg only; **no NVS**; resets to POR on reboot; shifts link margin/miss-rate | HW POR power | DEBUG (sent as `cmd_all TXPWR ..`) |
| `DIAG?` | uwb_tag_ble.c:1621 | Reports RF-diag flag `STATE=ON\|OFF`. | read-only | STATE=OFF | DEBUG |
| `DIAG <ON\|OFF>` | uwb_tag_ble.c:1627 → ss_twr_init.c:693 | Toggles per-response RF-diag reads on the RX hot path. | `volatile bool`; no NVS; `ON` can regress ge7/ge8 yield | OFF | DEBUG |
| `CIR?` / `CIR_STATUS` | uwb_tag_ble.c:1645 | Prints CIR mode + compile caps. | read-only | MODE=OFF | DEBUG |
| `CIR <OFF\|COMPACT\|FULL>` (also `0\|1\|2`, `none\|compact\|feature\|full\|raw`, optional `cir=`; alias `TAG CIR <mode>`) | uwb_tag_ble.c:1650 (parser ss_twr_init.c:653) | Sets CIR capture mode; rejects `ERR:BUSY_OTA` if OTA active; clears pending cal/samples/bundle. | atomic runtime var; no NVS; FULL adds heavy per-range capture | OFF | DEBUG |
| `APOS <id> <x> <y> <z>` (decimal, id 0–7) | uwb_tag_ble.c:1688 → uwb_anchor_layout.c:129 | Writes one anchor pose into **RAM** layout. id>7 → `APOS_FAIL`. | RAM only until `APOS_COMMIT` | hardcoded 8-anchor default (uwb_anchor_layout.c:16) | ACTIVE (AutoPos) |
| `APOS_COMMIT` | uwb_tag_ble.c:1727 → uwb_anchor_layout.c:154 | Saves RAM layout to NVS. | **writes NVS** `anchor_layout/runtime`; survives reboot | — | ACTIVE (AutoPos) |
| `APOS_RESET` | uwb_tag_ble.c:1741 → uwb_anchor_layout.c:167 | Restores default layout **and** commits it to NVS. | **writes NVS**; survives reboot | — | ACTIVE (AutoPos) |
| `APOS_STATUS` | uwb_tag_ble.c:1755 | Dumps each pose + `SRC=SETTINGS\|DEFAULT`. | read-only | SRC=DEFAULT | ACTIVE |
| `VERSION` | uwb_tag_ble.c:1778 | Prints fw marker, BS identity, tag id, mode, CIR mode, caps. | read-only | — | ACTIVE |
| `CFG_STATUS` | uwb_tag_ble.c:1799 | Prints full runtime config. | read-only | — | ACTIVE |
| `MODE?` | uwb_tag_ble.c:1824 | Prints mode + TDMA slot/mask/source. | read-only | MODE=DYN(RUN) | ACTIVE |
| `MODE <val>` — val→RUN: {RUN,RANGE,TR,TR_ONLY,MOTION,DYN,DYNAMIC,SOLVE,TS,TS_ENABLE,DEBUG,DIAG,TX_TEST}; →IDLE: {IDLE,STOP,HALT} (case-insensitive, parser :765) | uwb_tag_ble.c:1842 | Sets positioning mode, applies policy, stores config, reconfigures live; rejects if OTA active. IDLE disables TDMA + ranging. | **writes NVS** `tag_ble/runtime_cfg`; survives reboot; **IDLE persists** (tag stays stopped until `MODE RUN`) | boot mode RUN/DYNAMIC | ACTIVE |
| `MMOT` (exact) | uwb_tag_ble.c:1842 (alias :1851) | Exact `MMOT` = `MODE RUN`, then MODE store/live path. | writes NVS (same as MODE) | — | **ORPHAN** (no sender anywhere; hidden `MODE RUN` alias — see §D) |
| `TDMA_SET <slot>` (0–255) | uwb_tag_ble.c:1896 → :867 | Stores slot override (source=SETTINGS) + applies live. | **writes NVS** `tag_ble/runtime_cfg`; survives reboot; wrong slot → ring collision | — | ACTIVE (manual override; master uses `CFG SLOT=` instead) |
| `CFG TAG=<id> SLOT=<n> COUNT=<n> PERIOD=<ms> ACTIVE=<ms> EPOCH=<ms>` + optional `MASK=<hex> ACTIVE_US=<us> GEN=<n> RUN=<0\|1> PMODE=<n>` (keys via strstr, any order; parser :929) | uwb_tag_ble.c:1937 | Master TDMA assignment: sets tag id + full slot schedule (source=MASTER), applies mode policy, stores + reconfigures live. `AMODE=` (sent by master) is ignored. Range-checked → `CFG_BAD`/`CFG_SAVE_FAIL`. | **writes NVS** `tag_ble/runtime_cfg` (epoch/gen/active_us **not** in record); survives reboot | — | ACTIVE (core master command) |
| `CFG_RUN` | uwb_tag_ble.c:1975 | Enables TDMA transmit live → `STATE=RUNNING`. | **live only, not persisted**; reverts on reboot | — | ACTIVE (HELP-documented; manual only) |
| `CFG_STOP` | uwb_tag_ble.c:1980 | Disables TDMA transmit live → `STATE=ARMED` (halts TX). | live only, not persisted | — | ACTIVE (HELP-documented; manual only) |
| `HELP` | uwb_tag_ble.c:1985 | Prints pipe-separated usage list. | read-only | — | ACTIVE |
| `OTA_STATUS` | uwb_tag_ble.c:1995 (stub :2045) | `OTA_STATE=NORMAL\|READY\|ACTIVE` (or `OTA_DISABLED`). | read-only | NORMAL | ACTIVE |
| `OTA_PREPARE` | uwb_tag_ble.c:2006 | Arms OTA: sets `ota_ready`+`ota_active`, purges TX queue, blocks streaming → `OTA_READY`. | RAM flags; blocks telemetry until cancel/reboot; DFU entry | disarmed | ACTIVE (from master_ota) |
| `OTA_BEGIN` | uwb_tag_ble.c:2020 | Requires armed; sets `ota_active`, purges TX → `OTA_BEGIN_OK` (else `OTA_NOT_ARMED`). | RAM flags; blocks telemetry | disarmed | ACTIVE (from master_ota) |
| `OTA_CANCEL` | uwb_tag_ble.c:2038 | Clears `ota_active`+`ota_ready` → `OTA_CANCELLED`. | RAM flags; restores streaming | — | ACTIVE (HELP-documented; manual abort) |
| `REBOOT` | uwb_tag_ble.c:2054 (:317) | Cold reboot in 150 ms (`SYS_REBOOT_COLD`) → `REBOOTING`. | full MCU reset; drops BLE/ranging/telemetry | — | ACTIVE (post-OTA / config) |
| *(any other string)* | uwb_tag_ble.c:2060 | Replies `UNKNOWN_CMD`. | none | — | — |

**Tag orphan-sender note:** host scripts emit `STREAM OFF` / `STREAM 0` / `STREAMON 0` and `MODE AOTA`, none of which the tag handles (they fall through to `UNKNOWN_CMD` / mode-parse reject). See §D.

---

## 2. ANCHOR

Two runtime text planes (plus binary SMP for OTA):
**BLE-GATT** = custom control characteristic `…d3f4`, parser `process_control_cmd_locked` in `anchor_ble_ctrl.c` (whole buffer force-uppercased first, :747-749).
**UART** = on-board console (`DT_CHOSEN(zephyr_console)`), parser `process_line` in `uart_role_switch.c` (also uppercased, :312-314; CR/LF- or 300 ms-idle-terminated).
Plane is noted in the command cell: **[BLE]**, **[UART]**, or **[BOTH]** (mirrored). Central reboot on BLE fires when the result text contains `REBOOT` and is not `ERR:` (anchor_ble_ctrl.c:757-761).

| command + exact syntax | handled by (file:line) | what it does | side effects / persistence | default/initial state | status |
|---|---|---|---|---|---|
| `HELP` **[BLE]** | anchor_ble_ctrl.c:466 | Returns command menu. | read-only | — | ACTIVE |
| `VERSION` **[BLE]** | anchor_ble_ctrl.c:489 | `ANCHOR_FW fw=… bs=… uuid=… label=… role=… cir=… caps=…`. | read-only | — | ACTIVE |
| `SYNC` **[BLE]** | anchor_ble_ctrl.c:514 | Copies active_cfg → pending_cfg (discard staged edits). | RAM pending | pending=active at boot | ACTIVE |
| `VALIDATE` **[BLE]** | anchor_ble_ctrl.c:521 → :283 | Recomputes CRC/magic on pending cfg, reports VALID/INVALID. | RAM pending | — | ACTIVE |
| `R <M\|X\|P\|U>` / `ROLE <…>` **[BLE]** | anchor_ble_ctrl.c:686 (role_parse :131) | **Stage** pending role (M=master,X=matrix,P=responder,U=unset). Not live, not persisted. | RAM pending | — | ACTIVE |
| `L <A-H\|U\|0>` / `LABEL <…>` **[BLE]** | anchor_ble_ctrl.c:699 (:155) | Stage pending anchor_id label. | RAM pending | — | ACTIVE |
| `G <n>` / `GEN <n>` **[BLE]** | anchor_ble_ctrl.c:712 | Stage pending generation counter. | RAM pending | — | ACTIVE |
| `PENDING ROLE\|LABEL\|GEN <…>` **[BLE]** | anchor_ble_ctrl.c:653 / :641 / :665 | Verbose forms of `R`/`L`/`G`. | RAM pending | — | ACTIVE |
| `COMMIT` / `APPLY` **[BLE]** | anchor_ble_ctrl.c:526 → :296 | Normalize role (MASTER→MATRIX), gen=active+1, **write NVS**, cold-reboot. | **writes flash** (anchor_config.c:230); **bumps generation**; reboots | — | ACTIVE |
| `REBOOT` **[BLE]** | anchor_ble_ctrl.c:531 → :333 | `OK REBOOT` → cold reboot (no flash write). | reboot | — | ACTIVE |
| `RESET AUTOPOS` **[BLE]** | anchor_ble_ctrl.c:610 → :339 | Force persisted role=MATRIX, gen+1, **write NVS**, reboot. | **writes flash**; **bumps gen**; reboots | — | ACTIVE |
| `RESET RESPONDER` **[BLE]** | anchor_ble_ctrl.c:614 → :339 | Force persisted role=RESPONDER, gen+1, **write NVS**, reboot. | **writes flash**; **bumps gen**; reboots | — | ACTIVE |
| `RUNTIME <MASTER\|MATRIX\|RESPONDER> [FORCE\|RESTART] [SWEEP <n>] [CIR=<mode>]` **[BLE]** | anchor_ble_ctrl.c:552 → :373 → runtime_control.c:41 | **Live** role switch (no reboot, no NVS): stops loop, main loop re-enters new role. | RAM atomics; **changes live role**; not persisted | current runtime role | ACTIVE |
| `RUNTIME … SWEEP <n>` (1..10000) **[BLE]** | anchor_ble_ctrl.c:565-581 | MASTER-only finite circular-SAR sweep; auto-returns to MATRIX after N. | RAM; rejects if role≠MASTER (`ERR:SWEEP_REQUIRES_MASTER`) | — | ACTIVE |
| `RUNTIME … FORCE` / `… RESTART` **[BLE]** | anchor_ble_ctrl.c:563 | Force restart even if role unchanged. | RAM | — | ACTIVE |
| `RUNTIME … CIR=<0\|OFF\|NONE\|COMPACT\|FEATURE\|1\|FULL\|RAW\|2>` or `… CIR <mode>` **[BLE]** | anchor_ble_ctrl.c:582-599 → cir_output.c:41 | Sets CIR output mode; FULL also silences most printk (`full_cir_quiet`). **Only reachable as a RUNTIME sub-arg** (see §D). | RAM atomic; not persisted; build-gated | OFF | DEBUG |
| `TXPWR <MAX\|M3\|M6\|M12\|POR>` **[BLE]** | anchor_ble_ctrl.c:536 → ss_twr_anchor_init.c:159 | Writes DW1000 `TX_POWER` reg. | radio reg only; not persisted; **overwritten to `0x25456585` on next radio reconfigure/role-switch** | POR until boot cfg applies MAX | DEBUG |
| `STOP` **[BLE]** | anchor_ble_ctrl.c:622 → runtime_control.c:10 | Requests stop; main loop restarts the *same* role. | RAM flag; brief ranging gap; not persisted | — | ACTIVE |
| `DFU` / `ENTER_DFU` / `OTA` / `ENTER_OTA` **[BLE]** | anchor_ble_ctrl.c:628 → runtime_control.c:25 | Parks anchor idle in `anchor_enter_dfu_mode` (BLE/SMP alive for upload) → `OK DFU_READY`. Does **not** enter bootloader. | RAM flag; halts ranging; not persisted | — | ACTIVE |
| `US` / `US?` / `ULTRASOUND` **[BOTH]** | BLE :471→:400; UART uart_role_switch.c:328→:265 | Reports ultrasound sensor status. | none | `US;DISABLED` (build gate off) | DEBUG |
| `USON [sec]` / `ULTRASOUND_ON` **[BOTH]** | BLE :477→:408; UART :333/:337→:273 | Start HC-SR04 ranging (default 30 s, max 120 s). | RAM+GPIO; `ERR:US_DISABLED` unless built with US | disabled (APP_ANCHOR_ULTRASOUND_ENABLE=0) | DEBUG |
| `USOFF` / `ULTRASOUND_OFF` **[BOTH]** | BLE :483; UART :341 | Stop ultrasound. | RAM/GPIO | — | DEBUG |
| `ROLE?` / `ROLE` **[UART]** | uart_role_switch.c:320 → :159 | Prints working role/label/valid. | none | — | ACTIVE |
| `STATUS` **[UART]** | uart_role_switch.c:324 → :178 | Prints identity line (ANCHOR_ID, ROLE, BS_CODE, UUID, MCU_UID, CONFIG_VALID). | none | — | ACTIVE |
| `M` / `MASTER` **[UART]** | uart_role_switch.c:346 → :197 | Stage working role — **normalized MASTER→MATRIX** (:206). | RAM working cfg; MASTER cannot be staged | — | ACTIVE (see §D) |
| `X` / `MATRIX` **[UART]** | uart_role_switch.c:350 → :197 | Stage working role = matrix. | RAM working cfg | — | ACTIVE |
| `P` / `RESPONDER` **[UART]** | uart_role_switch.c:354 → :197 | Stage working role = responder. | RAM working cfg | — | ACTIVE |
| `ROLE SET <MASTER\|MATRIX\|RESPONDER>` **[UART]** | uart_role_switch.c:374 → :197 | Verbose stage (same MASTER→MATRIX normalize). | RAM working cfg | — | ACTIVE |
| `ID <A-H>` **[UART]** | uart_role_switch.c:358 → :213 | Stage working anchor_id label. | RAM working cfg | — | ACTIVE |
| `ANCHOR SET <A-H>` **[UART]** | uart_role_switch.c:382 → :213 | Verbose form of `ID`. | RAM working cfg | — | ACTIVE |
| `S` / `SAVE` **[UART]** | uart_role_switch.c:366 → :229 | Normalize role, **write working cfg to NVS** (no reboot). Rejects if ranging active (`ERR:BUSY`). | **writes flash**; **does NOT bump generation** (persists gen as-is) | — | ACTIVE |
| `CONFIG SAVE` **[UART]** | uart_role_switch.c:390 → :229 | Verbose form of `SAVE`. | **writes flash**; no gen bump | — | ACTIVE |
| `RB` **[UART]** | uart_role_switch.c:370 → :258 | `OK` then cold reboot after 40 ms. | reboot | — | ACTIVE |
| `REBOOT` **[UART]** | uart_role_switch.c:394 → :258 | Verbose form of `RB`. | reboot | — | ACTIVE |
| *(unknown token)* **[BOTH]** | BLE :730 / UART :398 | Falls through to `ERR:BAD_CMD`. | none | — | — |

**Anchor SMP/OTA (binary, not a text verb):** firmware image upload is Zephyr SMP img-mgmt (group 1) over BLE. `anchor_mcumgr_diag.c` only *logs* SMP events (`ANCHOR_SMP_INGRESS`, `MCUMGR_IMG_EVT`) and tracks `g_anchor_ota_active` — it parses no commands (DEBUG/observability). The text `DFU`/`OTA` verb just parks the anchor so this upload path is clean.

---

## 3. MASTER

Master carrier = **`apps/master_control`** = `master_control/src/main.c` (console, verb table, persistence, boot) **+** `master/src/master_multi_app.c` (multi-connection forwarding engine) **+** `master_ota/src/main.c` (OTA, §3b). **`apps/master/src/master_app.c` is NOT linked by any build → ORPHAN dead code (see §D).**

Console line buffer (`control_uart_irq_handler` main.c:3152, `\n`/`\r`-terminated) → work queue → verb dispatcher **`control_handle_uart_command()` main.c:2337** (args via `sscanf`/`strncasecmp`, verb+args lowercased). All file:lines in this section are `apps/master_control/src/main.c` unless noted.

**Boot/default:** default `control_mode = CONTROL_MODE_RECV` (main.c:78); `master_app_run()` (master_multi_app.c:3174) **auto-starts BLE scan + auto-connect** at boot (:3220). RECV auto-connects `BS*` tags and TDMA-configures them on connect (no explicit "start ranging" verb). Boot mode can be forced by build-time `APP_MASTER_BOOT_PROFILE` (`anchor`→AUTOPOS, `tag`→RECV) at `control_apply_boot_profile()` main.c:435 — **this is the 2026-07-15 zombie driver (§B).**

| command + exact syntax | handled by (file:line) | what it does | side effects / persistence | default/initial state | status |
|---|---|---|---|---|---|
| `status` | main.c:2574 → :331 | Prints `mode=.. pending=..`. | none | — | ACTIVE |
| `mode recv` / `mode rx` | main.c:2804 | RECV. If already RECV → clean-session (:777); else warm-reboot into RECV. Anchor-build redirects to AUTOPOS. | disconnects links; stages `__noinit` mode cookie (:696) + OTA cookie; `sys_reboot(WARM)` :2273 | mode=RECV(0) | ACTIVE |
| `mode ota` | main.c:2792 | OTA (warm-reboot). If already OTA → `master_ota_initiate()` :2796. | disconnects all peers; warm reboot; noinit cookie | — | ACTIVE (danger) |
| `mode autopos` | main.c:2829 | AUTOPOS **in-place (no reboot)**; wildcard anchor scan; target=ANCHOR. | `control_save_mode()` :2836 (noinit cookie); resets autopos runtime; disconnects peers | — | ACTIVE (danger) |
| `scan` | main.c:2608 | Scan-only discovery (RECV/AUTOPOS only). | disconnect all; **stops auto-connect (ranging halts)** | — | ACTIVE |
| `conn` | main.c:2626 | Connect-and-start discovery (RECV/AUTOPOS only). | resumes auto-connect | — | ACTIVE |
| `reroll <BSxxxx>` | main.c:2579 | Disconnect ONE tag by BS code → reconnect with fresh BLE↔UWB slot phase (RECV only). | transient disconnect of that tag | — | ACTIVE |
| `initiate` | main.c:2643 → master_ota_initiate() :2650 | **OTA handoff**: start DFU on staged target (OTA mode only). | forwards to OTA subsystem (§3b) | — | ACTIVE (OTA) |
| `ota_reset` | main.c:2780 → master_ota_reset_target() :2787 | **OTA handoff**: SMP OS-reset to connected peer (OTA mode only). | reboots peer (§3b) | — | ACTIVE (OTA) |
| `ota show` / `ota version` | main.c:2655 → :352 | Print bundled anchor/tag FW markers + OTA target. **Console-local — never enters OTA module.** | read-only | — | ACTIVE |
| `cmd <raw>` | main.c:2397 → master_multi_app.c:3722 | **Forward raw string** to current runtime-target peer (tag NUS *or* anchor GATT by target kind). | forwards to peer | — | ACTIVE |
| `cmd_all <raw>` | main.c:2404 → master_multi_app.c:3806 | **Broadcast raw string** to ALL ready peers of current target kind. | forwards to every matching peer | — | ACTIVE (danger) |
| `oneshot <raw>` / `oneshot show` / `oneshot clear` | main.c:2465 → master_multi_app.c:3872/3917/3907 | Arm a NUS command sent now **and re-sent to each tag on every reconnect** until cleared. | RAM one-shot state; re-fires on reconnect | build `APP_MASTER_ONE_SHOT_CMD` (empty) | ACTIVE |
| `APOS <id> <x> <y> <z>` / `APOS_COMMIT` / `APOS_STATUS` / `APOS_RESET` (any line starting `APOS`) | main.c:2391 → master_send_command_now | **Forward raw APOS line** to current tag target. | forwards to tag NUS | — | ACTIVE |
| `APOS_TO <BSxxxx> APOS…` | main.c:2351 | Point target at one tag by name, forward the `APOS…` payload (:2386). | temp sets runtime target=TAG; forwards | — | ACTIVE |
| `tag cir <off\|compact\|full\|status>` / `tag cir all <…>` | main.c:2411 | Translate to `CIR OFF/COMPACT/FULL/CIR?` → one/all tags. | forwards to tag NUS | — | DEBUG |
| `tdma show` | main.c:2494 → master_tdma_print_status | Print scheduler state. | none | — | ACTIVE |
| `tdma rebalance` | main.c:2498 → master_tdma_rebalance_now | Recompute + re-emit `CFG` slots to tags. | forwards `CFG…` | — | ACTIVE |
| `tdma clear` | main.c:2503 → master_tdma_clear_profiles | Clear roster/profiles; re-emit CFG. | forwards CFG | — | ACTIVE |
| `tdma hold <0\|1>` | main.c:2508 | Freeze/allow slot rebalancing. | RAM sched | hold=0 | ACTIVE |
| `tdma auto <0\|1>` | main.c:2521 | Auto-add-all-ready vs explicit roster. | RAM sched | — | ACTIVE |
| `tdma profile <BSxxxx> motion` | main.c:2534 | Set per-tag TDMA profile; re-emit CFG. | forwards CFG | — | ACTIVE |
| `tdma roster <BSxxxx> motion` | main.c:2539 | Add tag to explicit roster; re-emit CFG. | forwards CFG | — | ACTIVE |
| `tdma freq motion <hz>` | main.c:2544 | Set profile update rate; re-emit CFG. | forwards CFG | — | ACTIVE |
| `anchor version <A..H\|UUID32\|all>` | main.c:2666 → :1287/:1331 | Send anchor GATT `VERSION`, read reply (blocked in OTA). | forwards to anchor GATT | — | ACTIVE |
| `anchor role <A..H\|UUID32\|all> <master\|matrix\|responder> [cir <0\|compact\|full>\|cir=…]` | main.c:2681 → :1541/:1568 | Change anchor role. master/matrix → config-commit path (`R MASTER`/`R MATRIX`+`VALIDATE`+`COMMIT`+`REBOOT`); responder + `all` → `RUNTIME … FORCE CIR=` (blocked in OTA). | forwards multiple anchor GATT cmds; **reboots anchor(s)** on commit path | cir default `0` | ACTIVE (danger) |
| `anchor reset <A..H\|UUID32\|all> <autopos\|responder>` | main.c:2755 → :1736/:1751 | Send anchor GATT `RESET AUTOPOS` (→matrix) or `RESET RESPONDER`; reconnect. | forwards to anchor GATT; reboots/reconnects anchor | — | ACTIVE (danger) |
| `autopos status` | main.c:2855 | Print autopos state/target/map. | none | state=idle | ACTIVE |
| `autopos detach` | main.c:2859 → :1852 | Drop sweep stream; may send `STOP` to active-master anchor; reset runtime. | forwards `STOP` (conditional) | — | ACTIVE |
| `autopos cir <0\|compact\|full>` | main.c:2866 | Set CIR mode used by later apply/role runtime cmds. | RAM `autopos_cir_mode` | 0 | DEBUG |
| `autopos map <A..H> <UUID32>` | main.c:2881 → :683 | Map anchor label→32-hex UUID. | **writes NVS** `master_ctrl/autopos_map_<a..h>` — **survives power loss** | empty | ACTIVE (persist) |
| `autopos map show` | main.c:2882 | Print label→UUID map. | none | — | ACTIVE |
| `autopos round <A..H> [sets]` | main.c:2929 → :674 | Stage a sweep round (which anchor master, optional finite `sets`). | **writes NVS** `master_ctrl/autopos_target` — survives power loss; clears result history | idx=-1, sets=0 | ACTIVE (persist) |
| `autopos apply` | main.c:2960 → :1912 | Run promote/demote fleet sync: `RUNTIME MASTER/MATRIX [SWEEP n] CIR=` to anchors (AUTOPOS only). | forwards many anchor GATT cmds; **changes whole fleet roles** | — | ACTIVE (danger) |
| `autopos result show` / `autopos result clear` | main.c:2916 | Dump/clear sweep result history. | RAM | — | ACTIVE |
| `ota_target show` | main.c:2976 → master_ota_target_print | Print OTA/runtime target filter. | none | — | ACTIVE |
| `ota_target token <id\|-1>` | main.c:2981 | Set OTA target token (-1=any). | RAM cfg + runtime; staged to noinit cookie on next mode switch | -1 (or build `APP_MASTER_OTA_TARGET_TOKEN_ID`) | ACTIVE |
| `ota_target name <BSxxxx\|->` | main.c:2999 | Set exact advertised-name match (`-`=clear). | RAM cfg + runtime | build `APP_MASTER_OTA_TARGET_NAME` | ACTIVE |
| `ota_target prefix <BS\|->` | main.c:3019 | Set name-prefix match. | RAM cfg + runtime | `BS` | ACTIVE |
| `ota_target uuid <32hex\|->` | main.c:3039 | Set authoritative 32-hex UUID hard gate. | RAM cfg + runtime | empty | ACTIVE |
| `device show` | main.c:3065 | Print device kind/caps + OTA target. | none | kind derived at boot | ACTIVE |
| `device kind anchor` | main.c:3070 | Switch system target to anchor; reset OTA defaults; disconnect links; (RECV) restart discovery. | disconnects links; clears OTA target cfg | — | ACTIVE (danger) |
| `device kind tag` | main.c:3092 | Switch to tag; disconnect peers; **preserve** OTA name/prefix/uuid; reset token. | disconnects peers; token→-1 | — | ACTIVE |
| *(unrecognized)* | main.c:3133 → :338 | Prints help. | none | — | ACTIVE |

**Physical buttons** (not console, `button_handler` main.c:2303): BTN1 toggle RECV/OTA, BTN2 force OTA, BTN3 SCAN, BTN4 CONN&START — same code paths as the console verbs.
**Not master-local verbs:** `TXPWR`, `DIAG`, `PING`, `STATUS`, `TX_RF` are peer payloads only, reachable via `cmd`/`cmd_all` passthrough. `MSTAT` is a master-side stats printk (master_multi_app.c:2209), not a command.

### 3b. MASTER — OTA (`apps/master_ota/src/main.c`)

Linked with `-Dmain=master_ota_run` (master_control/CMakeLists.txt:87); `main()` at master_ota/src/main.c:2775 runs as `master_ota_run()` when the console enters OTA mode (master_control/src/main.c:3352). The OTA module parses **no** console strings itself — it exposes a C API the console dispatches into. Over BLE it emits pre-OTA NUS strings + MCUmgr SMP-DFU ops. **There is no `ota begin` / `ota tag <id>` / `ota anchor <set>` / `ota abort` / `ota status` verb** — those don't exist (§E).

| operation + syntax | handled/issued by (file:line) | what it does | side effects / persistence | status |
|---|---|---|---|---|
| `ota_target show\|token\|name\|prefix\|uuid` | dispatch main.c:2976-3047; impl master_ota/src/main.c:598/535/546/557/568 | Peer-selection filter for the next OTA connect. | RAM only (mirrored to console cfg) | ACTIVE |
| `initiate` / `mode ota` (already-OTA) → `master_ota_initiate()` | master_ota/src/main.c:2581 | Arms a fresh attempt: reset state, **disconnect all peers**, restart passive scan, acquire session. | disconnects every peer; `ota_armed=1`; `-EBUSY` if already active; no peer reboot itself | ACTIVE |
| `ota_reset` → `master_ota_reset_target()` | master_ota/src/main.c:2636 | Manual **SMP OS reset** to connected verified peer. | **reboots peer**; ends session | ACTIVE |
| (mode-switch handoff) `master_ota_prepare_mode_switch()` | master_ota/src/main.c:2661 | Quiesce OTA before leaving OTA mode. | disarms; drops link; no peer reboot | ACTIVE |
| NUS `"OTA_PREPARE\n"` (tag only) | built/sent master_ota/src/main.c:959 (bt_nus_client_send :941) | Tell tag to prepare for OTA (quiesce ranging). | sent only if `expect_nus` & NUS found & upload enabled | ACTIVE (SENT-to-peer) |
| NUS `"OTA_BEGIN\n"` (tag only) | master_ota/src/main.c:967 (300 ms after PREPARE) | Tell tag to enter OTA/DFU state. | — | ACTIVE (SENT-to-peer) |
| SMP OS echo prime — grp `0x0000` cmd `0x00` op 2 `{"d":"ping"}` | master_ota/src/main.c:1844, issued :1870 | Warms SMP/ATT path (timeout non-fatal). | none on peer | ACTIVE |
| SMP IMG erase slot 1 — grp `0x0001` cmd `0x05` op 2 `{"slot":1}` | :1794, issued :1817 | Erase peer secondary slot before upload. | **erases peer secondary slot**; EBADSTATE→recovery reset | ACTIVE |
| SMP IMG upload — grp `0x0001` cmd `0x01` op 2, CBOR `{image,data,off[,len,sha]}` | build :1555, loop :1596, issued :1684 | Stream baked-in `tag_ota_image[]` in 448 B chunks to slot 1. | **writes peer secondary slot**; 3× retry | ACTIVE |
| SMP IMG state READ — grp `0x0001` cmd `0x00` op 0 `{}` | :1821, issued :1840 | DFU-ready gate probe + post-upload verify. | read-only on peer | ACTIVE |
| SMP IMG state WRITE (set pending/test) — grp `0x0001` cmd `0x00` op 2 `{"hash":<h>,"confirm":false}` | :1740, issued :1766 | Mark uploaded image **pending/test** (swap on next boot, not permanent). | **swaps slot on next peer reboot** | ACTIVE |
| SMP OS reset — grp `0x0000` cmd `0x05` op 2 `{}` | :1770, issued :1790 | Reboot peer to trigger test-image swap. | **reboots peer**; ends session | ACTIVE |

**OTA sequence** (`ota_thread_fn` master_ota/src/main.c:1953): (1) NUS `OTA_PREPARE`→300 ms→`OTA_BEGIN` (tag only, :959/:967) → (2) upload-gate IMG-STATE READ loop (:1908) → (3) OS-echo prime (:2006) → (4) IMG erase slot 1 (:2021) → (5) IMG upload (:2054) → (6) IMG-STATE READ verify (:2064) → (7) IMG-STATE WRITE pending/test (:2070) → (8) OS reset (:2076). If `APP_MASTER_OTA_UPLOAD_ENABLE=0`, module runs monitor-only (no PREPARE/BEGIN/upload/reset).

---

## 4. LISTENER

The **UWB CIR listener is NOT receive-only** — it exposes a 4-command runtime interface over USB-CDC. Other probes are diagnostic. All command parsing is newline-terminated ASCII, exact-match.

| command + exact syntax | handled by (file:line) | what it does | side effects / persistence | default/initial state | status |
|---|---|---|---|---|---|
| `MODE_TAG\n` (USB-CDC) | UWB_listener/src/main.c:962 → :949 | Turn passive listener into an **active** Alt-SS-TWR tag: re-address to `APP_LISTENER_TAG_ADDR` (0xB1C0), 10 Hz poll all 8 anchors, emit `LTAG;src=…`. | radio TX active (RF emitted); persists until MODE change/reboot; not saved | boot = MODE_LISTEN (never TAG) | ACTIVE |
| `MODE_LISTEN\n` | UWB_listener/src/main.c:964 → :936 | Restore passive RX-only capture (addr 0xB1FE, filter off, clear ring, re-arm RX, reset watchdog). | radio RX-only; clears record ring | **boot default** | ACTIVE |
| `MODE_IDLE\n` | UWB_listener/src/main.c:966 → :920 | Radio fully off (`dwt_forcetrxoff`) → emits no RF (AutoPos-safe). | radio off; LED off | not idle at boot | ACTIVE |
| `MODE_QUERY\n` | UWB_listener/src/main.c:968 | Print `MODE=<TAG\|IDLE\|LISTEN> src=0xC000`. | none | — | ACTIVE |
| *(RX plumbing)* UART ISR → 128 B ring → `listener_poll_commands` assembles lines → `listener_apply_command`; unknown/partial/over-length (>31 ch) silently dropped | ISR :979; parser :1003; dispatch :960 | Command surface plumbing for the 4 MODE_* verbs. | over-length sets `cmd_overflow`, drops line | RX enabled at boot if `cmd_uart` ready (:1052) | ACTIVE |
| `apps/ble_listener`: **1200-baud CDC "touch"** (USB line-control event, not data) | apps/ble_listener/src/main.c:628-632 → :572 | Host setting CDC baud=1200 → cold reboot into Nordic Open Bootloader (`GPREGRET=0xB1`). | reboots into DFU; GPREGRET survives soft reset | active BLE scanner otherwise | ACTIVE |
| `apps/ble_listener`: no byte-data parser | apps/ble_listener/src/main.c (loop :624-635) | Otherwise receive-only: streams `BADV;…`/`BSTAT;…` out CDC; never reads inbound bytes. | none | streams continuously | ACTIVE (stream) |
| `apps/b120_ble_probe`: `\n` | apps/b120_ble_probe/src/main.c:50-53 | Liveness ping → `[BLE_PROBE] alive`. | none | — | DEBUG |
| `apps/b120_cdc_probe`: `status\n` / `ota version\n` / `echo <text>\n` | apps/b120_cdc_probe/src/main.c:42-49 | Bring-up probe: status ok / fw string / echo. Other lines → `unknown cmd`. | none | prints READY banner at boot | DEBUG |

**Build-time switches (NOT runtime commands)** — all `#ifndef #define` in `UWB_listener/src/main.c`, compile-time only: `APP_LISTENER_CIR_CAPTURE_ENABLE` (:42, default 0; fleet reflashed to 1), `APP_LISTENER_CIR_SAMPLE_PERIOD` (:46), `APP_LISTENER_POLL_DIAG_ENABLE` (:31), `APP_LISTENER_RESP_DIAG_ENABLE` (:38), `APP_LISTENER_STATUS_PRINT_ENABLE` (:58), `APP_LISTENER_ID` (:23), `APP_LISTENER_NEAR_ANCHOR_ID` (:27), `APP_LISTENER_TAG_ADDR` (:96), `APP_LISTENER_TAG_LABEL` (:99). The **460800 vs 115200 baud** setting is a `prj.conf`/Kconfig console-UART option, not in `main.c`.
**Sibling top-level tree** `/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/UWB_listener/src/main.c` (Geiger MODE_SCAN probe) has a different 3-command set (`MODE_SCAN`/`MODE_GEIGER`/`MODE_QUERY`) + a hardware-button toggle — not the live broadcast tree, not inventoried here.

---

## 5. HOST SCRIPTS

Command-sending scripts only (paths relative to broadcast root). "Master CDC" = `/dev/serial/by-id/usb-*BioSpur_BLE_Control_*`. The master relays `cmd`/`cmd_all`/`ota_target`/`APOS`/`tdma`/`autopos`/`anchor role` to peers over BLE; `mode`/`device`/`conn`/`scan`/`status` are master-local. See §5-emit for the exact per-string cross-check table.

| script + how invoked | entry/emit site (file:line) | what it emits (exact) | target | side effects | status |
|---|---|---|---|---|---|
| `ble_anchor_control.py --ble-addr/--device-uuid [--set-role/--set-label/--set-generation/--cmd/--validate/--commit/--reboot/--sync]` | write_cmd :102-111; sites :168-206 | `REBOOT`,`SYNC`,`R <ROLE>`,`L <LABEL>`,`G <gen>`,`VALIDATE`,`COMMIT`, raw (`US?`/`USON n`/`USOFF`) → GATT `…d3f4` | anchor BLE GATT (direct) | **persists** role/label/gen on COMMIT; reboots | ACTIVE |
| `serial_switch_role.py --port <anchorUART> [--role/--anchor-id/--save/--reboot/--boot-window-reboot]` | send_cmd_expect :95; :112,177 | `STATUS`,`ROLE?`,`ROLE SET <r>`,`ANCHOR SET <id>`,`CONFIG SAVE`,`REBOOT`,`RB`; boot-window `M`/`X`/`P`,`ID <id>`,`S`,`RB` | anchor UART (direct) | **persists** role/id on CONFIG SAVE; reboots; may JLink-reset | ACTIVE |
| `provision_anchor.py --probe-serial <7xxx> --anchor-id --role […]` | flash write :211; JLink :63,77 | packs `anchor_config_t` (magic 0xB105F00D) → flashes `0x0007E000` via `reset_then_flash.sh`; no serial verbs | anchor (JLink flash) | **flashes** config page; resets | ACTIVE (flash) |
| `read_config.py --probe-serial <snr>` | JLink SaveBin | reads `anchor_config_t`, prints JSON | anchor (JLink read) | none | ACTIVE |
| `capture_master_ble_session.py <snr> <port>` | read :292; reset :236 | reads master TagSummary+CM notifications → CSV; **no verbs** | master CDC (read) | nrfjprog reset; captures | ACTIVE |
| `capture_tag_session.py <snr> <port>` | reset :231; read :254 | reads tag USB stream → CSV; no verbs | tag USB (read) | nrfjprog reset; captures | ACTIVE |
| `capture_uwb_listener.py --port --duration` | open :52; read :160 | passive UL capture → CSV; **read-only** | listener (read) | captures | ACTIVE |
| `capture_uwb_poll_listener.py --port --baud 460800 --duration` | open :150; read :336 | poll-listener LPD/LRD/LCIR capture; **read-only** | listener (read) | captures | ACTIVE |
| `ota_single_shot_stable.py --port <MasterAnchor> --target-uuid <hex>` | send_cmd :265-270; sites :333-1227 | `conn`,`status`,`mode recv`,`device kind anchor`,`device show`,`ota_target uuid <hex>`,`ota_target show`,`cmd DFU`,`mode ota`,`initiate`,`ota_reset` | master CDC → anchor | **OTA reflashes** target anchor | ACTIVE |
| `ota_single_tag_stable.py --port <MasterTag> --target-name BSxxxx` | send_cmd; sites :165-719 | `status`,`ota_reset`,`mode recv`,`device kind tag`,`ota_target prefix/name <..>`,`ota_target show`,`device show`,`conn`,`cmd MODE IDLE`,`mode ota`,`initiate` | master CDC → tag | **OTA reflashes** target tag | ACTIVE |
| `ota_deploy_anchor_set.py --port <52840> --out-dir [--order ABCDEFGH]` | send_serial_command :338; subprocess :691,778 | `status`,`mode recv`,`device kind anchor`,`mode autopos`,`anchor version all`; per-anchor subprocess `ota_single_shot_stable.py`; verify guards; JLink/nrfutil resets | master CDC → anchors A–H | **OTA reflashes all anchors**; role changes | ACTIVE |
| `ota_deploy_tag_set.py --port <B120> --out-dir [--prefix BS]` | send_serial_command; subprocess | fleet tag-OTA; per-tag subprocess `ota_single_tag_stable.py` | master CDC → tags | **OTA reflashes tags** | ACTIVE |
| `push_apos_layout_verified.py --port <MasterTag> --targets BSxxx,… --out-dir` | send_tag_command_expect :230; emit :197-346 | `mode recv`,`device kind tag`,`ota_target token -1/prefix BS/uuid -`,`conn`, per-anchor `APOS_TO <tag> APOS <id> <x> <y> <z>`, `APOS_TO <tag> APOS_COMMIT`, `APOS_TO <tag> APOS_STATUS` | master CDC → tags | **persists** anchor layout to tag on APOS_COMMIT | ACTIVE |
| `verify_all_anchor_responder_runtime.py --port <MasterAnchor>` | send_cmd_collect_text; :164-509 | `status`,`device show`,`device kind anchor`,`mode autopos`,`mode recv`,`autopos map <L> <uuid>`,`conn`,**`anchor role all responder`** | master CDC → anchors | runtime role→responder (not persisted) | ACTIVE |
| `restore_and_smoke_test_anchor_responder.py [--anchor-port/--tag-port/--targets]` | subprocess :136-177 | runs `verify_all_anchor_responder_runtime.py` then `run_recv_tdma_capture.py` | (delegates) | role change + capture | ACTIVE |
| `run_autopos_round.py --port <52840> --master <A..H> --out-dir` | send_cmd :51-64; :80-110 | `status`,`mode autopos`,`autopos map <L> <uuid>`(×8),`autopos round <master>`,`autopos status`,**`autopos apply`** | master CDC → anchors | **`autopos apply` commits** solved layout | ACTIVE |
| `run_autopos_sweep_loop.py --port <52840> [--order/--sw-sets/…]` | send_cmd_collect(_text); write_cmd :778,785; emits :1042-3086 | `mode recv/autopos`,`autopos map/cir/round/status`,`anchor role <L> matrix`,`anchor role all matrix/responder [cir …]`,`device kind tag`,`ota_target …`,`conn`,`scan`, tag-quiet (`cmd MODE IDLE`,`cmd STREAM OFF/0`,`cmd STREAMON 0`) | master CDC → anchors & tags | drives whole array; runtime role/CIR | ACTIVE |
| `run_recv_tdma_capture.py --port <MasterTag> --targets --tr-hz --duration [--controller-reset-snr]` | send_cmd(_collect); emits :1655-3063 | `device kind tag`,`device show`,`ota_target …`,`conn`,`mode recv`,`cmd_all CIR OFF/COMPACT/FULL`,`cmd_all MODE IDLE/AOTA`,`tdma hold/clear/freq/roster/rebalance/show`,`reroll <BS>` | master CDC → tags | sets TDMA/CIR; **`cmd_all MODE AOTA/IDLE` stops all tag ranging** at cleanup | ACTIVE |
| `run_recv_tdma_capture_with_listener.py` / `…_with_poll_listener.py` | subprocess :60,68 | wrap `capture_uwb_(poll_)listener.py` + `run_recv_tdma_capture.py` | (delegates) | capture + listener | ACTIVE |
| `run_recv_tdma_reject_overnight.py` | subprocess :21 | overnight loop over `run_recv_tdma_capture.py` | (delegates) | repeated captures | ACTIVE |
| `run_dual_master_tdma_capture.py --anchor-port --tag-port` | :64-66; subprocess :213,261 | direct **`anchor role all responder cir 0`** to anchor CDC; subprocesses verify + capture | Master_Anchor CDC + delegates | anchor role→responder; capture | ACTIVE |
| `run_autopos_ultrasound_motion_triplet.py --anchor-port [--cycles]` | send_serial_command :163,168; subprocess :302 | `ota_target uuid <uuid>`,`conn`,`cmd <US?\|USON n\|USOFF>`; subprocesses sweep + capture | anchor CDC + delegates | ultrasound trigger + captures | ACTIVE |
| `run_responder_profile_overnight.py` | build/flash :102-122; :133 | `build_*` + `flash_master_control_*` + `flash_all_anchors.sh`, then **`REBOOT`** to each anchor CDC | anchor CDC + flash | **builds & flashes master + all anchors**; reboots | ACTIVE |
| `run_overnight_v3_multitag_loop.py` | subprocess :136,172,190 | `scan_and_map.py` + `run_recv_tdma_capture.py` + analysis | (delegates) | repeated captures | ACTIVE |
| `run_ground_truth_point.py` | subprocess :35,49 | subprocesses `capture_tag_session.py` | (delegates) | captures | ACTIVE |
| `quarantine_tags.py --port <master> --tags BSxxx,…` | imports `run_autopos_sweep_loop.quarantine_tag_for_sweep` :32,49 | `mode recv`,`scan`,`status`,`device kind tag`,`ota_target …`,`conn`,`cmd MODE IDLE`,`cmd STREAM OFF`,`cmd STREAM 0`,`cmd STREAMON 0` | master CDC → tags | runtime tag idle/stream-off | ACTIVE |
| `loop_test_ota_targeting.py --port <master>` | s.write :188-324; subprocess :81 | `device kind anchor`,`mode recv`,`mode ota`,`status` (repeated) | master CDC | enters OTA mode repeatedly | DEBUG |
| `loop_test_link.py` | JLink :84-90; subprocess :383-397; read :428 | JLink-resets, switches anchor roles via `serial_switch_role.py`, reads master sweep | anchor UART (subproc) + master read | anchor role switches + resets | DEBUG |
| `evaluate_motion_tag_profile.py` / `…_dual_tag_profile.py` | subprocess :172-310 | orchestrate `capture_master_ble_session.py` + `capture_tag_session.py` | (delegates) | captures | ACTIVE |
| `resolve_anchor_diag_port.py` | JLink reset :49; read :63 | identify anchor diag port by SNR; no verbs | anchor (JLink+read) | JLink reset | ACTIVE |
| `reset_then_read_serial.py --port [--snr]` | nrfjprog reset; read :116 | reset board, read serial; no verbs | any board (read) | nrfjprog reset | ACTIVE |
| `cir_live_view.py` / `cir_notch_detector.py --port <L-B>` | serial.Serial :50/:143 | live L-B CIR viewer / occlusion scoring; **read-only** | listener (read) | none | ACTIVE |
| `uwb_live_viewer.py` / `tail_live_serials.py` | serial.Serial :117/:22 | live USB-tag / BLE-TagSummary / tag serial viewers; **read-only** | tag/master (read) | none | ACTIVE |
| `scan_and_map.py [--timeout-s --json]` | BleakScanner.discover :72 | BLE advertisement scan → anchor identity map; **read-only** | BLE scan (read) | none | ACTIVE |
| `experiments/run_overnight_power.py` (env-driven) | set_txpwr :41; check_links :59; subprocess :71 | **`cmd_all TXPWR <preset>`**,`conn` to Master_Tag; drives `run_recv_tdma_capture.py` per cell | master CDC → tags | runtime tag TX-power change; captures | ACTIVE |
| `experiments/overnight_power_preflight.py` | :19,30,37 | `conn`,`cmd_all TXPWR <name>`,**`cmd_all TXPWR MAX`** (restore) | master CDC → tags | restores TX power=MAX | ACTIVE |
| `experiments/run_listeners_poll_7.py` (env LSN_DUR/LSN_OUT) | subprocess :58 | launches 7× `capture_uwb_poll_listener.py`, supervises; **read-only** | listeners (read) | captures | ACTIVE |
| `run_anchor_responder_then_tag_cm.py` | :13-18 | prints "deprecated…" and returns 2 | none | none | **REMOVED** (dead stub) |

**Offline (send nothing to hardware):** ~63 analysis/solver/prep scripts under `scripts/` process logs / BLE-scan JSON only — all `autopos_*` (compare/eval/dump/extract/generate/offline/split), `solve_anchor_layout*`, `fuse_bidirectional_matrix_v{1,2,3}`, `analyze_*`, `plot_*`, `evaluate_ref115_*`, `optimize_*`, `prepare_autopos_*`, `prepare_alt_ota_payload`, `cir_features_to_pair_weights`, `cir_mech_discriminators`, `recalibrate_anchor_layout_with_ref115`, `replay_tr_4anchor_subsets`, `sdp_init_v3`, `generate_ground_truth_points`, `generate_inter_anchor_matrix`, `gen_ota_image_inc`, `assert_active_ota_payload`, `verify_ota_payload_kind`, `parse_recv_tdma_raw`, `extract_cm_from_recv_raw`, `summarize_anchor_layout_result`, `anchor_dp_decompose/timeresolve`, `tag_roster`, `soak_link_census`, `check_ota_blackbox`, `join_range_diag_listener`, `delayed_diag_shift`, `write_build_source`, `run_autopos_solve_*_from_existing`, `run_cir_weighted_layout_compare`, `run_joint_resp_delay_profile`, `run_autopos_sweep_then_tag_cm_loop`. Two are helper libs: `master_control_port.py` (port-finder) and `anchor_probe_guard.py`.
**Flash/provisioning wrappers (not runtime commands):** `flash_all_anchors.sh`, `flash_anchor_auto.sh`, `flash_b120_master_freeze.sh`, `flash_master_control_b120_m1_noninteractive.sh`, `flash_master_noninteractive.sh`, `flash_uwb_listener_jlink.sh`, `jlink_flash_hex_by_snr.sh`, `jlink_flash_nrf5340_dualcore_by_snr.sh`, `jlink_reset_by_snr.sh`, `jlink_show_emulators.sh`, `reset_then_flash.sh`.

---

## §A. Command Flow Map

```
HOST SCRIPT / OPERATOR
  │
  ├─(A) Master USB-CDC console  ──► control_handle_uart_command()  master_control/src/main.c:2337
  │        │
  │        ├─ MASTER-LOCAL (handled on the carrier, never leaves it):
  │        │     status, scan, conn, reroll, mode {recv|ota|autopos}, device {show|kind},
  │        │     tdma {show|rebalance|clear|hold|auto|profile|roster|freq},
  │        │     autopos {status|cir|map|round|apply|result|detach},
  │        │     ota {show|version}, ota_target {…}, initiate, ota_reset
  │        │
  │        ├─ FORWARDED → TAG   (BLE NUS, bt_nus_client_send  master_multi_app.c:3784/3854/2609/1364):
  │        │     CFG TAG=…  (built :1338/:1352, auto on connect + every tdma verb)
  │        │     CIR OFF|COMPACT|FULL|CIR?   (from `tag cir …`)
  │        │     APOS / APOS_COMMIT / APOS_STATUS / APOS_RESET   (from APOS / APOS_TO)
  │        │     one-shot string (re-sent every reconnect)
  │        │     arbitrary raw  (from `cmd` / `cmd_all`, when target kind = TAG)
  │        │
  │        ├─ FORWARDED → ANCHOR (BLE GATT ctrl char …d3f4,
  │        │                       bt_gatt_write_without_response  master_multi_app.c:3771/3841/3679):
  │        │     VERSION, R MASTER, R MATRIX, VALIDATE, COMMIT, REBOOT, STOP,
  │        │     RESET AUTOPOS, RESET RESPONDER,
  │        │     RUNTIME {MASTER|MATRIX|RESPONDER} [FORCE|SWEEP n] CIR=…
  │        │     arbitrary raw  (from `cmd` / `cmd_all`, when target kind = ANCHOR)
  │        │
  │        └─ OTA (master_ota_run, master_ota/src/main.c):
  │              NUS OTA_PREPARE → OTA_BEGIN (tag only)  then  SMP-DFU (tag & anchor)
  │
  ├─(B) Anchor on-board UART console  ──► process_line()  uart_role_switch.c:312   [bypasses master]
  │        ROLE?, STATUS, M/X/P, MASTER/MATRIX/RESPONDER, ROLE SET, ID, ANCHOR SET,
  │        SAVE/CONFIG SAVE, RB/REBOOT, US?/USON/USOFF
  │
  ├─(C) Anchor BLE GATT direct  ──► process_control_cmd_locked()  anchor_ble_ctrl.c   [ble_anchor_control.py]
  │        R/L/G, VALIDATE, COMMIT, SYNC, REBOOT, US?/USON/USOFF, and every §2 [BLE] verb
  │
  └─(D) Listener USB-CDC direct  ──► listener_apply_command()  UWB_listener/src/main.c:960
           MODE_TAG / MODE_LISTEN / MODE_IDLE / MODE_QUERY
```

**Master-local vs forwarded, summary:** `mode/device/scan/conn/reroll/tdma*/autopos-console/status/ota*` are master-local; anything the master *emits as a peer string* (§3 rows with "forwards…", and the §5-emit table) is forwarded. The tag has **no** UART/console command path; the anchor and listener can be driven **without** the master via paths (B)/(C)/(D).

---

## §B. Persistence Table (flash / NVS writes)

Every command that survives a reboot, its key, where it is restored at boot, and the zombie-state risk. **Reference incident: 2026-07-15 Master_Anchor booted with persisted AUTOPOS/role state** — the rows marked ⚠ are the mechanisms behind it.

| piece | key / store | written by (command) | boot-restore (file:line) | survives power loss? | bumps gen? | zombie risk |
|---|---|---|---|---|---|---|
| TAG | `tag_ble/runtime_cfg` (Zephyr settings, uwb_tag_ble.c:121) | `CFG`, `MODE`, `TDMA_SET` (via :706) | settings_load :451 → handler :325 → apply :609 | **yes** | n/a | `MODE IDLE` persists → tag stays stopped until `MODE RUN`; wrong slot persists a ring collision. (epoch/gen/active_us NOT persisted → build defaults) |
| TAG | `anchor_layout/runtime` (settings, uwb_anchor_layout.c:11) | `APOS_COMMIT`, `APOS_RESET` (via :154) | uwb_anchor_layout.c:96 → handler :46 (from tag_app.c:417) | **yes** | n/a | bad pose values corrupt on-tag geometry across reboots |
| ANCHOR | flash config `anchor_config_t` @ `0x0007E000`, magic `0xB105F00D` (anchor_config.c:12) | `COMMIT`/`APPLY`, `RESET AUTOPOS`, `RESET RESPONDER` (BLE); `SAVE`/`CONFIG SAVE` (UART) | anchor_config_load → anchor_app.c:461, role select :469-488 | **yes** | COMMIT/RESET **yes** (+1); SAVE **no** | ⚠ **persisted RESPONDER role boots responder every time — no auto-normalize** (only MASTER→MATRIX is auto-fixed, anchor_app.c:219-255). Cure = `RESET AUTOPOS`. UART `SAVE` persisting a stale gen can fool freshness checks. |
| MASTER | `master_ctrl/autopos_target` (NVS, settings_save_one main.c:679) | `autopos round` | control_settings_set main.c:610-620 (settings_load :3251) | ⚠ **yes** | n/a | ⚠ stale sweep target survives full power cycle |
| MASTER | `master_ctrl/autopos_map_<a..h>` (NVS, settings_save_one main.c:692) | `autopos map` | control_settings_set main.c:622-638 | ⚠ **yes** | n/a | ⚠ stale label→UUID map survives full power cycle; **no verb clears it except overwrite** |
| MASTER | `master_ctrl/mode` (NVS key) | exported by control_settings_export main.c:649 — **but no `settings_save()` commits it** | control_settings_set main.c:599-607 | **no** (never written → stays default RECV) | n/a | low (never persisted) |
| MASTER | `control_boot_mode` / `control_boot_cookie` (`__noinit` RAM) | `control_save_mode()` main.c:696 on every `mode` switch (:2269,:2809,:2836) | control_load_mode main.c:3213-3223 | **survives WARM reboot only** | n/a | ⚠ a master last in AUTOPOS re-enters AUTOPOS after any warm/mode-switch reboot |
| MASTER | `ota_target_boot_*` / `ota_nus_boot_*` (`__noinit` RAM) | `control_stage_ota_target()` main.c:703 pre-warm-reboot | main.c:3289-3307 | warm reboot only | n/a | medium (stale OTA target after warm reboot) |
| MASTER | **build-time** `APP_MASTER_BOOT_PROFILE` | compile define (`anchor`/`tag`) | control_apply_boot_profile main.c:435 | n/a (baked in image) | n/a | ⚠ **`"anchor"` forces `CONTROL_MODE_AUTOPOS` on every boot — the primary 2026-07-15 driver** |

**2026-07-15 incident, root cause:** a "Master_Anchor" image is built with `APP_MASTER_BOOT_PROFILE="anchor"`, so `control_apply_boot_profile()` forces AUTOPOS on **every** boot — the observed "persisted AUTOPOS." It is compounded by the two power-persistent NVS keys (`autopos_target`, `autopos_map_*`) that make it resume with a **stale** round target/map the moment anchors connect, and by the `__noinit` warm-reboot mode cookie. There is no console verb that clears `autopos_target`/`autopos_map_*` other than overwriting them. On the anchor side, the analogous zombie is a **persisted RESPONDER role** (cure: `RESET AUTOPOS`); MASTER role can never persist on an anchor (always normalized to MATRIX).

---

## §C. Danger List — do NOT send at the wrong time

Ranked by blast radius. Each line: what it breaks / when NOT to use.

**Fleet-wide / deployment-altering:**
- `ota_deploy_anchor_set.py` / `ota_deploy_tag_set.py` / `ota_single_*` and master `mode ota` / `initiate` — trigger BLE DFU that **reflashes tag/anchor app firmware**; `initiate` first disconnects **every** peer (master_ota/src/main.c:2613). Never during a capture; a bad image can brick a target; IMG-erase (:1817) wipes the peer's secondary slot before new bytes land.
- `run_responder_profile_overnight.py` — **builds and flashes the master + all 8 anchors**, then reboots them. Whole-fleet outage.
- master `autopos apply` (and `run_autopos_round.py`) — reconfigures the **entire anchor fleet** (promote/demote master↔matrix↔responder), can reboot anchors. AUTOPOS calibration window only.
- master `anchor role …` / `anchor reset …` — sends `COMMIT`+`REBOOT` / `RESET` to anchors → the anchor drops out and reboots. Never mid-capture.
- `verify_all_anchor_responder_runtime.py` / `run_dual_master_tdma_capture.py` (`anchor role all responder cir 0`) / `run_autopos_sweep_loop.py` (`anchor role all matrix`) — **runtime role flip of all anchors**; mid-capture this collapses the responder set.

**Anchor-persistent (silent until re-provision):**
- `ble_anchor_control.py --commit` / `serial_switch_role.py --save` — **persist** anchor role/label/gen/id to NVS. A wrong `RESET RESPONDER` / saved responder role zombie-boots (§B). `provision_anchor.py` + flash wrappers write flash directly.
- anchor `COMMIT`/`APPLY`, `REBOOT`, `RB`, `RESET AUTOPOS`, `RESET RESPONDER` — cold-reboot the node; it disappears for the boot interval.

**Tag ranging/telemetry:**
- `cmd_all MODE AOTA` / `cmd_all MODE IDLE` (`run_recv_tdma_capture.py` cleanup) — **stops all tag ranging**; an aborted run can leave tags idle. (`AOTA` is not even understood by the tag — see §D; only `IDLE` actually stops it.)
- tag `MODE IDLE` / `CFG_STOP` — halt ranging; `IDLE` **persists** (silent through reboots until `MODE RUN`); `CFG_STOP` is live-only (easy to forget, reverts on reboot).
- tag `REBOOT` — cold-reset in 150 ms; drops BLE + in-flight capture.
- tag/anchor `OTA_PREPARE`/`OTA_BEGIN` / `DFU` — purge TX queue, block telemetry / park the node idle; a stray one silently removes it from ranging until cancel/reboot/role-switch.
- tag/anchor `CFG …` / `TDMA_SET <slot>` — live-reassign slot/mask/id; a duplicate slot collides the ring and corrupts other tags' slots.
- master `scan` — disconnects all peers and stops auto-connect → ranging halts until `conn`. `reroll <BS>` — transient one-tag disconnect.
- master `device kind anchor|tag` — disconnects links and rewrites the OTA/runtime target; switching kind mid-session drops the wrong links.
- master `oneshot <raw>` — the armed string is **silently re-sent to every tag on every reconnect** until `oneshot clear`; a forgotten one-shot keeps reconfiguring tags.
- master `cmd_all <raw>` — one wrong verb hits every tag or every anchor at once.
- master `tdma clear`/`profile`/`freq`/`rebalance` — re-emit live `CFG` to tags; mis-set values collide slots.

**Diagnostic load / bias (production hygiene):**
- `TXPWR <preset>` (tag or anchor, or `cmd_all TXPWR …`) — mutates live TX power; breaks SS-TWR common-mode symmetry (a non-obvious range-bias source) and shifts miss-rate. Anchor value is silently reverted to `0x25456585` on the next role-switch/radio reconfigure.
- `DIAG ON` (tag) — RF-diag reads on the RX hot path; documented ge7/ge8 yield regression.
- `CIR FULL` (tag or anchor `RUNTIME … CIR=FULL`, or `autopos cir full`) — heavy per-range CIR streaming that disturbs timing/throughput; on the anchor it also silences most diagnostic printk (`full_cir_quiet`), hiding other faults during a capture.
- `loop_test_ota_targeting.py` — repeatedly forces `mode ota`; leaves the master in OTA mode between iterations.

---

## §D. Cross-Check — orphans & mismatches (FLAGGED)

Every command the master/host can send was matched to a peer handler, and vice-versa.

**✅ Clean:** all master→anchor GATT strings (`VERSION`, `R MASTER/MATRIX`, `VALIDATE`, `COMMIT`, `REBOOT`, `STOP`, `RESET AUTOPOS/RESPONDER`, `RUNTIME … CIR=/SWEEP/FORCE`) have anchor handlers (§2). All master→tag NUS strings (`CFG`, `CIR`, `APOS*`, `MODE`, `TXPWR`, `DIAG`, `OTA_PREPARE`, `OTA_BEGIN`, `REBOOT`) have tag handlers (§1). All anchor-UART / anchor-GATT-direct / listener-CDC strings emitted by host scripts have handlers.

**⚠ ORPHAN — sender exists, NO handler:**
| string | emitted by | why it's orphaned |
|---|---|---|
| `STREAM OFF` / `STREAM 0` / `STREAMON 0` | `run_autopos_sweep_loop.py` (quarantine), `quarantine_tags.py` (`cmd STREAM …`) | Tag has **no `STREAM` verb** → dispatcher returns `UNKNOWN_CMD` (uwb_tag_ble.c:2060). Only a build-time `APP_TAG_STREAM_FORCE_OFF_AT_BOOT` (:40) exists. Scripts already treat it as best-effort and fall back to `MODE IDLE`. |
| `MODE AOTA` | `run_recv_tdma_capture.py` (`cmd_all MODE AOTA`, :2141/:2415) | `AOTA` appears **nowhere** in tag firmware; the mode parser (uwb_tag_ble.c:771-801) returns false → `MODE_BAD`. Listed as a valid mode in the 2026-07-06 audit, so it was **removed from the tag but the host sender remains**. Harmless: paired with `MODE IDLE` which does stop ranging. |

**⚠ ORPHAN — handler exists, NO sender:**
| verb | handler | note |
|---|---|---|
| `TDMA_STATUS` | uwb_tag_ble.c:1585 | not in tag HELP; no sender in firmware or scripts; superseded by `MODE?` / `CFG_STATUS`. |
| `MMOT` | uwb_tag_ble.c:1842 (:1851) | hidden exact-match alias for `MODE RUN`; nothing sends it. (A `MMOT<suffix>` string mis-parses `arg=cmd+5` — dead alt-path.) |

**⚠ ORPHAN — dead code (never linked / never compiled):**
| unit | evidence |
|---|---|
| `apps/master/src/master_app.c` | legacy single-connection demo with its own `PING`/`STATUS`/`OTA_STATUS`/`OTA_PREPARE` loop (master_app.c:436-439); **not in any CMakeLists `target_sources`** — the live engine is `master_multi_app.c`. |
| `src/uwb_control_proto.c` | UWB-airframe command skeleton `PING`/`SINGLE_RANGE`/`START_SWEEP`/`START_AUTOPOS` (encoders :70-124); **in no CMakeLists, zero callers** — never compiled. (This is the would-be UWB command channel if BLE were removed.) |

**Manual-only (documented but no automated sender — NOT orphaned):** tag `CFG_RUN`, `CFG_STOP`, `OTA_CANCEL`, `TDMA_SET` are in tag HELP and reachable via `cmd` passthrough. Anchor `HELP`/`SYNC`/`US*` similar.

**AMBIGUOUS (quoted, not guessed):**
- Anchor `CIR=OFF|COMPACT|FULL` is **not** a standalone verb — only a `RUNTIME …` sub-argument (`anchor_ble_ctrl.c:582-599`); a bare top-level `CIR=FULL` returns `ERR:BAD_CMD`.
- Anchor UART `M`/`MASTER`/`ROLE SET MASTER` accept the word but **store MATRIX** — `g_working_cfg.role = persistent_role_normalize(role);` (uart_role_switch.c:206). So "set master" over UART silently yields matrix.
- Master `master_ctrl/mode` NVS key is exported but there is **no `settings_save()`** in `apps/master_control/src`, `apps/master/src`, or `apps/master_ota/src` (grep-verified) → mode persistence is warm-reboot-cookie only, not power-persistent.
- OTA SMP path is image-agnostic (always uploads the single baked-in `tag_ota_image[]`); tag-vs-anchor is selected by the runtime target filter + `expect_nus` + which master image is loaded, not a distinct code path — hence "TAG-or-ANCHOR SMP."

---

## §E. Completeness / Audit Trail

**Files searched (full read unless noted):**
- **TAG:** `apps/tag/src/uwb_tag_ble.c` (2317 L, dispatcher `ble_received` 1540-2061), `tag_app.c` (no parser), `main.c`, `src/ss_twr_init.c` (TXPWR/DIAG/CIR handler bodies), `src/uwb_anchor_layout.c`, `include/uwb_tdma.h`.
- **ANCHOR:** `src/anchors/unified/{anchor_ble_ctrl.c (896 L), anchor_ble_ctrl.h, anchor_ble_id.c/.h, uart_role_switch.c/.h, anchor_ultrasound.c/.h, anchor_runtime_control.c/.h, anchor_config.c/.h, anchor_cir_output.c/.h, anchor_mcumgr_diag.c/.h}`, `apps/anchor/src/{anchor_app.c, main.c}`, `src/ss_twr_anchor_init.c`, `src/ss_twr_resp.c` (no parser), `src/uwb_ss_twr_shared.c`.
- **MASTER:** `apps/master_control/src/main.c` (3362 L, dispatcher :2337, persistence :593-720, boot :3230), `apps/master/src/master_multi_app.c` (4089 L, forwarding engine), `apps/master/src/master_app.c` (ORPHAN), `apps/master/src/{main.c, master_multi_app.h}`, both `CMakeLists.txt`.
- **MASTER-OTA:** `apps/master_ota/src/main.c` (2792 L, full), `bt_rand.c`, `master_ota.h`, `generated/ota_image.inc`, `master_control/CMakeLists.txt`.
- **LISTENER:** `UWB_listener/src/main.c` (1129 L), `apps/ble_listener/src/main.c` (638 L), `apps/b120_ble_probe/src/main.c`, `apps/b120_cdc_probe/src/main.c`; cross-ref `<repo-root>/UWB_listener/src/main.c` (grep only).
- **HOST:** `scripts/` (105 `*.py` + `*.sh`), `experiments/` (3 `*.py` + report subdirs); flash/JLink wrappers.
- **Dead-code check:** `src/uwb_control_proto.c` (confirmed no CMakeLists ref, no callers).
- **Prior art consulted:** `BLE_COMMAND_PATH_AUDIT_20260706.md` (2026-07-06 static audit; line numbers there have since drifted — this doc re-derived them live).

**Grep patterns used (per piece):** `strcmp|strncmp|strncasecmp|strcasecmp|strstr|memcmp|strtok|strtok_r|sscanf`; per-verb literal searches (`CFG`, `RUNTIME`, `R MASTER`, `MODE`, `TDMA_SET`, `CIR`, `APOS*`, `OTA_*`, `TXPWR`, `DIAG`, `SWEEP`, `US*`, `MMOT`, `STREAM`, `AOTA`, `PING`, `VERSION`, `REBOOT`, `COMMIT`, `VALIDATE`, `RESET`, `STOP`, `DFU`, `MODE_TAG|MODE_LISTEN|MODE_IDLE`); `settings_save_one|settings_load|SETTINGS_STATIC_HANDLER|nvs_|flash_write|flash_erase`; `bt_nus_client_send|bt_gatt_write_without_response|bt_dfu_smp|MGMT_GROUP`; `sys_reboot|nrf_power_gpregret_set`; host: `serial.Serial|import serial|.write(|/dev/ttyACM|/dev/ttyUSB|subprocess|nrfutil|JLinkExe|nrfjprog|BleakScanner|send_cmd|send_serial_command|write_cmd`.

**Known gaps / caveats:** (1) line numbers are a 2026-07-15 snapshot. (2) Firmware handler existence for host-emitted strings was cross-checked against §1-§4; the SMP binary protocol was mapped by group/cmd IDs, not a text handler. (3) `apps/master/src/master_app.c` ORPHAN status assumes no out-of-tree build wrapper compiles it. (4) The sibling top-level `UWB_listener` tree (Geiger MODE_SCAN variant) was noted but not fully inventoried — the broadcast tree is the live one.

*Read-only static audit. No files were modified, built, or flashed.*
