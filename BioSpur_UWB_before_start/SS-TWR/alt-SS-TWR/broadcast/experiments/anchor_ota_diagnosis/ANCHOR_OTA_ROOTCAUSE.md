# Anchor-OTA "drop all 8" instability — root-cause diagnosis (firmware vs script)

**Date:** 2026-07-16 · **Status:** diagnosis complete · freeze-clean PAUSED pending operator decision.
**Tree:** `SS-TWR/alt-SS-TWR/broadcast`. Repro was a **no-op OTA** (anchor binary is byte-identical to freeze — marker only), so it was safe.

## BOTTOM LINE (verdict)

The incident has **two layers**, and both must be named:

1. **SCRIPT defect (the trigger of the observed incident).** `scripts/ota_deploy_anchor_set.py` runs consecutive per-anchor OTAs with **no master reset and no "wait for 8/8" settle between anchors** (by default), and on the first per-anchor failure it **aborts before the control-plane recovery runs**, leaving the master **stranded in OTA mode** → all 8 anchors stay dropped. This is fixable in the host script.

2. **FIRMWARE limitation (why the workaround MUST use a JLink reset, not a software settle).** After the master enters OTA mode (which disconnects all 8 + warm-reboots **by design**), its **software recovery** (`mode recv` → warm reboot → AUTOPOS) **does NOT re-establish the 8 anchor connections** — empirically 0/8 sustained for 3+ minutes. Only a **cold boot (JLink reset / power cycle)** reconnects them (8/8 in ~40 s). So the per-anchor JLink reset in the freeze recipe is **load-bearing**, not belt-and-suspenders. This touches frozen master firmware → **PROPOSE-and-STOP** (not fixed inline).

**The "drop all 8" is NOT a firmware fault/panic on an SMP error** — it is the *deliberate* entry into OTA mode. The instability = script strands the master there + firmware can't software-recover the anchor links.

---

## PHASE 0 — read-first (freeze notes + code path + hypotheses)

### 0.1 The recorded warning: workaround only, no root cause

`FREEZE_4PIECE_20260715.md:143-148` (verbatim):
> **Anchor OTA recipe that works:** per-anchor `ota_single_shot_stable.py` (`--target-uuid <A..H uuid>`), each preceded by a **JLink reset of Master_Anchor + wait for 8/8 anchors `conn=1 ready=1`**. The `ota_deploy_anchor_set.py` *pre-version phase* mode-churn destabilizes the master (transient CDC write-timeout) → use `--skip-pre-version-verify`, or the per-anchor loop. Anchor A is the finicky one.

Only the **workaround** (per-anchor JLink reset + wait-for-8/8) and a **symptom** ("mode-churn destabilizes the master") were recorded. **No firmware root cause / hypothesis existed.** `experiments/ota_blocker_audit/OTA_BLOCKER_REPORT.md` is about TAG locks, not this; `FREEZE_A18_REVERIFIED_8ANCHOR_20260701.md:116` root cause was a damaged anchor image (unrelated).

### 0.2 Anchor-OTA code path (file:line)

**Host:**
- Batch driver `scripts/ota_deploy_anchor_set.py`: UUID map A..H `:15-24`; per-anchor loop `:682` spawns `ota_single_shot_stable.py --target-uuid <uuid>` `:776-799`; **between anchors it does nothing by default** — per-anchor reset/settle only behind opt-in `--pre-reset-first` `:687-771`, `--master-reset-snr-after-pre-reset` `:743-769`, `--post-pre-reset-wait-s` `:770-771`. Abort-on-failure `:854-856` returns **before** `prepare_control_plane_after_ota()` `:858-872` (the OTA→RECV→AUTOPOS handoff that reconnects the 8).
- Per-anchor recipe `scripts/ota_single_shot_stable.py`: Phase A `mode recv` if master still OTA `:837`, `device kind anchor` `:892`, `ota_target uuid` `:910/:948`, `cmd DFU` `:1021` (make target leave its responder loop) → wait `OK DFU_READY`; Phase B `mode ota` `:1060`; Phase C `initiate` `:1197`. It waits for the **target** anchor only (`wait_for_anchor_ctrl_ready` `:1005`), **not** all-8, and does **not** JLink-reset the master. `finally` only closes serial `:1315-1320` (never restores RECV on failure → contributes to stranding).
- Error string source: `ota_single_shot_stable.py:1340` `blocker = "request did not reach anchor BLE SMP transport"` is a **host classification label** (class A1) set when the firmware prints `OTA upload gate failed` (`apps/master_ota/src/main.c:1981`).

