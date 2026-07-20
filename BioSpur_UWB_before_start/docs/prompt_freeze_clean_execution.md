# PROMPT: freeze-clean — Execute the Six-Batch Cleanup on top of freeze-4piece-20260715

## CONTEXT

Repo: `/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start`
Tree: `SS-TWR/alt-SS-TWR/broadcast`
Branch: `feature/wand-internal-sweep`

Rollback anchor: **`freeze-4piece-20260715`** (commit cb8603316) — the
verified functional baseline. This prompt produces `freeze-clean` on top
of it.

This is EXECUTION, not audit. The audits are done and the operator
has ruled on every item:
  - `docs/COMMAND_REFERENCE.md`
  - `experiments/declutter_audit/DECLUTTER_AUDIT.md`
  - `experiments/autopos_output_audit/AUTOPOS_OUTPUT_AUDIT.md`
  - `experiments/ota_blocker_audit/OTA_BLOCKER_REPORT.md`
  - `experiments/firmware_freeze_audit/AUDIT.md`
  - `experiments/listener_freeze_audit/LISTENER_FREEZE.md`

## LISTENER FLEET STATE — RECORD ONLY, NOT part of tonight's execution

The listener fleet is ALREADY flashed and frozen separately as
`listener-freeze-20260715` (9 units, single common image, CIR=1, USB
J-Link, PANS-cleared, passive-confirmed). **The listeners are NOT part
of tonight's freeze-clean** — freeze-clean is the FOUR-PIECE cleanup
(tag/anchor/master_tag/master_anchor). The listeners are purely passive
USB devices (no BLE competition, no UWB airtime) and do not interfere
with any batch below. Analysis of their data is TOMORROW's task.

Two DOC-ONLY tasks for this fleet (write into `docs/DEPLOYMENT.md`,
no code, no flash — just record):

  (i) **Fleet snapshot** — the 9-unit SNR / position / height / origin
      map (EXCLUDED: SNR 760185886 = legacy Geiger, never flashed):
      - 760184753 · A-E anchor-pair midpoint · mid
      - 760184548 · B-F anchor-pair midpoint · mid
      - 760181725 · C-G anchor-pair midpoint · mid
      - 760184784 · D-H anchor-pair midpoint · mid
      - 760184964 · vertical-profile LOW · low
      - 760184767 · vertical-profile MID · mid
      - 760184545 · vertical-profile HIGH ~2.3m · high
      - 760181879 · AEDH face, between E-H, UPPER anchor-layer height · upper
      - 760186115 · BFCG face, between B-C, LOWER anchor-layer height ·
        lower · = BSF66F (repurposed tag, temporary passive, label kept,
        historical value preserved, tag-active behavior confirmed OFF)
      (Operator will confirm which SNRs are new DWM1001C / PANS-cleared;
      mark origin accordingly.)

  (ii) **Anchor-layer naming convention** — write into DEPLOYMENT.md so
      nobody (including future automated agents) infers height backwards
      from an anchor name:
      - **ABCD = LOWER anchor layer; EFGH = UPPER anchor layer.**
      - Any listener height description MUST reference an anchor pair in
        the SAME layer as its actual height: an UPPER-height listener is
        "between E-H" (upper), NEVER "between A-D" (reads as lower). A
        LOWER-height listener references an ABCD pair.
      - Every listener position carries: physical face (AEDH/BFCG),
        height layer (upper/lower/mid/low), same-layer anchor reference.
      Correct the two sentinels in LISTENER_FREEZE.md to match:
        upper → "AEDH face, between E-H, UPPER" (760181879)
        lower → "BFCG face, between B-C, LOWER" (760186115)

These two are documentation only — they do NOT gate or block any
firmware batch below and require no build/flash.

## IRON DISCIPLINE (violating this fails the task)

- **One git commit per batch.** Each batch is individually revertible.
- **After EVERY batch that touches firmware: build + OTA + 60s verify
  ge7 ≥ 0.97, ge8 ≥ 0.90, valid% ≥ 96, production TR still emits.**
  If ANY metric regresses → `git reset --hard freeze-4piece-20260715`,
  report which batch broke it, STOP.
