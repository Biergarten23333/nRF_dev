# BioSpur Deployment Contract

Authoritative deployment + operations doc. Built during `freeze-clean` on top of
`freeze-4piece-20260715` (commit cb8603316). Sections are filled in per batch;
the OTA preflight / escape hatch / command tables / corrected laws / output
contracts are added by freeze-clean Batch 6 + Finalize.

---

## 1. Anchor-layer naming convention (READ THIS before naming any position)

**The anchor letters encode a physical layer. Do not infer height backwards from
a single anchor name.**

- **ABCD = LOWER anchor layer.**
- **EFGH = UPPER anchor layer.**

Rules for every listener / device position label:
1. Any **height** description MUST reference an anchor pair in the **same layer**
   as the device's actual height.
   - An **UPPER-height** device is described "between E–H" (or any EFGH pair) —
     **NEVER** "between A–D" (an ABCD pair reads as *lower*).
   - A **LOWER-height** device references an **ABCD** pair.
2. Every position carries three fields: **physical face** (e.g. AEDH / BFCG),
   **height layer** (upper / lower / mid / low), and a **same-layer anchor
   reference**.

Example of the trap this prevents: SNR 760181879 sits at UPPER anchor-layer
height on the AEDH face → it is "between **E–H**, UPPER", NOT "between A–D".

---

## 2. Listener fleet snapshot — `listener-freeze-20260715`

9 units, single common image (`listener-freeze-20260715` = commit `631911c3e` +
CIR=1, generic id=255, `CONFIG_BT=0`), flashed 2026-07-15 via USB J-Link with a
full `recover` (mass-erase → **PANS-cleared** on every unit), passive-confirmed.
Full record: `SS-TWR/alt-SS-TWR/broadcast/experiments/listener_freeze_audit/LISTENER_FREEZE.md`.

**EXCLUDED — never flashed:** SNR **760185886** = legacy Geiger air monitor (盖格).

| SNR | Position | Height | Face / layer ref | PANS-cleared | Origin |
|---|---|---|---|---|---|
| 760184753 | A–E anchor-pair midpoint | mid | — | ✅ recover | operator-confirm |
| 760184548 | B–F anchor-pair midpoint | mid | — | ✅ recover | operator-confirm |
| 760181725 | C–G anchor-pair midpoint | mid | — | ✅ recover | operator-confirm |
| 760184784 | D–H anchor-pair midpoint | mid | — | ✅ recover | operator-confirm |
| 760184964 | vertical-profile LOW | low | — | ✅ recover | operator-confirm |
| 760184767 | vertical-profile MID | mid | — | ✅ recover | operator-confirm |
| 760184545 | vertical-profile HIGH ~2.3 m | high | — | ✅ recover | operator-confirm |
| 760181879 | AEDH face, **between E–H, UPPER** anchor-layer height | upper | AEDH / upper (EFGH) | ✅ recover | operator-confirm (was on outdated J-Link OB fw) |
| 760186115 | BFCG face, **between B–C, LOWER** anchor-layer height | lower | BFCG / lower (ABCD) | ✅ recover | **BSF66F** — repurposed retired tag; temporary passive; label kept; historical value preserved; **tag-active behavior confirmed OFF** |

Notes:
- All 9 booted the common image (`BioSpur co-located UWB listener start id=255 …
  cir=1 … MODE=LISTEN`), verified not-PANS. Passivity is structural
  (`MODE=LISTEN` has no TX path; `CONFIG_BT=0` → no BLE peer).
- Origin (new DWM1001C vs. repurposed board) per SNR is **operator-confirm**;
  every unit is PANS-cleared regardless (full `recover` before program).
- The live "hears polls/responses" spot-check across the fleet is pending the
  tag/anchor system ranging (see LISTENER_FREEZE.md).

---

## 3. Corrected firmware laws (freeze-clean batch6f)

Code-anchored. These correct/supersede the laws in `FREEZE_4PIECE_20260715.md`
using `experiments/ota_blocker_audit/OTA_BLOCKER_REPORT.md`.

1. **A Master carrier MUST declare its role** with an explicit
   `-DAPP_MASTER_BOOT_PROFILE=anchor|tag`. **`neutral` is a build error** — now
   *compile-enforced* (batch6b, `apps/master_control/CMakeLists.txt`), not a doc
   rule. `anchor` rejects wand tags; `tag` = RECV + BS prefix.

