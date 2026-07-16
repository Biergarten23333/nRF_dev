# SCRIPTS_GUIDE — 2026-07-15-FREEZE

How to operate the frozen system: **flash → OTA → capture → AutoPos → position**.
Two script groups under `scripts/`:
- `scripts/ops/` — operational scripts (flash / OTA / capture / AutoPos / escape hatch / parse)
- `scripts/solvers/` — positioning solvers (the two-stage layout→position chain)

**RUN caveat (like the solver `[REPOINT]` flags):** the ops scripts are copied **verbatim**
from the live tree `SS-TWR/alt-SS-TWR/broadcast/scripts/`. They assume that tree's context —
firmware build dirs (`build-*/…/merged.hex`), an optional `.protec/biospur_ports.env` port map,
and (for one optional CIR path) `flutter_ui_autopos/`. To actually run them, invoke from the
live `broadcast/scripts/` checkout of tag `freeze-clean-20260716`, or repoint those references.
This `ops/` copy is the authoritative frozen inventory + the reference for what to run.
Port safety: **never `cat` an nRF CDC** (DTR-reset); the scripts use pyserial `dtr=False`.
Full laws + tables: `../docs/DEPLOYMENT.md` (§3 laws, §4 command table, §5 preflight, §7 anchor OTA).

---

# Operational scripts (`scripts/ops/`)