**Master firmware:** `apps/master_control/src/main.c` = real `main()`; compiles the OTA SMP engine `apps/master_ota/src/main.c` (`-Dmain=master_ota_run`) + peer manager `apps/master/src/master_multi_app.c`. OTA uses a **single** static `bt_dfu_smp dfu_smp` over a **single** `default_conn` (`master_ota:134-135`); scan accepts only when `default_conn==NULL` (`:2473`); gate probe = 5 image-state reads 180 ms apart (`:1907-1918`); on stall → `OTA upload gate failed` (`:1981`); the gate-fail branch does **not** disconnect — it releases the session and stays put (`:2001-2002`).

**Anchor firmware:** `apps/anchor/src/anchor_app.c` advertises the DFU SMP UUID continuously; `anchor_enter_dfu_mode()` `:386-404` stops ranging `:391`, prints `OK DFU_READY` `:394`, spins. It must leave its tight responder loop (via `cmd DFU`) before its SMP is serviced.

### 0.3 Hypotheses (pre-repro)
- **H-fw-1** (master leaks BLE/SMP state across OTAs): weak — `master_ota_initiate` resets state + unrefs conn + disconnects-all before each `:2591-2613`; `disconnected` resets per-target state `:2370-2392`.
- **H-fw-2** (anchor SMP not ready after responder): supported as a *contributing* mechanism — anchor must exit responder loop `:391`, host gates on it `ota_single_shot_stable:999-1004`.
- **H-script-1** (no settle to clean 8/8 between anchors): strongly supported — no reset/8-of-8 gate in the loop.
- **H-script-2** (connects B while A half-open): weak — single `default_conn` accepted only when NULL.

**"Drop all 8" origin:** `master_control:2254-2273` — `master_disconnect_all_peers()` `:2258` + `control_disconnect_all_links()` `:2265` + `sys_reboot(SYS_REBOOT_WARM)` `:2273`, the **normal** entry into OTA mode. No SMP-error disconnect-all/reboot exists anywhere.

---

## PHASE 1 — repro (the firmware-vs-script split)

### 1.1/1.2 — today's real incident (master-side log, `logs/fc_finalize_anchor_ota_20260716/`)
- Anchor **A**: `ota_success_observed`. Its log shows the **by-design** drop-all at end: `anchor_A/stage1/single_shot.log:540-553` → `MODE_TRANSITION: RECV->OTA` → `Master disconnecting peer[0..7]` → `Control mode now OTA, rebooting`.
- Anchor **B**: `ota_gate_failed_after_dfu_ready`, class A1, blocker `request did not reach anchor BLE SMP transport`. Its log shows the master in OTA mode with `conn_count=0`, scanning `prefix=ANCHOR-`, `cmd DFU` returning `rc=-128` ("not target yet") ~15× → gate fail.
- After B failed, the driver aborted → master **stranded in OTA mode** → all 8 stayed dropped → recovered only by a full master reset.

### 1.3 — THE DECISIVE TEST (mode transitions only, no upload — safe)
Log: `experiments/anchor_ota_diagnosis/mode_transition_test.log`.

| step | command | result |
|---|---|---|
| baseline | `status` | 8/8 anchors, AUTOPOS |
| **drop-all** | `mode ota` | master → **0/8, mode=OTA** (banner `mode=OTA`) — CDC dropped mid-command (warm reboot). **Confirms "drop all 8" is by design.** |
| **(a) software recovery** | `mode recv` (redirected → AUTOPOS) | master → AUTOPOS but **0/8 sustained for 3+ min** (checked +48s…+173s). **Software recovery does NOT reconnect anchors.** |
| **(b) reset recovery** | JLink reset (SNR 960148546) | master cold-boots → AUTOPOS → **8/8 in ~40 s** (re-confirmed twice). |

**Verdict of the (a)/(b)/(c) test = (b):** a bare settle-wait / software recovery is **insufficient**; a **master cold boot (reset/power-cycle) is required** to re-establish the anchor links after an OTA-mode cycle. The firmware's warm-reboot AUTOPOS path does not reconnect advertising anchors.