2. **`control_mode` is NOT in flash — do NOT "full-erase to clear a zombie mode".**
   Master boot mode = a `__noinit` **RAM** warm-reboot cookie (`main.c:79-80`,
   restored at `:3215` iff cookie==MAGIC) **plus** the compile-time boot profile
   (`:3337`). There is **no `settings_save()` of the mode** anywhere in the master
   sources. A "zombie AUTOPOS that survived a reflash" was the boot profile doing
   what it was compiled to do, not flash-persisted state. **Fix: correct
   `APP_MASTER_BOOT_PROFILE` + a POWER CYCLE** (clears the warm cookie). A full
   erase is neither necessary nor sufficient for mode.
   **Distinct, separately-true fact (do not conflate):** flashing a **B120
   nRF5340** MUST use JLink **`recover`** (CTRL-AP mass-erase + APPROTECT clear).
   A plain `erase` leaves debug `loadfile` writes **non-persistent across a power
   cycle** (blank 0x0 → HardFault); only a fresh-session physical 0x0 read proves
   persistence. This is an nRF5340 flash-tooling fact, unrelated to `control_mode`.

3. **OTA never needs `MODE IDLE`.** `OTA_PREPARE` (`uwb_tag_ble.c:2006`) alone
   quiesces the tag (purges TX, blocks all telemetry via `ota_active`). A pre-OTA
   `MODE IDLE` only persists a stopped state and is harmful, not required.

4. **Tags always advertise, regardless of mode** (`uwb_tag_ble.c:1017`, resumed on
   every disconnect `:1368`). The ONLY thing that stops a tag advertising is being
   **held connected** by a master → the only hard OTA-lock is a master holding
   the tags. Exactly one master may own the tag target-kind at a time.

5. **Leave tags in RUN; stop via LIVE `CFG_STOP`, never persistent `MODE IDLE`.**
   Persistent `MODE IDLE` writes NVS `tag_ble/runtime_cfg` and silently stops
   ranging. Every capture script must restore RUN + advertising on **every** exit
   path (normal / Ctrl-C / crash) using live-only commands. See §4.

## 3a. Master boot banner (freeze-clean batch6a)

Every master prints ONE unconditional line at boot, right after the boot profile is
applied (`apps/master_control/src/main.c`, after `control_apply_boot_profile()`):

```
=== MASTER BOOT: profile=<anchor|tag> mode=<AUTOPOS|RECV|OTA> target=<TAG|ANCHOR|NONE> wand tags: <WILL HOLD BS* | rejected> ===
```

Read it on every reflash / power cycle. It turns "a master silently holding the
wand tags" into a one-line announcement — the visibility fix for the 2026-07-15
Master_Anchor tag-grab incident:
- **`profile=anchor … wand tags: rejected`** — the anchor master will NOT connect
  wand tags (correct for Master_Anchor).
- **`profile=tag … wand tags: WILL HOLD BS*`** — the tag master owns the wand tags
  (expected ONLY on Master_Tag). If you ever see `WILL HOLD BS*` on the anchor
  carrier, the wrong image was flashed — reflash the anchor carrier.

A `neutral` profile can never appear here — it is a compile error (§3 law 1, batch6b).

## 4. Persistent vs. live command decision table (batch6c doctrine)

**Rule: transient/debug state uses LIVE commands (not persisted); persistent
`MODE <..>` is ONLY for deliberately changing the production default.**

| command | effect | NVS? | use when |
|---|---|---|---|
| `CFG_RUN` | enable TDMA transmit live → RUNNING | **live only** | start/resume ranging in a capture; reverts on reboot |
| `CFG_STOP` | disable TDMA transmit live → ARMED (halts TX) | **live only** | **stop ranging at capture exit** — the correct stop |
| `CFG TAG=… SLOT=… …` | master TDMA assignment (PMODE defaults RUN) | writes NVS | master (re)configuring a tag on connect |
| `MODE RUN` / `MODE <run-ish>` | set persisted positioning mode = RUN | **writes NVS** | deliberately set the production default to RUN |
| `MODE IDLE` / `STOP` / `HALT` | set persisted mode = IDLE (stops ranging) | **writes NVS** | ONLY to deliberately ship a stopped tag (rare) — never as a capture stop |
| `MODE AOTA` | **no-op** (AOTA removed from tag) | — | never (removed; do not send) |
| `STREAM OFF/0` / `STREAMON 0` | **no-op** (no tag handler → UNKNOWN_CMD) | — | never (removed; do not send) |
| `DIAG ON/OFF`, `TXPWR …`, `CIR …` | live radio/diag toggles | **live only** (no NVS) | diagnostics; reset to default on reboot |

