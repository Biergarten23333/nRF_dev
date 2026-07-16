# AutoPos Data-Plane Output Audit — the Anchor↔Anchor↔Master Path

**Date:** 2026-07-15 · **Type:** read-only survey + classification (no build/flash/delete)
**Completes the trio:** commands (`docs/COMMAND_REFERENCE.md`) · tag-ranging output (`experiments/declutter_audit/DECLUTTER_AUDIT.md`) · **AutoPos output (this doc).**
**Path convention:** `file:line` relative to the broadcast tree root `/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast`.
**Buckets & rules:** identical to the declutter audit — **KEEP** / **KEEP-DEBUG** (incl. tombstones) / **DELETE**; **R1** (DELETE needs proof of death: zero host parser/caller + no firmware counterpart + not in CMakeLists + git-superseded, not "never wired"; incomplete → KEEP-DEBUG), **R2** (falsified-experiment lesson → tombstone), **R3** (unparsed output → DELETE candidate unless reserved-for-fusion).

**The AutoPos data plane is a separate path from tag ranging:** anchor (matrix-master role) two-way-ranges every peer anchor → aggregates an inter-anchor distance matrix → publishes `SW-…` on its **BLE GATT RESULT characteristic** → Master_Anchor reads it and echoes `AUTOPOS result: SW-…` to its console → host captures, **solves the layout offline (V3-box)**, and pushes the result to tags as `APOS`. **No tag participates in the ranging; the master never solves.**

---

## 0. Decoder — every AutoPos-path line type

| token | meaning | emitted by | transport | verdict |
|---|---|---|---|---|
| **`SW-<M>,<p>,<raw>,<q>,…`** | Per-sweep-set inter-anchor matrix row: master `M`, then repeating `peer,last_raw_mm,quality%` (invalid pair → `,X,0,0`). One line = one sweep set (no sequence field). | `ss_twr_anchor_init.c:85-103` | **BLE GATT RESULT** (`:474`) + console mirror (`:111`) | **KEEP** (production AutoPos data) |
| **`SWEEP_DONE master=<M> sets=<N> role=matrix`** | Finite-sweep completion marker. | `ss_twr_anchor_init.c:481-488` | BLE GATT RESULT + console | **KEEP** (parsed: run_autopos_sweep_loop.py:2837) |
| **`Matrix <M>-<peer> addr=… raw=… last=… ok=… fail=… q=…%`** | Human-readable per-pair inter-anchor range. | `ss_twr_anchor_init.c:427` | anchor console (printk) | **KEEP-DEBUG** (verbose; machine form is `SW-`) |
| **`Anchor master ready anchor=<M>(id) addr=… peer_count=…`** | Anchor role-entry announcement (enters matrix-master). | `ss_twr_anchor_init.c:239` | anchor console | **KEEP** (role announce) |
| **`Anchor master reject/timeout …`**, **`… TX failed`**, **`unexpected frame`** | Per-pair failure / TX / frame diagnostics. | `ss_twr_anchor_init.c:405/449/282/293/354` | anchor console | **KEEP-DEBUG** |
| **`Anchor sweep <n> complete for <M>`** + matrix row/edges dump | Per-sweep completion + matrix state. | `ss_twr_anchor_init.c:467-472` | anchor console | **KEEP-DEBUG** |
| **`ACRX;1` / `ACIRM/ACIRD/ACIRE;1`** | Anchor compact-CIR features / full-CIR on the anchor↔anchor path. | `anchor_cir_output.c` via `ss_twr_anchor_init.c:436/440` | anchor console/BLE | **KEEP** (reserved fusion/imaging) |
| **`AUTOPOS result: <SW-…\|OK RUNTIME\|SWEEP_DONE>`** | Master echo of the anchor RESULT char — the SW- conduit the host parses. | `main.c:990/1023/1154` | Master console | **KEEP** |
| **`AUTOPOS: mode=… state=… staged=… last_success=… sets=… cir=… error=…`** | `autopos status` reply. | `main.c:499` | Master console | **KEEP** (parsed: run_autopos_sweep_loop.py:805) |
| **`AUTOPOS map <L>=<uuid>` / `map refresh`** | `autopos map show` / refresh. | `main.c:578/548` | Master console | **KEEP** / KEEP-DEBUG |
| **`AUTOPOS state: <state>`**, **`… sweep converge: master=… stable=…`**, **`… runtime accept …`**, **`… wait anchor ready/cleared …`**, **`… result history[…]`** | Progress / convergence / connection / history echoes. | `main.c:925/1161/1029/825-908/242` | Master console | **KEEP-DEBUG** |
| **`AUTOPOS round staged …` / `apply success …` / `finite sweep handoff …`** | Round-control acks. | `main.c` autopos handlers | Master console | **KEEP** |
| **`Launching AUTOPOS mode (internal receive active)`** | Session-entry announcement. | `main.c:3357` | Master console | **KEEP** |
| **`Boot profile anchor: … no tag scan` / `Control mode loaded: AUTOPOS`** | Boot-time role/mode announcement. | `main.c:446/3225` | Master console | **KEEP** (see §E) |
| **`APOS_OK` / `APOS_COMMIT_OK N=…` / `APOS_STATUS_DONE SRC=SETTINGS\|DEFAULT` / `APOS_FAIL`** | Tag write-back acks. | `uwb_tag_ble.c:1721/1735/1774` | tag NUS | **KEEP** (write-back contract) |