---

## PHASE 2 — verdict + fix

### 2.1 Root cause
- **Script (incident trigger):** `ota_deploy_anchor_set.py:682-856` — no per-anchor master reset + no wait-for-8/8 settle; abort at `:854-856` skips the control-plane recovery `:858-872`, stranding the master in OTA mode. `ota_single_shot_stable.py:1315-1320` `finally` never restores RECV on failure.
- **Firmware (why a reset is required):** after `mode ota`'s by-design drop-all + warm reboot (`master_control:2258/:2265/:2273`), the master's **software** return path (`mode recv` → warm reboot → AUTOPOS) does not re-scan/reconnect the 8 anchors (empirical 0/8, 3+ min). Only a cold boot does. Likely the warm reboot preserves BLE/AUTOPOS RAM state that suppresses a fresh anchor scan/connect; **needs firmware investigation** (not done inline — frozen firmware).

### 2.2 SCRIPT fix — PROPOSED (apply with operator OK)
Make `ota_deploy_anchor_set.py` reliable by default (the recipe, automated):
1. Between every anchor: **JLink-reset Master_Anchor + wait for all 8 `conn=1 ready=1`** before starting the next per-anchor OTA (make `--pre-reset-first` + `--master-reset-snr-after-pre-reset <MASTER_SNR>` the default, or add an explicit `--per-anchor-master-reset` + `--wait-8of8-timeout` and default them on).
2. On **any** per-anchor failure (and in a `finally`/atexit): run `prepare_control_plane_after_ota()` (or a JLink reset) so the master is **never left in OTA mode** — recover to AUTOPOS 8/8 before exiting. This alone would have prevented today's "all 8 dropped, needs manual reset."
This makes batch anchor OTA work end-to-end using the reset; it does not require the firmware fix.

### 2.3 FIRMWARE limitation — PROPOSED, STOPPED (operator decision)
The master cannot software-recover anchor links after OTA mode. **Proposed fix (do NOT apply without review — frozen master firmware):** in the OTA→RECV/AUTOPOS handoff, force a clean anchor re-scan on the warm-reboot AUTOPOS path (e.g. reset the AUTOPOS connect state + restart scan on boot when returning from OTA), OR have `prepare_control_plane_after_ota` issue a `sys_reboot(COLD)` / trigger the AUTOPOS reconnect explicitly. **Why it matters:** reverse SS-TWR needs *real* anchor firmware changes → frequent anchor OTA. The reset-per-anchor workaround is ~5-10 min/anchor (40-80 min for 8); a firmware fix would make batch OTA fast and reset-free. Recommend fixing this before the reverse-phase anchor-OTA workload, as a reviewed post-freeze firmware change.

### 2.4 Deployment-doc update
`docs/DEPLOYMENT.md` §7 replaces the vague "anchor OTA is unstable, use the per-anchor recipe" with the real mechanism (see that file).

## Leave-state
Rig left **8/8 stable** (Master_Anchor reset to AUTOPOS, 8 anchors `conn=1 ready=1`). Anchor A carries marker `anchor-freeze-clean-20260716`; B–H carry the freeze marker — all **byte-identical binaries**.

## Resolution (operator decision 2026-07-16)

- **SCRIPT fix: APPLIED** to `scripts/ota_deploy_anchor_set.py` — per-anchor cold-reset (`--master-reset-snr`, default `960148546`) + wait-for-8/8 before EVERY anchor (`--per-anchor-reset`, default ON), and an `atexit` control-plane recovery that cold-resets to 8/8 on ANY exit (clean/abort/exception) so the master is **never left stranded in OTA mode**. Verified end-to-end.
- **FIRMWARE fix: DEFERRED — OPEN ITEM (reverse-SS-TWR prerequisite).** The frozen master firmware is NOT touched now. Before the reverse-SS-TWR phase (which needs real anchor firmware changes → frequent anchor OTA), fix the master warm-reboot AUTOPOS path so **software recovery can re-establish the anchor links**, eliminating the per-anchor JLink reset (currently ~5-10 min/anchor). Until then, batch anchor OTA works via the script fix's reset-per-anchor (slower but reliable).