**Capture exit contract (every capture script, every exit path):**
`CFG_STOP` (live halt) → ensure tags left in persisted RUN + advertising →
`oneshot clear` → NEVER leave persistent `MODE IDLE`. A crashed capture must not
strand tags in a persistent stopped state (that is the OTA-blocker chain).

## 5. OTA preflight checklist (batch6e — enforced by `ota_deploy` preflight)

1. Inventory BOTH masters' connection state first (never hunt external
   "competing centrals" — a battery-charged dongle was a 2026-07-15 red herring).
2. If a master holds the target tags → release via the escape hatch (§6).
3. Confirm no active capture (no TR streaming) on the target tags.
4. Confirm all target tags are advertising (`scan` on the OTA master sees them).
5. Deploy. On any unmet condition, report the reason + next step; do not hang.
6. OTA does NOT need `MODE IDLE` (law 3). Post-OTA: tags boot advertising + RUN.

## 6. Escape hatch — `scripts/release_all_tags.py` (batch6d)

Atomic "unstick": on the holding master, stop auto-connect (`scan`) → disconnect
all peers → verify tags re-advertise → hold the master from re-grabbing. This is
the universal unlock for any future OTA lock (a master holding tags). Uses only
existing verbs (`scan`, `status`, `device kind`).

## 7. Anchor OTA — the REAL mechanism (was "unstable, use per-anchor recipe")

Diagnosed 2026-07-16, evidence in `experiments/anchor_ota_diagnosis/ANCHOR_OTA_ROOTCAUSE.md`.
Replaces the old vague warning.

**Anchor OTA is a MODE the master reboots into.** `mode ota` deliberately
disconnects **all** anchor peers and `sys_reboot(WARM)` into a single-connection
OTA mode (`apps/master_control/src/main.c:2254-2273`). So "the master dropped all
8" during a batch OTA is **by design**, not a fault. There is **no** firmware
error path that disconnects-all/reboots on an SMP error.

**Two real problems, both must be handled:**

1. **Script (fixable):** `scripts/ota_deploy_anchor_set.py` by default does NOT
   reset the master or wait for all-8 `conn=1 ready=1` between anchors, and on a
   per-anchor failure it aborts (`:854-856`) **before** the OTA→AUTOPOS recovery
   (`:858-872`) — so the master is left **stranded in OTA mode** (all 8 dropped)
   and needs a manual reset. Correct batch OTA MUST: (a) between every anchor,
   JLink-reset Master_Anchor + wait for 8/8; (b) on ANY failure/exit, recover the
   control plane (never leave the master in OTA mode).

2. **Firmware limitation (why a RESET is required, not a software settle):** after
   OTA mode, the master's software return (`mode recv` → warm reboot → AUTOPOS)
   does **NOT** reconnect the anchors — empirically **0/8 for 3+ min**. Only a
   **cold boot (JLink reset / power cycle)** reconnects them (8/8 in ~40 s). So the
   per-anchor JLink reset is **load-bearing**. A firmware fix (clean anchor re-scan
   on the warm-reboot AUTOPOS path) is proposed but NOT applied — needed before the
   reverse-SS-TWR anchor-OTA workload; see ROOTCAUSE.md §2.3.

**Proven manual recipe (until the script fix lands):** per anchor —
`ota_single_shot_stable.py --target-uuid <A..H>`, each preceded by a JLink reset of
Master_Anchor (SNR 960148546) + wait for 8/8; then `anchor role all responder`.

## 8. Master BLE recovery, port resolution, capture = demo-ready (2026-07-19)

### 8.1 nRF5340 dual-core reset — a JLink reset does NOT recover a stuck master BLE
**Symptom:** a Master B120 sees the tags' advertising names (`MSTAT name=BSxxxx`) but
**cannot complete BLE connections** (`conn=0/3`, or `scan_running=0` with no discovery) —
while the tags are **fine** (still ranging, listeners hear them on-air).
**Cause:** the nRF5340 **NET core (the BLE controller) is stuck.** A **J-Link reset only
resets the APP core** (`jlink_reset_by_snr.sh … NRF5340_XXAA_APP` → `AIRCR.SYSRESETREQ`);
the NET core keeps its stuck state, so it can scan/see adv names but never connects.
**Fix: a FULL POWER CYCLE of the B120 (unplug + replug the USB).** Verified 2026-07-19:
two app-core JLink resets did NOT recover it; the USB power cycle did, immediately.
**Anchors (nRF52832, single core) are NOT affected — this is master-only.**