---

## Part A — Anchor outputs in matrix/AutoPos role

Responder-role anchors are silent; matrix/AutoPos-role anchors emit the following. **All console prints are gated by `!ss_twr_anchor_init_full_cir_quiet()`** — i.e. ON by default, suppressed only when `CIR=FULL` is active (the `full_cir_quiet` mechanism, AUDIT A11). The machine-readable data goes to the **BLE GATT RESULT characteristic**.

| output | grammar | transport / trigger | host parser | bucket | evidence |
|---|---|---|---|---|---|
| `SW-<M>,<p>,<raw_mm>,<q>,…` | master label + per-peer triplet (invalid→`,X,0,0`) | GATT RESULT (`:474`) + console `Anchor sweep summary prepared:` (`:111`); matrix role | run_autopos_sweep_loop.py:268 (`parse_sw_line_triplets`), master main.c:1073 | **KEEP** | production inter-anchor data |
| `SWEEP_DONE master=<M> sets=<N> role=matrix` | finite-sweep completion | GATT RESULT (`:488`); `RUNTIME MASTER SWEEP <n>` | run_autopos_sweep_loop.py:2837 | **KEEP** | finite-round handoff marker |
| `Anchor master ready anchor=<M>(id) addr=… peer_count=…` | role-entry banner | console (`:239`); on matrix-master start | — (operator-visible) | **KEEP** | role announcement (§E) |
| `Matrix <M>-<peer> addr=… raw=… last=… ok=… fail=… q=…%` | human per-pair range | console (`:427`) | — | **KEEP-DEBUG** | verbose; SW- is the machine form |
| `Anchor master reject … / timeout … / TX buffer/start failed / unexpected frame / stop requested` | per-pair + control diagnostics | console (`:405/449/282/293/354/266/307`) | — | **KEEP-DEBUG** | bug-chasing during AutoPos |
| `Anchor sweep <n> complete for <M>` + `matrix_print_row` + `matrix_print_valid_edges` | sweep completion + matrix dump | console (`:467-472`) | — | **KEEP-DEBUG** | per-sweep progress |
| `Anchor master finite sweep limit/complete …` | finite-sweep bookkeeping | console (`:247/486`) | — | **KEEP-DEBUG** | |
| `ACRX;1;…` | compact CIR features (anchor↔anchor) | `anchor_cir_output_publish_feature` (`:436`); `CIR_FEATURE_OUTPUT`=0 | cir_features_to_pair_weights.py:15 | **KEEP** (reserved fusion) | off in prod; consumed by pair-weights |
| `ACIRM/ACIRD/ACIRE;1` | full CIR dump (anchor↔anchor) | `anchor_cir_output_publish_full` (`:440`); `CIR_FULL_OUTPUT`=0 | out-of-tree `cir_full_usb_capture.py` | **KEEP** (reserved imaging) | off in prod |
| (`dwt_readdiagnostics` read) | — | A13 freeze gate: only reads when `anchor_cir_output_get_mode()!=OFF` (`:372-374`) | — | **KEEP** | the A13 gate keeps the diag read off the production path |

**Part A finding:** the anchor↔anchor path emits the same CIR family as tag ranging (`ACRX`/`ACIRM…`, not `;D1`), gated OFF in production. The only production output is `SW-`/`SWEEP_DONE` on GATT RESULT. All console diagnostics are useful-during-AutoPos KEEP-DEBUG, correctly suppressed under `CIR=FULL`.

---

## Part B — Master_Anchor console outputs during AutoPos