- **Host-script-only batches** (2, 5, parts of 6): no OTA needed, but
  run the affected script's dry path to confirm it still works.
- **Do NOT touch frozen four-piece CORE logic** (TDMA/ranging/solver
  math). Scope = dead code, orphan commands, output labeling, host
  scripts, build-time guards, docs.
- **NEVER delete anything marked KEEP or TOMBSTONE below**, even if it
  looks unused. Deletion is allowed ONLY for the explicit DELETE items.
- Read `FREEZE_4PIECE_20260715.md`, `.protec`, `PROXY_DIAGON_*` before
  any flash.
- Fully authorized: Master_Tag (1050070698) + Master_Anchor
  (960148546, protected) flash + OTA all 11 units.

========================================================================
BATCH 1 — Dead code deletion (zero runtime impact)
========================================================================
DELETE:
  - `apps/master/src/master_app.c` (never in any CMakeLists; superseded
    by master_multi_app.c)
  - `scripts/recalibrate_anchor_layout_with_ref115.py` (hard
    `raise SystemExit`, zero live callers)
  - `scripts/run_autopos_sweep_then_tag_cm_loop.py` (deprecation
    hard-stub, zero callers)
KEEP (do NOT delete):
  - `src/uwb_control_proto.c` + `.h` — reserved BLE-removal skeleton,
    operator keeps the tail. Leave untouched.
VERIFY: grep confirms no #include/import/subprocess references to the
  deleted files; all four firmware pieces build byte-identical (no
  firmware change → OTA not required this batch, but build must pass).
COMMIT: "freeze-clean batch1: remove never-compiled dead code"

========================================================================
BATCH 2 — Host orphan senders (firmware no-ops today)
========================================================================
REMOVE (host scripts only):
  - `cmd_all MODE AOTA` at `run_recv_tdma_capture.py:2415` and `:2454`
    (AOTA removed from tag; returns MODE_BAD)
  - `cmd STREAM OFF` / `STREAM 0` / `STREAMON 0` at
    `run_autopos_sweep_loop.py:2405-2407` and the quarantine path
    (no tag handler → UNKNOWN_CMD)
IMPORTANT: these scripts currently FALL BACK to persistent `MODE IDLE`
  after the orphan fails. Do NOT leave the MODE IDLE fallback either —
  replace the whole stop path with the live-CFG stop per Batch 6's
  doctrine (CFG_STOP, not persistent MODE IDLE). If Batch 6 isn't done
  yet, at minimum remove the orphan senders and leave a TODO marker
  pointing to Batch 6.
VERIFY: capture start/stop still works (dry run); tags stop correctly.
COMMIT: "freeze-clean batch2: remove orphan host senders"

========================================================================
BATCH 3 — Orphan firmware handlers
========================================================================
DELETE (tag firmware):
  - `MMOT` handler (uwb_tag_ble.c:1842/1851) — footgun
    (`MMOT<suffix>` misparse) + exact duplicate of `MODE RUN`, zero
    senders
KEEP-DEBUG (do NOT delete — R1 unproven-dead):
  - `TDMA_STATUS` (uwb_tag_ble.c:1585) — orphan but harmless read-only;
    keep as inert.
VERIFY: build + OTA tag + 60s ge7 unchanged.
COMMIT: "freeze-clean batch3: remove MMOT footgun handler"

========================================================================
BATCH 4 — Output contract hardening
========================================================================
The operator ruled: **TR;2 stays as-is** (do NOT rename to TR/TR;1 —
renaming touches every host parser + breaks historical-log semantics
for zero benefit; the "missing TR;1" is an accepted engineering scar).

