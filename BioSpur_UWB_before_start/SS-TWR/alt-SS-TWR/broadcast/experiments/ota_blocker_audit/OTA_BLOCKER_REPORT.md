# OTA-BLOCKER Deep Audit — What Can Lock Out an OTA (and the Escape Hatch)

**Date:** 2026-07-15 · **Type:** read-only code audit (no build/flash/hardware)
**Reference:** `docs/COMMAND_REFERENCE.md` (full command inventory)
**Path convention:** every `file:line` is relative to the broadcast tree root
`/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast`.
Firmware line numbers verified live for this report; capture-script line numbers from the Q5 exit-path trace.

---

## TL;DR — the three verdicts operators asked for

> **Q1 — Does OTA require `MODE IDLE`? → NO.**
> `OTA_PREPARE` alone fully quiesces the tag (purges the TX queue **and** blocks every telemetry stream, mode-independent). Sending `MODE IDLE` before OTA is **vestigial and mildly harmful** (it writes persistent NVS IDLE). **Correct preflight: do NOT send `MODE IDLE`; send `OTA_PREPARE` (the deploy scripts already do).**

> **Q3 — Is a "full chip erase" necessary to clear a zombie master mode? → NO.**
> `control_mode` is **never written to flash** (there is no `settings_save()` anywhere in the master sources; only `autopos_target`/`autopos_map_*` are `settings_save_one`'d). A master's boot mode comes from a `__noinit` RAM cookie (warm-reboot only) **plus the compile-time boot profile**. A full erase cannot change it. **Fix a zombie mode with the correct `APP_MASTER_BOOT_PROFILE` + a power cycle.** Full erase only clears the *stale-sweep* NVS (`autopos_target`/`map`) and stale anchor configs — neither of which locks an OTA.

> **Core mechanism — the ONLY hard OTA-lock is a tag HELD CONNECTED by a master.**
> A connected BLE peripheral does not advertise, so the OTA master's scan sees nothing. Every *other* suspected blocker (persisted `MODE IDLE`, forgotten `oneshot`, a capture that exited dirty) leaves the tag **still advertising** → recoverable, **not** a lock. Advertising on the tag is **unconditional** (`uwb_tag_ble.c:1017`, resumed on every disconnect at `:1368`), so the instant a tag is released it reappears.

---

## OTA-BLOCKER decision table

| trigger | mechanism (file:line) | stops advertising? | recovery | severity | fix / preflight step |
|---|---|---|---|---|---|
| **Tag held connected by the wrong master** (the 2026-07-15 incident class) | A master on the **tag** target-kind auto-connects & holds BS* tags: `auto_connect_enabled` default true (`master_multi_app.c:256`), scan-callback accepts matching BS peers → `candidate_accept` (`master_multi_app.c:2899-2936`); a connected BLE peripheral does not advertise; tag re-advertises **only** on disconnect (`uwb_tag_ble.c:1368`) | **YES** (while connected) | Disconnect it from the holder → tag re-advertises instantly (`uwb_tag_ble.c:1368`). On the holder: **`scan`** (`main.c:2608` → scan-only + disconnect-all) or **power it off** or `device kind anchor` (`main.c:3070`) | **RED** | Exactly ONE master owns the tag target-kind (the OTA master). Send `scan`/power-down to every other master **before** OTA. |
| **Master boots on the tag target-kind by default/wrong profile** | `APP_MASTER_BOOT_PROFILE` default `"neutral"` (`main.c:44`); `control_apply_boot_profile()` (`main.c:435`, called `:3337`): `"anchor"`→AUTOPOS (rejects BS tags, see next row), `"tag"`→RECV+tag target (`:450`), `"neutral"`→leaves target UNKNOWN. A master driven to RECV/`device kind tag` then grabs BS* tags | **causes row 1** | Pin the profile; for an anchor-carrier flash `"anchor"` so it **rejects** wand tags (`master_multi_app.c:2856`) | **RED** | Build/flash **each** master with an explicit profile. Master_Anchor **must** be `"anchor"`. Verify with `status` / `device show` after boot. |
| **AUTOPOS master (correctly) does NOT hold tags** | `runtime_target_kind == MASTER_TARGET_ANCHOR` → bare BS tag **rejected**: `"ANCHOR candidate rejected: bare BS peer"` (`master_multi_app.c:2856`) | **NO** | n/a (this is the safe state for the non-OTA carrier) | — (reference) | This is *why* the `"anchor"` profile is the fix: an anchor-target master ignores wand tags. |
| **Zombie `control_mode` across a WARM reboot** | `control_boot_mode`/`control_boot_cookie` are `__noinit` **RAM** (`main.c:79-80`); `control_load_mode()` restores mode iff cookie==MAGIC (`main.c:3215-3217`). Survives `sys_reboot` (warm); **cleared by power cycle**; never in flash | only if it lands the master on the tag kind → row 1 | **Power-cycle** the master (clears `__noinit` → boot profile decides). **Not** a full erase | **ORANGE** | When in doubt, power-cycle (not warm reboot); confirm `status`. |
| **Persisted `MODE IDLE` on a tag** | `MODE`/`CFG` write NVS `tag_ble/runtime_cfg` (`uwb_tag_ble.c:706`, handler `:1842`); `apply_mode_defaults` idle branch only disables **TDMA** (`:809-822`); **advertising is unconditional** (`:1017`, `:478`, `:1368`) — not mode-gated | **NO** | Tag is already advertising → OTA master connects normally; `OTA_PREPARE` quiesces it. Post-OTA send `MODE RUN` (or the next `CFG` restores RUN) | **YELLOW** (no ranging; not an OTA lock) | None needed for OTA. Leave tags in RUN for cleanliness. |
| **Forgotten `oneshot MODE IDLE` / `oneshot CIR FULL` on the master** | `runtime_one_shot_cmd[]` **static RAM** (`master_multi_app.c:271-272`); re-sent to each tag on every reconnect while `!one_shot_sent` (`:2602-2618`); `one_shot_sent` reset on disconnect/quiesce (`:3293`,`:3342`). Query `oneshot show` (`main.c:2468`→`master_multi_app.c:3917`); cleared by `oneshot clear` (`main.c:2472`), clean-RECV (`main.c:780`), `device kind` (`main.c:3072`,`:3100`); **cleared by master reboot** (RAM) | **NO** (tag still advertises) but silently re-writes persistent NVS IDLE to every tag on every reconnect → ranging stops | `oneshot clear`; or reboot the master | **ORANGE** (silent; defeats "keep ranging," not OTA itself) | Preflight: `oneshot show` → `oneshot clear`. |
| **Capture exit → transient/persistent `MODE IDLE`** | Setup `cmd_all MODE IDLE` (`run_recv_tdma_capture.py:2141`,`:2256`) is **overwritten** by the master's `CFG PMODE=RUN` at `tdma hold 0` (`:2260`) — see Q5 resolution. Exit sends `MODE AOTA` (no-op) + `tdma clear` + **conditional** `MODE IDLE` (`:2435`,`:2471`). **No** signal/atexit handler → crash/Ctrl-C in the setup window strands IDLE. `quarantine_tags.py:49` deliberately persists IDLE | **NO** | `MODE RUN` / next capture `CFG` | **YELLOW** (no ranging; not an OTA lock) | Capture exit contract (below): restore `MODE RUN` on **every** exit. |

**Reading the table:** exactly one row is RED-and-locking (tag held connected); the second RED row (wrong boot profile) is the *upstream cause* of it. Everything below ORANGE leaves tags advertising and is therefore recoverable without touching the holder.

---

## Q1 — OTA does not need `MODE IDLE` (detail)

**Production OTA entry sequence** (master → tag, `apps/master_ota/src/main.c`): `ota_arm_target_via_nus()` sends `"OTA_PREPARE\n"` (`:959`), waits 300 ms, sends `"OTA_BEGIN\n"` (`:967`). No `MODE` command is part of the OTA path.

**`OTA_PREPARE` fully quiesces the tag, independent of mode** (`uwb_tag_ble.c:2006-2017`):
- sets `ota_ready = true; ota_active = true` (`:2008-2009`),
- clears pending cal/samples/bundle (`:2010-2012`), cancels the bundle flush (`:2014`),
- **purges the TX queue** (`:2015`).

`ota_active` then **gates every telemetry path** via `uwb_tag_ble_runtime_stream_blocked_locked()` which simply `return ota_active` (`:1263-1266`) and is checked in the flush handler (`:1281`) and all four enqueue/stream sites (`:1499`, `:2127`, `:2219`, `:2279`). None of this reads `positioning_mode`. **So a tag in `MODE RUN` is completely silenced by `OTA_PREPARE` — a prior `MODE IDLE` adds nothing.**

**Why pre-OTA `MODE IDLE` is harmful, not just redundant:** `MODE IDLE` writes persistent NVS (`tag_ble/runtime_cfg`, `:706`). OTA does not touch `positioning_mode`, and the post-OTA reboot loads that NVS → the tag boots **stopped** (still advertising, but not ranging) until a `MODE RUN`/`CFG` arrives.

**VERDICT: the correct OTA preflight is "OTA_PREPARE is enough — do NOT send `MODE IDLE`."**

**Scripts that send a vestigial pre-OTA `MODE IDLE` (audit only — do NOT change without operator sign-off):**
- `scripts/ota_single_tag_stable.py:576` — `cmd MODE IDLE` before `mode ota`. Vestigial; harmless to the OTA itself but leaves the tag persistently IDLE post-flash. Recommend removing.

---

## Q2 — Persisted `MODE IDLE` does not stop advertising (detail)

`uwb_tag_ble_start_advertising()` (`uwb_tag_ble.c:1017-1038`) is a bare `bt_le_adv_stop()`/`bt_le_adv_start()` loop with **no reference to `positioning_mode`**. It is called from three unconditional sites: BLE init (`:478`), the advertising-retry work item, and **every disconnect** (`ble_disconnected`, `:1368`). The idle path in `apply_mode_defaults` (`:809-822`) only zeroes TDMA fields (`tdma.enabled=false`, slot params) — it never touches advertising.

**VERDICT: advertising is UNCONDITIONAL. A tag in persisted `MODE IDLE` still advertises and is still connectable → persisted IDLE is recoverable (connect + `MODE RUN`), NOT a hard OTA-lock.** Recovery does not require a chip erase or physical reflash.

---

## Q3 — The flash-persistence contradiction, resolved

`docs/COMMAND_REFERENCE.md §D` said mode is not power-persistent; the 2026-07-15 "full chip erase" fix implied it was. **The command reference is correct.**

**Boot sequence** (`main()`): `settings_load()` (`main.c:3251`) → `control_load_mode()` (`:3257`) → later `control_apply_boot_profile()` (`:3337`).
- `settings_load()` restores **only** `master_ctrl/autopos_target` and `master_ctrl/autopos_map_*` — the sole keys ever committed, via `settings_save_one` at `main.c:679` and `:692`. **There is no `settings_save()` anywhere in `apps/master_control/src`, `apps/master/src`, or `apps/master_ota/src`** (grep-verified), and `master_ctrl/mode` is only ever *exported* (`control_settings_export:649`), never written.
- `control_load_mode()` (`:3213-3222`): if `control_boot_cookie (__noinit RAM) == CONTROL_BOOT_COOKIE_MAGIC` → `control_mode = control_boot_mode`; else → `CONTROL_MODE_RECV`. `__noinit` RAM survives a **warm** `sys_reboot` but is garbage after a power cycle / fresh flash.
- `control_apply_boot_profile()` (`:435-465`): `"anchor"` → forces `CONTROL_MODE_AUTOPOS` (`:443`); `"tag"` → RECV (`:451`); `"neutral"` (default) → leaves mode as-is, no role target.

**Therefore `control_mode` is determined by (a) a warm-reboot `__noinit` cookie and (b) the compile-time boot profile — never by flash.** A "zombie AUTOPOS" that survived a *reflash* was the boot profile doing exactly what it was compiled to do (`"anchor"` → AUTOPOS), not a flash-persisted runtime state. A power cycle does not change a compiled boot profile, but it *does* clear the warm cookie.

**VERDICT (corrects the freeze doc):**
- **Full chip erase is NOT necessary to clear a zombie `control_mode`** — mode is never in flash.
- To fix a master that boots into the wrong mode: **flash it with the correct `APP_MASTER_BOOT_PROFILE`** (Master_Anchor=`anchor`, Master_Tag=`tag`), **and power-cycle** (not just warm-reboot) so no stale `__noinit` cookie carries a prior AUTOPOS/OTA forward. Confirm with `status`.
- Full erase remains *optionally* useful for a different reason: it clears the power-persistent `autopos_target`/`autopos_map_*` NVS (a **stale-sweep** hazard, not an OTA lock) and any stale anchor flash config. It is **not** part of the OTA-unblock path.

**AMBIGUOUS (quoted):** the incident report says the Master_Anchor "booted into AUTOPOS and held all 3 wand tags." At the code level an AUTOPOS master (`MASTER_TARGET_ANCHOR`) **rejects** bare BS wand tags — `master_multi_app.c:2856`: `printk("ANCHOR candidate rejected: bare BS peer …"); return;`. So a *freshly-booted* AUTOPOS master would not grab advertising tags. The tags were therefore held either (a) via connections established while the carrier was on the **tag** target-kind (warm-cookie-restored RECV, or an explicit `device kind tag`) before/at the AUTOPOS transition, or (b) as stale links not dropped across the mode change. The exact grab path is not determinable from code; what *is* certain is that the `"anchor"` boot profile fixes it by making the carrier reject tags (2856).

---

## Q4 — `oneshot`: the invisible tag re-lock (detail)

- **Storage:** `runtime_one_shot_cmd[]` + `runtime_one_shot_cmd_set` — **static RAM** (`master_multi_app.c:271-272`), set by `master_set_one_shot_command()` (`:3872`). **Not NVS.** Zero-initialised on boot → a master reboot/power-cycle clears it. No boot-time restore path exists.
- **Re-application:** on every peer (re)connect, if a command is armed and `!peer->one_shot_sent`, it is sent and `one_shot_sent=true` (`:2602-2618`). `one_shot_sent` is reset to false on disconnect/quiesce (`:3293`, `:3342`) and on arm/clear (`:3896`, `:3912`) — so **it re-fires on every reconnect** until cleared.
- **Query:** **yes** — `oneshot show` → `master_print_one_shot_command()` (`main.c:2468` → `master_multi_app.c:3917`). **Clear:** `oneshot clear` (`:2472`), and implicitly by clean-RECV (`main.c:780`) and `device kind` switches (`:3072`, `:3100`).

**Q4.4 — confirmed:** a forgotten `oneshot MODE IDLE` **does** silently re-lock tags — every reconnect re-sends `MODE IDLE`, which persists to each tag's NVS and stops its ranging. It reads exactly as "the tags keep going quiet." **However** it is **not** an OTA-lock: the tag still advertises (Q2), and it clears on master reboot. `oneshot CIR FULL` similarly re-loads the BLE with heavy CIR on every reconnect.

**Recommendation (Q4.5):** make `oneshot show` → `oneshot clear` a mandatory OTA/capture preflight step (it *is* query-able and force-clearable today; the gap is procedural, not a missing command).

---

## Q5 — The capture exit contract, and the `STREAM OFF`→IDLE chain

**Resolution of the one firmware ambiguity the exit-trace flagged:** the master's `CFG` builder always emits `PMODE = UWB_TAG_POSITIONING_MODE_DYNAMIC` (`master_multi_app.c:1133`, `= UWB_TAG_MODE_RUN`, `uwb_tdma.h:24`), and the tag's `CFG` parser defaults `positioning_mode = UWB_TAG_MODE_RUN` (`uwb_tag_ble.c:943`) and **persists it** to NVS (`:976`→`:1951`). So the setup `cmd_all MODE IDLE` (`run_recv_tdma_capture.py:2256`) is **overwritten by the very next master `CFG` (PMODE=RUN)** when TDMA is released at `tdma hold 0` (`:2260`). **A normally-configured tag therefore ends persisted RUN, not IDLE.**

**What actually leaves a tag in persisted IDLE:**
1. the **conditional** exit fallback `cmd_all MODE IDLE` (`run_recv_tdma_capture.py:2435`, twin `:2471`) — fires only if `verify_quiet()` still sees TR/CM activity after the (no-op) `MODE AOTA` + `tdma clear`; or
2. a **crash / Ctrl-C / SIGTERM in the setup window** between `:2256` and the `CFG` delivery at `:2260` — there is **no outer `try/finally` and no signal/atexit handler**, so cleanup (`:3543`) is skipped and the transient setup IDLE is stranded; or
3. `quarantine_tags.py:49` (`quarantine_tag_for_sweep` → `cmd MODE IDLE` at `run_autopos_sweep_loop.py:2457`), which deliberately persists IDLE with no following `CFG`.

**In all three cases the tag still advertises (Q2) → none is a hard OTA-lock.** The `STREAM OFF`/`STREAM 0`/`STREAMON 0` strings are **no-ops** (no tag handler → `UNKNOWN_CMD`, `uwb_tag_ble.c:2060`); `MODE AOTA` is a **no-op** (`AOTA` was removed from the tag mode model — freeze `AUDIT.md` T7; parser returns false, `uwb_tag_ble.c:771-801`). So the real "stop" in a clean capture exit is `tdma clear`, not any tag MODE command.

**No capture/quarantine/OTA script restores `MODE RUN` anywhere** (grep across all six scripts: zero `MODE RUN`/`STREAM ON` hits). Tags are only ever put into IDLE or left stopped; ranging is re-established solely through the master's TDMA scheduler.

**VERDICT:** the "capture that didn't exit cleanly left tags in a bad state" is **real but not an OTA-lock** — it leaves tags stopped-but-advertising. It becomes a *ranging* problem, and only looks like an OTA problem when combined with a master still holding the tags (row 1).

### Capture exit contract (spec — what every capture script MUST do)

On **every** exit path — normal completion, `KeyboardInterrupt`, `SIGTERM`, and unhandled exception — via an outer `try/finally` **plus** a `signal`/`atexit` handler:

1. **Restore tags to ranging + advertising:** send `cmd_all MODE RUN` (or a final `CFG … PMODE=<RUN>`). *(Currently NO script does this.)*
2. **Clear any armed oneshot:** `oneshot clear`.
3. **Do not rely on no-ops:** `MODE AOTA` and `STREAM OFF` do nothing on the tag; use `tdma clear` + `MODE RUN`.
4. **Never leave tags in persistent `MODE IDLE`** unless that is the explicit, logged intent (as in `quarantine_tags.py`) — and even then, log a reminder to `MODE RUN` before the next OTA/run.

### Exact lines implicated (audit only — do NOT change without operator sign-off)
- `scripts/run_recv_tdma_capture.py:2256` (setup IDLE, first persistent write), `:2141` (initial IDLE), `:2435`/`:2471` (conditional exit IDLE), `:2415`/`:2454` (`MODE AOTA` no-op), `:3523` (`except KeyboardInterrupt` covers only the loop), `:3543` (skippable cleanup — no outer `try/finally`); **no `MODE RUN` restore exists in the file.**
- `scripts/quarantine_tags.py:49` (persistent IDLE) + `:59-63` (finally closes serial only, no restore).
- `scripts/run_autopos_sweep_loop.py:2457` (`cmd MODE IDLE` inside quarantine helper), `:2405-2407` (`STREAM OFF` no-ops).
- `scripts/ota_single_tag_stable.py:576` (vestigial pre-OTA `MODE IDLE`, per Q1).
- `experiments/run_overnight_power.py:207` (`finally` restores `TXPWR MAX` only, not MODE; does not run on SIGTERM/SIGKILL).

---

## Q6 — The escape hatch

**Is there an atomic "release all tags, stop auto-connect, leave them advertising"?** Yes — **`scan`** on the holding master (`main.c:2608-2623`):
`master_set_scan_only_mode()` (sets `auto_connect_enabled = false`, `master_multi_app.c:3234-3236`; the connect path honours it at `:1694`) → `master_disconnect_all_peers()` (`:3251`) → `master_restart_discovery()`. Result: all peers disconnected → tags re-advertise (`uwb_tag_ble.c:1368`) → master observes but does **not** re-grab. `master_quiesce_peers()` (`:3317`, used by clean-RECV) is the same primitive plus `stop_scan()`.

**Constraint:** `scan` is only accepted in `CONTROL_MODE_RECV`/`AUTOPOS` (`main.c:2609`), which is exactly where a mis-behaving holder is — so it applies. It is **not** valid from OTA mode, but you run it on the *non-OTA* (holding) master, and OTA on the *other* master. This matches the freeze topology (Master_Anchor holds, Master_Tag does the OTA).

**Robust escape sequence (from existing commands):**
1. On the **holding** master: `oneshot clear` → `scan`. (Or simply **power it down** — the most reliable release; the tags reappear the instant the link drops, `uwb_tag_ble.c:1368`.)
2. On the **OTA** master: `scan` then confirm SCAN hits / `device show` list all expected BS* tags. Any missing tag is still held → find/kill its holder.
3. Proceed with OTA on the OTA master.

**Recommendation:** the composition works, but there is no *single* verb and no guard preventing two masters from both owning the tag kind. A small new verb (e.g. `release_tags` = quiesce + hold, or a `hold` flag that blocks re-grab until explicitly released) would make this foolproof; not required, but worth considering.

### OTA PREFLIGHT checklist (ordered)

1. **Inventory masters.** Know every powered master and its intended role. Only the OTA master may own the tag target-kind.
2. **Neutralise every non-OTA master:** on each, `oneshot show` → `oneshot clear`, then `scan` (releases tags, stops auto-connect) **or power it down**. Confirm via `status` it is not holding tags.
3. **Confirm tags are visible:** on the OTA master, `scan` and verify SCAN hits for **all** target BS* tags (`device show`). A missing tag = still held → resolve before continuing.
4. **Clear the OTA master's own oneshot:** `oneshot show` → `oneshot clear`.
5. **Set the target and go:** `device kind tag` (or `anchor`), `ota_target …`, `conn`, then `mode ota` / `initiate`. **Do NOT send `MODE IDLE`** — `OTA_PREPARE` quiesces the tag.
6. **Zombie-mode check (no erase):** if any master booted into the wrong mode, **power-cycle** it and confirm `status`; if the boot mode is still wrong, reflash with the correct `APP_MASTER_BOOT_PROFILE`. Do **not** reach for a full chip erase to fix mode.
7. **Post-OTA:** verify each tag boots **advertising + ranging**. If a tag boots IDLE (e.g. left by a prior capture), send `cmd_all MODE RUN` (or let the next `CFG` restore RUN).

---

## Corrected firmware laws (proposed — for the freeze doc)

*(No pre-existing "five laws" artifact was found in the tree; these are proposed for the operator to adopt. Each is code-anchored.)*

1. **OTA never needs `MODE IDLE`.** `OTA_PREPARE` (`uwb_tag_ble.c:2006`) alone quiesces the tag (purges TX, blocks all streaming via `ota_active`, `:1266`). Do not pre-idle tags for OTA; it only persists a stopped state.
2. **Tags always advertise, regardless of mode.** Advertising is unconditional (`uwb_tag_ble.c:1017`, resumed on every disconnect `:1368`). The **only** thing that stops a tag advertising is being **held connected** by a master — so the only hard OTA-lock is a master holding the tags.
3. **`control_mode` is not in flash.** No `settings_save()` exists in the master sources; boot mode = `__noinit` warm-reboot cookie (`main.c:79-80`, `:3215`) + compile-time boot profile (`:3337`). A **full chip erase cannot change a master's mode.** Fix a zombie mode with the correct `APP_MASTER_BOOT_PROFILE` + a **power cycle**.
4. **Master identity is the boot profile.** `APP_MASTER_BOOT_PROFILE` (compile-time): `"anchor"` **rejects** wand tags (`master_multi_app.c:2856`); `"tag"`/`"neutral"` can grab them. **Exactly one master may own the tag target-kind at a time.**
5. **Leave tags in RUN.** Persisted `MODE IDLE` is not an OTA lock but silently stops ranging (`uwb_tag_ble.c:809-822`, NVS `:706`). Capture/quarantine/OTA scripts must restore `MODE RUN` on **every** exit path — **none currently do**.

---

## Completeness / audit trail

**Firmware read (verified live):** `apps/tag/src/uwb_tag_ble.c` (advertising 1017/1368/478; OTA handlers 2006-2043; stream gate 1263-1266 + callers 1281/1499/2127/2219/2279; CFG parser 929-983 + handler 1937-1972; mode parser 765-801; idle defaults 803-837), `apps/master_control/src/main.c` (boot 3245-3281; `control_load_mode` 3213-3222; `control_apply_boot_profile` 435-465; persistence 649/679/692/696; `scan` 2608; clean-RECV 777-799; oneshot dispatch 2465-2476), `apps/master/src/master_multi_app.c` (oneshot 271-272/486-489/2602-2618/3872-3920; connect filter 2825-2948, tag-reject vs anchor-reject 2856/2899; quiesce/scan-only 3234-3345; CFG builder 1327-1372 + `master_tdma_profile_pmode` 1133), `apps/master_ota/src/main.c` (arm 950-967), `include/uwb_tdma.h` (24-26).
**Capture scripts traced (Q5 sub-audit):** `run_recv_tdma_capture.py`, `run_autopos_sweep_loop.py`, `quarantine_tags.py`, `run_dual_master_tdma_capture.py`, the two listener wrappers, `experiments/run_overnight_power.py`; cross-ref `ota_single_tag_stable.py`.
**Grep patterns:** `settings_save\b`, `settings_save_one`, `__noinit`, `control_boot_(mode|cookie)`, `boot_profile_is`, `APP_MASTER_BOOT_PROFILE`, `auto_connect_enabled`, `candidate_accept|ANCHOR candidate rejected`, `one_shot`, `ota_active|ota_ready`, `runtime_stream_blocked`, `start_advertising|bt_le_adv`, `PMODE=|profile_pmode`, `MODE IDLE|MODE AOTA|MODE RUN|STREAM`, `signal|atexit|SIGINT|SIGTERM`, `try:|finally:|except`.
**AMBIGUOUS items:** (1) the exact path by which the 2026-07-15 Master_Anchor came to hold the wand tags — an AUTOPOS master rejects bare BS tags (`master_multi_app.c:2856`), so the hold predates or bypassed the AUTOPOS target state; the `"anchor"` profile fix is nonetheless verified. (2) A `"neutral"`-profile master leaves `runtime_target_kind` UNKNOWN (`master_multi_app.c:273`); whether UNKNOWN can auto-connect a tag was not exhaustively traced — the safe rule (pin every master's profile) covers it either way.

*Read-only static audit. No firmware was built, flashed, or run; no hardware was touched.*
