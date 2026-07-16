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