DO:
  4a. Gate `TP;1` OFF by default:
      `SS_TWR_INIT_PHASE_TELEMETRY_ENABLE 1→0` (ss_twr_init.c:3575).
      **Keep the code** (KEEP-DEBUG); only flip the default. This
      removes the only unparsed line shipping in production.
  4b. Resolve the `TR;3` overload: the legacy `#else` TR path
      (ss_twr_init.c:1338, non-production since V2=1 always ships) is
      renumbered OUT of `TR;3` (e.g. `TR;13`) or retired, so `TR;3`
      unambiguously = "production TR;2 + compact RF-diag". Production
      TR;2 branch is UNTOUCHED.
  4c. Version policy (frozen, write to contract doc): production TR line
      is TR;2; any future production field change bumps to `TR;5` (skip
      3/4 which are historically loaded). Document this.
  4d. Clean TR;2 rebuild + re-OTA (the deferred v1 item): the frozen
      image currently emits `TR;3` with an all-zero `;D1` trailer when
      DIAG is off. Rebuild so production (DIAG off) emits literal `TR;2`
      with NO `;D1` trailer. Re-OTA the 3 tags. This delivers the
      clean-TR;2 gate that v1 deferred.
  4e. DELETE vestigial `"BS;"` from the bundle-candidate strstr set
      (uwb_tag_ble.c:1052) — never emitted, superseded by `TS;`.
  4f. `SW-` version policy (AutoPos): `SW-` currently carries NO version
      field. Document in the contract that any future `SW-` grammar
      change adds `SW-2;`. (Doc only this batch — no code change to the
      emitter unless trivial to add a reserved version token safely;
      if not trivial, doc-only.)
KEEP-DEBUG (do NOT delete):
  - `BSTAT` (uwb_tag_ble.c:566) — inert, compile-gated OFF; leave.
VERIFY: after 4a-4e OTA, 60s capture emits literal `TR;2` + `;T`
  trailer, NO `;D1`, NO `TP`, ge7 unchanged. Host parser
  `run_recv_tdma_capture.py` still matches.
COMMIT: "freeze-clean batch4: output contract — clean TR;2, TP off,
  TR;3 de-overload, drop BS;"