### 8.2 Two CDCs per master; console = the App CDC; always pass the App-CDC by-id
**Each B120 master enumerates TWO USB CDCs** (verified 2026-07-19, `ls -l /dev/serial/by-id`):
| | **App CDC = the console** (nRF5340's own USB, serial = FICR DEVICEID) | **J-Link OB VCOM** (SEGGER debug probe, serial = J-Link SNR) — **DO NOT OPEN** |
|---|---|---|
| Master_Tag | `usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00` | `usb-SEGGER_J-Link_001050070698-if00/if02` |
| Master_Anchor | `usb-Master_Anchor_Master_Anchor_Control_87EA2F4A526C5A02-if00` | `usb-SEGGER_J-Link_000960148546-if00/if02/if04` |

- **The console is on the App CDC** (`*Master_Tag_Control*` / `*Master_Anchor_Control*`) — proven:
  `status` there → `Control status: mode=RECV`. **Never send console commands to the J-Link VCOM
  (`SEGGER_J-Link_*`) — it won't respond, and opening it can DTR-reset the master** (→ drops the
  tags, → §8.1 recovery).
- A power cycle **renumbers** ttyACM (2026-07-19: the App CDC was `ttyACM0`, became `ttyACM2`; the
  J-Link VCOM took `ttyACM0`). So a hardcoded `ttyACM0` will land on the **J-Link VCOM after a power
  cycle** — opens fine, no response. **Always use the App-CDC by-id path, never a literal `ttyACM<n>`.**

**Which scripts resolve the master port correctly (empirically checked 2026-07-19):**
- ✅ `ota_preflight.py`, `release_all_tags.py` — carry the **correct** `*Master_Tag_Control*` /
  `*Master_Anchor_Control*` by-id globs → auto-resolve, **no `--port` needed.**
- ⚠️ `run_recv_tdma_capture.py`, `run_autopos_sweep_loop.py` — **MUST be given `--port` with the full
  App-CDC by-id.** Their auto-resolver (`master_control_port.preferred_master_control_port()`) is
  **stale** — its globs expect `*BioSpur_BLE_Control*` and match **0 devices** (returns `None`), so
  they fall back to `--port`. The capture's *default* `--port` is doubly wrong: stale name **and** it
  points at the **anchor's** SNR (`…87EA2F4A…`). Example that works:
  `--port /dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00`.
- ⚠️ `demo_start.py` **and the `experiments/three_tag_demo_readiness/*.py` scripts hardcode
  `/dev/ttyACM0`** — after a power cycle that is the **J-Link VCOM**, not the console, so they open
  the wrong device and get no response. Before running them, confirm `/dev/ttyACM0` is the App CDC
  (`ls -l /dev/serial/by-id | grep Master_Tag_Control`) or edit the port to the App-CDC by-id — OR
  just use `run_recv_tdma_capture.py` with an explicit `--port` (it's the confirmed demo-ready path).
- (`master_control_port.py`'s stale globs + the capture default are a documented robustness note,
  not fixed — code untouched by choice.)

### 8.3 The capture script alone is demo-ready — `demo_start.py` is NOT a prerequisite
Empirically confirmed 2026-07-19 (closes the previously code-only gap): `run_recv_tdma_capture.py`
run normally from a **clean free-run state**, WITHOUT `demo_start.py`, sets distinct-slot TDMA
itself (`tdma clear → roster motion → rebalance` → on-air `0xb102/03/04`, slots 2/3/5) and
delivers **ge7 97.8% / ge8 96.4%** over 2178 sweeps / 80 s, longest-below-floor 0.0 s, worst
1-s bin 97.7%, valid 97.6%, all 3 tags 0 dropouts / 100% span / balance 0.98, prewarm converged
in 1 attempt. `demo_start.py` is a convenience / recovery tool, not a prerequisite. Details:
`SS-TWR/alt-SS-TWR/broadcast/experiments/three_tag_demo_readiness/DEMO_READINESS.md`.

### 8.4 Do NOT run a capture on an artificial slow-slot/quiet state
If the rig was put into a slow-slot/quiet state (e.g. `CFG …PERIOD=9000` for silence),
**`cmd_all REBOOT` to free-run BEFORE running a capture.** Running the capture on top of the
artificial state — its clean-RECV step drops the tag links and from the near-silent state they
may not re-link (verified failure mode 2026-07-17: `link ready 0/3`, 0 rows captured).