The Master_Anchor reads the anchor GATT RESULT/STATE chars (`master_anchor_ctrl_read_result`/`_read_state`) and echoes them to its console, where host scripts parse them. **It performs no layout solve** — it stages/applies rounds and forwards SW-; convergence is a purely structural check (`autopos_sweep_line_converged`, main.c:1056-1136).

| output | grammar / when | host parser | bucket | evidence |
|---|---|---|---|---|
| `AUTOPOS result: <SW-…\|OK RUNTIME\|SWEEP_DONE>` | echo of anchor RESULT, on read | host parses embedded `SW-`/`SWEEP_DONE` from master console | **KEEP** | main.c:990/1023/1154 — the SW- conduit |
| `AUTOPOS: mode=<m> state=<s> staged=<c> last_success=<c> sets=<n> cir=<m> error=<e>` | `autopos status` reply | run_autopos_sweep_loop.py:805/1019 | **KEEP** | main.c:499 |
| `AUTOPOS map <L>=<uuid>` | `autopos map show` | (operator/verify) | **KEEP** | main.c:578 |
| `AUTOPOS round staged: master=<M> sets=<n>` / `AUTOPOS apply success: master=<M>` / finite-handoff | round control acks | run_autopos_round.py / sweep_loop | **KEEP** | main.c autopos handlers (`:2955` area) |
| `Launching AUTOPOS mode (internal receive active)` | session entry | (operator) | **KEEP** | main.c:3357 |
| `Boot profile anchor: … no tag scan` / `Control mode loaded: AUTOPOS` | boot | ota_* scripts parse `Control mode loaded` | **KEEP** | main.c:446/3225 |
| `AUTOPOS state: <state>` | echo of anchor STATE | (progress) | **KEEP-DEBUG** | main.c:925/1038/1226 |
| `AUTOPOS sweep converge: master=<M> stable=<k>/<K> peers=<n> min_q=<q>` | convergence progress | (progress) | **KEEP-DEBUG** | main.c:1161 |
| `AUTOPOS runtime accept via sweep stream/role state: master=<M>` | runtime-role acceptance | (progress) | **KEEP-DEBUG** | main.c:1029/1042 |
| `AUTOPOS wait anchor ready/cleared … waited=… ready_count=…` | connection-wait diagnostics | (progress) | **KEEP-DEBUG** | main.c:825-908 |
| `AUTOPOS result history[<i>/<n>]: <line>` / `… history empty` | `autopos result show` | run_autopos_sweep_loop.py:2899 (recovery) | **KEEP-DEBUG** | main.c:242/229 |
| `AUTOPOS map refresh: <L>=<uuid>` | map load | — | **KEEP-DEBUG** | main.c:548 |
| `AUTOPOS cmds: …` | help | — | **KEEP** | main.c:349 |
| error/timeout: `AUTOPOS wait anchor ready timeout`, `wait anchor cleared timeout` | anchor non-response | (fail path) | **KEEP-DEBUG** | main.c:857/877/908 |

---

## Part C — the anchor↔anchor ranging frame

**Same frame format as tag ranging.** The anchor-initiator builds its poll with the shared builder `uwb_ss_twr_build_poll_frame()` (ss_twr_anchor_init.c:272) and matches responses with `uwb_ss_twr_resp_matches()` (`:349`) — the identical `uwb_ss_twr_shared.c` frame the tag uses. The only differences are addressing (src = anchor addr, dst = peer-anchor addr, `:274-275`) and role (anchor is initiator instead of tag). The response carries the standard SS-TWR timestamps (`poll_rx_ts` at `RESP_MSG_POLL_RX_TS_IDX`, `resp_tx_ts` at `RESP_MSG_RESP_TX_TS_IDX`, `:380-387`), and TOF uses the same clock-offset-corrected math (`:389-393`).

| item | bucket | evidence |
|---|---|---|
| Anchor↔anchor poll/resp frame (shared SS-TWR) | **KEEP** | ss_twr_anchor_init.c:272/349; shared `uwb_ss_twr_shared.c` |
| `rank_offset` byte (packed in tag_id field) | **KEEP** | 0 in production (declutter T8/A4); byte-identical to 5.28; only CIR-compact rotates it |

**No AutoPos-specific vestigial frame fields** — the path reuses the frozen production frame, so there is nothing to delete here.

---

## Part D — AutoPos result write-back path (host solves, master forwards)