========================================================================
BATCH 5 — AutoPos host reorg (optional, no deletion)
========================================================================
REORGANIZE (move, don't delete):
  - Move superseded AutoPos solvers (v1/v2/iterative/least-squares,
    v2/v3-lite prepares) under `scripts/autopos_legacy/`. They stay
    KEEP-DEBUG (compare harnesses depend on them) — this is tidying,
    not removal. Update any imports that reference their old paths.
KEEP (do NOT delete):
  - `autopos detach` firmware verb (harmless manual escape verb — in
    fact useful for the Batch 6 escape hatch)
  - `solve_anchor_layout.py`, `run_anchor_responder_then_tag_cm.py`
    (referenced / unproven-dead)
VERIFY: the compare harnesses still import the moved solvers.
COMMIT: "freeze-clean batch5: move legacy autopos solvers to
  autopos_legacy/"
(If the reorg risks breaking imports and the value is marginal, SKIP
this batch and note it — it's the lowest priority.)

========================================================================
BATCH 6 — Anti-recurrence infrastructure (adds + guards, treat as core)
========================================================================
This batch is the point of the whole freeze-clean: make the incidents
that cost hours STRUCTURALLY impossible, not just documented.

6a. **Loud boot banner** (AutoPos audit §E) — master prints
    unconditionally right after control_load_mode():
    `=== MASTER BOOT: profile=<..> mode=<..> target=<TAG|ANCHOR|NONE>
        wand tags: <WILL HOLD BS* / rejected> ===`
    Turns "a master silently holding the tags" into a one-line obvious
    announcement.

6b. **Compile-time assertions — do NOT rely on documentation.**
    Add three build-time guards so the exact incidents become
    "won't compile":
    (i)  Master carrier with NO explicit `APP_MASTER_BOOT_PROFILE`
         (i.e. neutral fallback) → `#error "Master carrier must set
         APP_MASTER_BOOT_PROFILE=anchor|tag; neutral is a build error"`.
    (ii) DIAG hot-path flag (`APP_TAG_RF_DIAG_TAG_RX_ENABLE`) defaulting
         ON → `#error` / static_assert forcing default OFF. This is the
         flag that caused the ge7=0 regression.
    (iii) The fixeda19 flag combination — a static_assert catching the
         fatal combo (tag DIAG on + anchor deferred-diag flags off) that
         collapsed ranging on 2026-07-14.
    Place each guard adjacent to the flag it protects, with a comment
    citing the incident date.

6c. **MODE/CFG persistence doctrine + capture exit contract.**
    - Document (and enforce where possible) the rule: transient/debug
      state uses LIVE commands (`CFG_RUN`/`CFG_STOP`, live-only, not
      persisted); persistent `MODE <..>` (writes NVS `tag_ble/runtime_cfg`)
      is ONLY for deliberately changing the production default.
    - Rewrite EVERY capture script's exit path (normal, Ctrl-C, crash)
      to leave tags in RUN + advertising, stopping via LIVE `CFG_STOP`
      — NEVER persistent `MODE IDLE`. This is the true root fix for the
      OTA-blocker chain (a crashed capture must not leave tags in a
      persistent stopped state).
    - Produce a "persistent vs live command" decision table in the
      deployment doc: for each state-changing command, mark writes-NVS
      vs live-only, and when to use which.

6d. **Escape hatch script** `scripts/release_all_tags.py` — the atomic
    "release all tags, stop auto-connect, verify they re-advertise,
    hold the master from re-grabbing" sequence (OTA-blocker Q6). Uses
    existing commands (stop auto-connect → disconnect → verify
    re-advertise). This is the universal unstick for any future
    OTA lock.

6e. **OTA preflight script/checklist** — bake the preflight into
    `ota_deploy.py` (or a preflight module it calls): enumerate both
    masters' connection state → if a master holds target tags,
    release via 6d → check no active capture (TR streaming) → confirm
    targets advertising → then deploy. On any unmet condition, report
    the reason + next step, don't hang.

6f. **Correct the five firmware laws** in `FREEZE_4PIECE_20260715.md`
    (and the freeze-clean doc):
    - Law "flash master = full erase" → "explicit BOOT_PROFILE +
      power-cycle restart; erase is NOT required to clear control_mode
      (it's a GPREGRET warm-cookie, not flash — OTA-blocker Q3)."
    - BUT preserve the separately-true nRF5340 fact CC found: a plain
      erase does not leave flash persistent on nRF5340 — document the
      real mechanism clearly so the next freeze doesn't re-derive it
      wrong. State both facts distinctly.

VERIFY: 6a boot banner appears in boot log; 6b guards — deliberately
  try a neutral-profile build and confirm it FAILS to compile (then
  revert to correct build); 6c capture exit leaves tags advertising
  (test a Ctrl-C mid-capture → tag still advertises → OTA works); 6d
  release script unsticks a held tag; full 60s ge7 unchanged.
COMMIT: "freeze-clean batch6: anti-recurrence — boot banner,
  compile-time guards, live-stop exit contract, escape hatch, preflight,
  corrected laws"

========================================================================
FINALIZE
========================================================================
- Consolidate the deployment contract into ONE authoritative doc
  `docs/DEPLOYMENT.md`: OTA preflight checklist, escape hatch, the
  persistent-vs-live command table, the corrected five laws, the three
  output contracts (command / TR;2 / SW-), the boot-banner meaning.
- After all batches pass: build clean images, final 60s verification
  (ge7 ≥ 0.97, literal TR;2, no TP, boot banner present, neutral build
  fails to compile), OTA all 11 units, git commit + tag
  `freeze-clean-<date>` with an annotated message referencing
  freeze-4piece-20260715 as parent.
- Update `BIOSPUR_USABLE_FIRMWARE_VERSIONS.md`.
- Report: per-batch commit hashes, per-batch ge7, the final tag,
  DEPLOYMENT.md path, and confirmation that a neutral-profile build now
  fails to compile.

## CONSTRAINTS RECAP
- Per-batch commit + ge7 gate; regression → hard reset to
  freeze-4piece-20260715, stop, report.
- Never delete KEEP/TOMBSTONE items.
- No core TDMA/ranging/solver logic changes.
- Compile-time guards are NOT optional and NOT replaceable by docs.
- Batch 5 is skippable if import-risk outweighs value; everything else
  is required.