## 1. Flash firmware (JLink)
| Task | Script | Notes |
|---|---|---|
| **B120 master** (Tag SNR 1050070698 / Anchor SNR 960148546) | `flash_b120_master_freeze.sh` | JLink `recover` + loads `merged.hex` + `merged_CPUNET.hex` from the master build. **Master_Anchor is protected** → needs `BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1`. Read the boot banner after (§3a DEPLOYMENT). |
| **Listener** (9 units) | `flash_listener_freeze.sh` | USB JLink, `recover` (PANS-clear), image `listener-freeze-20260715` (MODE_LISTEN, CIR=1). **NEVER flash Geiger SNR 760185886.** |
| **Cold reset a B120 by SNR** | `jlink_reset_by_snr.sh <snr> <device> <speed>` | e.g. `… 960148546 NRF5340_XXAA_APP 4000`. **Load-bearing** for anchor-OTA recovery (warm reboot can't reconnect anchors; only a cold reset does — DEPLOYMENT §7). |

Tags/anchors are **not** JLink-flashed — they are BLE-OTA'd (below). Firmware images +
build recipes: `../firmware/` (`README.md`, `build.source.json`, `SHA256SUMS.txt`).

## 2. OTA (BLE, from a master)
Order matters: **stage anchor payload → anchor OTA → stage tag payload → tag OTA.**
| Step | Script | Notes |
|---|---|---|
| **Preflight** | `ota_preflight.py --targets BS9336,BS955A,BSCCF4 --ota-master <Master_Tag CDC>` | inventories both masters; if a master HOLDS the target tags → points to the escape hatch. OTA needs **no** `MODE IDLE` (DEPLOYMENT law 3). |
| **Escape hatch** (unstick a holding master) | `release_all_tags.py --both`  (or `--port <CDC>`) | `scan` off → disconnect all → verify tags re-advertise. The universal OTA-lock unlock (DEPLOYMENT §6). |
| **Tag OTA** | `ota_deploy_tag_set.py --targets BS9336,BS955A,BSCCF4` | from **Master_Tag**. Uses `ota_single_tag_stable.py` + `verify_ota_payload_kind.py`. **Post-OTA:** RECV filter latches last tag → clear with `ota_target name -`. |
| **Anchor OTA** | `ota_deploy_anchor_set.py` | from **Master_Anchor**. Per-anchor JLink reset + wait 8/8 (`--per-anchor-reset` default ON), atexit control-plane recovery. Uses `ota_single_shot_stable.py` + `verify_all_anchor_responder_runtime.py`. **Post-OTA MUST:** `anchor role all responder` (auto-run by the verifier) else valid_mask=0xf8 → ge7=0. |

## 3. Capture ranging (TR stream)
| Script | Notes |
|---|---|
| `run_recv_tdma_capture.py` | captures the wand tags' `TR;2` stream via **Master_Tag**. Exit paths use **live `CFG_STOP`** (halt TX, stays RUN+advertising) — **never persistent `MODE IDLE`** (DEPLOYMENT law 5 / §4). Needs sibling `master_control_port.py`. Parses clean `TR;2` (DIAG-off freeze contract). |
| `parse_recv_tdma_raw.py` | offline parse of a captured raw log → per-sweep records; **imports `run_recv_tdma_capture`** for its regexes (keep them in the same dir). Accepts `TR;2` (ver==2), no `;D1`/`;TP`. |

## 4. AutoPos anchor self-calibration (Stage-1 input)
| Script | Notes |
|---|---|
| `run_autopos_sweep_loop.py` | drives the anchor↔anchor AutoPos sweep from **Master_Anchor** → `summary.json` (`SW-<label>,…` lines). Recommended sweep entrypoint. Needs siblings `run_autopos_round.py` (UUIDS), `master_control_port.py`, `scan_and_map.py`, `quarantine_tags.py`. Quiesces tags with live `CFG_STOP`. |
| `autopos_extract_pairs_from_sweep_summary.py` | `summary.json` (SW- lines) → inter-anchor pairs. Feeds the V4-io Stage-1 solver. |
| `push_apos_layout_verified.py` | pushes a solved anchor layout back to the anchors (`APOS`/`APOS_COMMIT`, verified via `APOS_STATUS` readback). |

**AutoPos → solver handoff (the full loop):**
`run_autopos_sweep_loop.py` → `summary.json` → `autopos_extract_pairs_from_sweep_summary.py`
(or `solvers/.../build_pairs.py`) → `pairs_all.csv` → **`solvers/.../run_v4io_solve.py`** →
`anchor_layout.json` → **Stage-2** (below) → tag position. `push_apos_layout_verified.py`
writes the layout back to the anchors.

`ops/README.md` lists every ops file + its role + its dependencies.

---

# Positioning Pipeline (`scripts/solvers/`)

A **two-stage** chain. Stage-1 solves the anchor layout from anchor↔anchor ranging; Stage-2
solves tag position from tag↔anchor ranging **+ the Stage-1 layout**. File-mediated: Stage-1
writes `anchor_layout*.json`, Stage-2 reads it. Integrity `solvers/SHA256SUMS.txt`; per-file
source/deps/repoint caveats `solvers/DEPS_MANIFEST.md`. Solver logic copied **verbatim**.

## ★ PRIMARY — Erlangen deployment chain: V4-io + T4
`scripts/solvers/erlangen_deployment_v4io_t4/`  ·  full detail: `…/ERLANGEN_CHAIN.md`

> **Production solver of the Erlangen 2026-05-28 field deployment — produced
> 72.7 mm static / 102.6 mm dynamic. Reproduce those numbers with THIS chain.**

- **Stage-1 = V4-io** (`stage1_layout/`): `run_v4io_solve.py` (driver) → `build_pairs.py`
  → `run_clean_full_compare.py` → `analysis_20260513_182053/…` (core `solve_v4`). Pairs → `anchor_layout.json`.
- **Stage-2 = T4** (`stage2_position_T4_pristine/` + `drivers/run_original_t4.py`): biospur
  package at git `3acfeeda5`, `.so` rebuilt from pristine C source. TR + layout → tag XYZ.
- **T4 vs U5** = same package, 4 files differ (`…/T4_PRISTINE_NOTE.md`).

## OTHER / research / follow-up variants  (`scripts/solvers/other_variants/`)
| Variant | File(s) | What it is |
|---|---|---|
| **V5** (Stage-1) | `v5/solve_v5.py` | scale-locked layout solver — research follow-up to V4-io |
| **U5** (Stage-2) | `u5_note.md` → `…/stage2_position/` | current-tree biospur pkg = T4 + Huber/IRLS + per-anchor σ + RF-SNR σ |
| **multilaterate** (Stage-2) | `multilaterate/{calibrate_listener_positions,full_system_calibration,pg_lib}.py` | **deployed** Stage-2 for the **home CIR rig** static calibration (scipy least_squares Huber f_scale=50) → `wand_positions.json` |
| **research drivers** | `research_drivers/{v5u5_vs_v4iot4,v5_vs_v4io,solver_v2_validation}/` | offline A/B + validation |
| **historical** (Stage-1) | `historical/solve_anchor_layout*.py`, `historical/solve_v4_fusion/` | superseded — lineage only |

## Two Stage-2 solvers — not a contradiction
- Erlangen field deployment → Stage-2 = **T4** (biospur). ★ the primary chain.
- Home CIR-listener rig static calibration → Stage-2 = **multilaterate** (`calibrate_listener_positions.py`).
- Stage-1 for both = **V4-io** (V5 is research). `wand_positions.json` = a **static calibration
  snapshot** (wand held still) for the imaging channel matrix — not a live tracker.

See also `FREEZE_STATE.md` (firmware freeze + reverse-SS-TWR TDMA blocker) and
`HARDWARE_STATE.md` (fleet SNR↔port↔position map).