**Confirmed: the master never solves.** No anchor-layout solver exists in `src/` or `apps/` — the only firmware `solve` is tag-side multilateration (`uwb_tag_loc.c:539`) that *consumes* the pushed layout. The Master_Anchor aggregates `SW-` and forwards; the **host solves offline** and pushes `APOS`.

**Canonical chain (current):**
`run_autopos_sweep_loop.py` (drive round, capture SW-) → `autopos_extract_pairs_from_sweep_summary.py` (→ `pairs_all.csv`) → `fuse_bidirectional_matrix_v3.py` (→ `inter_anchor_matrix_v3fused.json`) → `solve_anchor_layout_v3_full.py --geometry-mode box` via `prepare_autopos_v3_box.py` (→ `anchor_layout_v3_box.json`) → `push_apos_layout_verified.py` → tag NVS. End-to-end wrapper: `run_autopos_sweep_and_solve_v3_box.py`.

| stage | script/firmware (file:line) | emits/parses | bucket | evidence |
|---|---|---|---|---|
| round driver (canonical) | run_autopos_sweep_loop.py (docstring `:3` "Recommended … entrypoint") | `autopos map/cir/round/apply/status`, `anchor role/reset`; parses SW- (`:268`) | **KEEP** | canonical |
| round driver (simple) | run_autopos_round.py:80-89 | `mode autopos`, `autopos map/round/apply/status`; raw-log only | **KEEP-DEBUG** | superseded-for-solving, not dead |
| pairs extractor | autopos_extract_pairs_from_sweep_summary.py:10 | SW- → `pairs_all.csv` | **KEEP** | 2nd SW- parser |
| V3 fusion | fuse_bidirectional_matrix_v3.py:200 | pairs → fused matrix (MVUE + robust) | **KEEP** | current fusion |
| V3 solver core | solve_anchor_layout_v3_full.py:807 | matrix → layout (SDP seed + antenna-delay bias + Tukey IRLS) | **KEEP** | current solver |
| V3-box prepare + e2e | prepare_autopos_v3_box.py:105 / run_autopos_sweep_and_solve_v3_box.py:124 | → `anchor_layout_v3_box.json` | **KEEP** | canonical ship path |
| APOS write-back + verify | push_apos_layout_verified.py:230/334/344/267 | `APOS_TO <tag> APOS/APOS_COMMIT/APOS_STATUS`; parses `APOS_OK`/`APOS_COMMIT_OK N=8`/`APOS_STATUS_DONE SRC=SETTINGS` | **KEEP** | canonical push+verify |
| tag APOS handler + NVS | uwb_tag_ble.c:1688-1774; uwb_anchor_layout.c:145-154 | APOS verbs → `settings_save_one("anchor_layout/runtime")` | **KEEP** | write-back target |
| layout dump (diag) | autopos_dump_anchor_layouts.py:86 | layout json → markdown; sends nothing | **KEEP-DEBUG** | read-only diag |
| V1/V2 fusion, iterative & least-squares solvers, v2/v3-lite prepares | fuse_bidirectional_matrix_v1.py/_v2.py, solve_anchor_layout_iterative.py, solve_anchor_layout.py, prepare_autopos_v2.py/_v3_lite.py | superseded by v3; live only inside compare harnesses | **KEEP-DEBUG** | superseded-but-kept (R1: no hard-stop marker) |
| v3-free / v3-full prepares | prepare_autopos_v3_free.py/_v3_full.py | geometry variants of same v3 core | **KEEP-DEBUG** | not superseded — geometry modes |
| compare/overnight harnesses | run_autopos_capture_once_and_solve_v1_v2_v3.py, run_autopos_vx_capture_and_solve.py, run_autopos_solve_…_from_existing.py, run_autopos_v1_v2_v3_overnight.sh | run v1/v2/v3 side-by-side | **KEEP-DEBUG** | research, not ship |
| **deprecated hard-stub** `recalibrate_anchor_layout_with_ref115.py` | `:2-5` `raise SystemExit("… retired")` | nothing | **DELETE** | proven dead (hard raise) |
| **deprecated hard-stub** `run_autopos_sweep_then_tag_cm_loop.py` | `:2` deprecation stub | nothing | **DELETE** | proven dead |
| deprecated stub `run_anchor_responder_then_tag_cm.py` | `:1-19` prints "deprecated", returns 2 | nothing | **KEEP-DEBUG** | **still referenced** by run_autopos_capture_once_and_solve_v1_v2_v3.py:144 + run_autopos_sweep_and_solve_v3_box.py:155 → deleting breaks imports (R1 fails) |

---

## Part E — cross-check against the 2026-07-15 incident

**State surface (correcting the prompt).** `control_mode` is **not GPREGRET** — it is a `__noinit` RAM cookie: `control_boot_mode`/`control_boot_cookie` (main.c:79-80), restored by `control_load_mode()` iff `cookie==CONTROL_BOOT_COOKIE_MAGIC` (main.c:3215-3217), else default RECV; then overridden by the compile-time `control_apply_boot_profile()` (main.c:435, called `:3337`). `__noinit` survives a **warm** `sys_reboot` but is cleared by a **power cycle**; it is never in flash (no `settings_save()` for mode — only `autopos_target`/`autopos_map_*` via `settings_save_one`, main.c:679/692). This matches the OTA-blocker Q3 finding exactly: **a zombie AUTOPOS boot comes from the boot profile / warm cookie, not flash — a full erase does not fix it; the right profile + a power cycle does.**

**Would a boot announcement have made the incident obvious?** Partly. The master already prints, at boot: `Boot profile <anchor|tag|neutral>: …` (main.c:446/460/464) and `Control mode loaded: <RECV|AUTOPOS|OTA>` (main.c:3225), plus `Launching AUTOPOS mode` (main.c:3357) on AUTOPOS entry. **But the information is split and does not spell out the tag-grab consequence.** A Master_Anchor that boots RECV (neutral/tag profile, or a warm cookie) prints `Boot profile neutral: no role-specific auto target` + `Control mode loaded: RECV` — neither line screams "I will connect and HOLD all BS* wand tags, and they will stop advertising." The anchor-profile line does say `no tag scan`, but only if the `anchor` profile was actually compiled in (the incident's root cause was that it was not / a warm cookie overrode it).

**Recommendation (KEEP add — additive, low-risk):** a single loud boot banner that states mode + profile + target-kind + the tag-grab consequence, printed unconditionally right after `control_load_mode()`:
```
=== MASTER BOOT: profile=<neutral|tag|anchor> mode=<RECV|AUTOPOS|OTA> target=<TAG|ANCHOR|NONE>
    wand tags: <WILL HOLD BS* — they will NOT advertise while connected | rejected (anchor target)>  ===
```
This turns the exact incident condition ("a master silently holding the tags") into a one-line, operator-obvious announcement. It is a KEEP add, not a cleanup; it belongs with the OTA-blocker preflight (`experiments/ota_blocker_audit/`).

---

## AutoPos Production Output Contract v0

**Normal AutoPos run**, all debug CIR OFF (`CIR_FEATURE/FULL=0`, runtime `autopos cir 0`):

**Anchor (matrix-master) → BLE GATT RESULT characteristic** (the machine-readable plane the host consumes via the master):
```
SW-<M>,<peer1>,<raw1_mm>,<q1>,<peer2>,<raw2_mm>,<q2>,…      # one line per sweep set; invalid pair -> <peer>,0,0
SWEEP_DONE master=<M> sets=<N> role=matrix                   # only for a finite RUNTIME MASTER SWEEP <N>
```
`SW-` field-by-field:

| position | field | type | meaning |
|---|---|---|---|
| 1 | `SW-<M>` | literal+char | master anchor label (A–H) |
| 2,3,4 … | `<peer>,<raw_mm>,<q>` (repeating) | char,u32,u8 | peer label, last raw inter-anchor distance (mm; 0 if invalid), quality % (0 if invalid) |

No per-line sequence number — **one `SW-` line = one completed sweep set**; the round count is carried by `sets=` in `SWEEP_DONE` and by `autopos status` `sets=`.

**Master_Anchor console** (what host scripts parse): `AUTOPOS result: <SW-…|SWEEP_DONE …>`, `AUTOPOS: mode=AUTOPOS state=…` (status), `AUTOPOS sweep converge: …` (progress). **Anchor console** additionally prints human-readable `Matrix <M>-<peer> … raw=… q=…%` (KEEP-DEBUG).

**When debug is enabled:** `autopos cir compact` → `ACRX;1` per pair; `autopos cir full` → `ACIRM/ACIRD/ACIRE;1` (and the A13 `dwt_readdiagnostics` read switches on). The solved layout is host-side (V3-box) and reaches tags as `APOS`/`APOS_COMMIT` → NVS `anchor_layout/runtime`, acked by `APOS_OK`/`APOS_COMMIT_OK N=8`/`APOS_STATUS_DONE SRC=SETTINGS`.

**Version note:** unlike the tag `TR;` line, `SW-` carries **no version field**. If its grammar ever changes, add a `SW-2;`-style version (currently parsers key on the `SW-<label>,` prefix + triplet structure — run_autopos_sweep_loop.py:268, main.c:1073).

---

## DELETE candidates & ambiguous (for operator ruling)

**DELETE (proven dead, R1 satisfied):**
- `scripts/recalibrate_anchor_layout_with_ref115.py` — hard `raise SystemExit("… retired")` (`:2-5`); zero live callers.
- `scripts/run_autopos_sweep_then_tag_cm_loop.py` — deprecation hard-stub (`:2`); zero live callers.

**No firmware output is DELETE-eligible on this path** — `SW-`/`SWEEP_DONE` are parsed and production; `Matrix/reject/timeout` console prints are `full_cir_quiet`-gated KEEP-DEBUG; `ACRX/ACIRM` are reserved-for-fusion.

**Ambiguous — present both readings:**
1. **`autopos detach`** — firmware command exists (main.c:349) but **no host script sends it** (sweep_loop uses `autopos result show` + `mode` transitions instead). Reading A: KEEP (valid manual operator verb). Reading B: prune the handler as unused. Recommend **KEEP** (harmless manual verb; R1 bias).
2. **`solve_anchor_layout.py` (least-squares)** — no deprecation marker, not called by any current driver. KEEP-DEBUG (superseded-but-kept) vs DELETE. Recommend **KEEP-DEBUG** (R1: unproven dead).
3. **`run_anchor_responder_then_tag_cm.py`** — deprecated stub but referenced by two live compare drivers under `--capture-tag115`; those Tag115 paths are dead-on-invoke. KEEP-DEBUG (referenced) vs fix-callers-then-DELETE. Recommend **KEEP-DEBUG** until the two callers are cleaned.

---

## Batched cleanup (merges with the declutter audit's batches — do NOT execute)

Same rollback point (`freeze-4piece-<date>`), one commit per batch. This audit adds to the declutter plan:

- **Batch 1 (dead code)** — ADD: DELETE `scripts/recalibrate_anchor_layout_with_ref115.py`, `scripts/run_autopos_sweep_then_tag_cm_loop.py` (hard-stub stubs; zero callers). *Verify:* `grep` confirms no live import/subprocess. (`run_anchor_responder_then_tag_cm.py` stays — referenced.)
- **Batch 5 (NEW — AutoPos host consolidation, optional/low-risk, offline-only)** — the superseded v1/v2/iterative/least-squares solvers + v2/v3-lite prepares are **KEEP-DEBUG**, not deleted (compare harnesses depend on them). Optional: move them under `scripts/autopos_legacy/` for clarity — a *reorganisation*, not a deletion; operator decision.
- **Batch 6 (NEW — KEEP add, not cleanup)** — add the loud boot role banner (§E). Additive; verify boot log shows the banner and `TR;2`/AutoPos flows unchanged.

**Tombstones (keep):** none new on this path — the AutoPos code carries no falsified-experiment residue (unlike RXAUTR/dblbuf on the tag path).

---

## Trio status — deployment contract complete

The three deployment surfaces are now fully audited:

| surface | document | covers |
|---|---|---|
| **Commands** | `docs/COMMAND_REFERENCE.md` | every runtime command (TAG/ANCHOR/MASTER/LISTENER/HOST) + cross-check |
| **Tag-ranging output** | `experiments/declutter_audit/DECLUTTER_AUDIT.md` | TR/TP/;D1/;T/CIR/CRX + command buckets + production TR;2 contract |
| **AutoPos output** | `experiments/autopos_output_audit/AUTOPOS_OUTPUT_AUDIT.md` (this) | SW-/SWEEP_DONE/Matrix/ACRX + master AUTOPOS lines + APOS write-back + SW- contract |

Adjacent, already-covered surfaces (not a fourth gap): the **OTA/DFU** surface is in `experiments/ota_blocker_audit/` + `COMMAND_REFERENCE.md §3b`; the **listener** telemetry (`LPD/LRD/LCIR*/LSTAT/LTAG`) is bucketed in `DECLUTTER_AUDIT.md §2 LISTENER`. **No genuinely new fourth surface was discovered** — the three data planes (command / tag-ranging / anchor-AutoPos) plus the OTA control plane account for every runtime input and output in the system.

*Read-only survey. No code changed, nothing built, flashed, or deleted. Machine-readable buckets: `autopos_output_audit.json`.*
