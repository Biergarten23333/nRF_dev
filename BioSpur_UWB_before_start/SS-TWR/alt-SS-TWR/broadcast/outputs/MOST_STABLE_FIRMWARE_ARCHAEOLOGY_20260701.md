# Most-stable firmware — one-month capture-log archaeology (read-only)

**Date:** 2026-07-01 · **Scope:** read-only. No hardware / OTA / flash / new capture / code change.
**Ground truth:** measured `ratio_ge7` in `summary.json` (`tag_capture.sweep_validity_all` +
`per_tag.*.sweep_validity`), **not** freeze-doc claims. Anchor id 5 is a structural non-ranging
node → ceiling is **7/8**, ge8≡0, only ge7 matters.

**Corpus:** 2402 `summary.json` under `logs/` (2026-04-29 → 06-30); 659 carry per-tag ge7
(the rest are OTA/anchor-stage summaries). Extractor: `scratchpad/ge7_index.json`.

---

## Verdict (one line)

**The empirically most-stable ranging config is the FROZEN 4-PIECE set (`FREEZE_4PIECE_20260628.md`):
anchor `altbcast-responder-a18-g1200-r1000-20260512_154806` (g=1200 µs / r=1000 µs) + the 2026-06-28
tag baseline.** It produced **409 captures at ge7 ≈ 0.97 with ~97 % of all tags simultaneously
healthy**, and every artifact is on disk + SHA-verified → **re-deployable now**. The current anchor
image (`a19` rfdiag-v2, on the rig since 6/30 20:05) is the *broken* one (ge7 → 0).

---

## Layer 1 — Capture-data archaeology (hard numbers)

### Key discriminator
Best-tag ge7 ≥ 0.9 is the **norm** (539 of 659 captures) — "one tag hit 0.96" does **not**
discriminate firmware. The real signals are **(a)** how many tags are *simultaneously* healthy
(`n_ge90 / n_tags`) and **(b)** whether any sweep reaches the 7/8 ceiling. A broken anchor fleet
caps *every* tag at ~0.5 and never reaches 7.

### Top config groups (ranked by consistency, ≥3 captures)

| median best-ge7 | max | all-tags-healthy frac | N caps | config group | anchor fw | era |
|---:|---:|---:|---:|---|---|---|
| 0.973 | 0.988 | **0.97** | **321** | `wand3_mc*` (3-tag reps) | **a18** | 6/29 |
| 0.972 | 0.977 | **0.97** | **88** | `overnight_thermal*` | **a18** | 6/30 pre-20:05 |
| 0.978 | 0.978 | 0.64 | 6 | `mstatdet_*` (6-tag) | a18 | 6/27 |
| 0.978 | 0.978 | 0.73 | 15 | `mc4_*` (4-tag) | a18 | 6/27 |
| 0.978 | 0.978 | 0.62 | 8 | `clean_visible*` | a18 | 6/27 |

The lower "all-healthy frac" for `mstatdet/mc4/clean` is **not** an anchor defect — those are
deliberate 4–6-tag runs hitting the known TDMA-capacity ceiling (~3–4 tags @10 Hz sacrifices one
tag, see memory `tdma-capacity-ble-phase-beat`). Best-tag stays 0.978 throughout → anchor fleet is
healthy; the loss is tag-slot contention.

### Single highest captures (representative)

| ge7(best tag) | ge8 | date | tag build | anchor build | g/r | tags | capture dir |
|---:|---:|---|---|---|---|---:|---|
| 0.988 | ~0.5 | 2026-06-29 21:29 | baseline-20260628 | a18-g1200-r1000 | 1200/1000 | 3 | `wand3_mc4_rep113_20260629_212727` |
| 0.988 | | 2026-06-29 21:06 | baseline-20260628 | a18-g1200-r1000 | 1200/1000 | 3 | `wand3_mc4_rep099_20260629_210429` |
| 0.978 | | 2026-06-27 19:45 | a7win | a18-g1200-r1000 | 1200/1000 | **6** | `mstatdet_3_20260627_194320` (6/6 tags ≥0.9) |
| 0.978 | | 2026-06-27 16:49 | a7win | a18-g1200-r1000 | 1200/1000 | 4 | `mc4_06_excl_BS9336_BSDC91_20260627` (4/4) |
| 0.978 | | 2026-06-27 15:02 | a7win | a18-g1200-r1000 | 1200/1000 | 3 | `clean_visible3_post_a7win_norfd_10hz_20260627` (3/3) |

*Listener build is irrelevant to ge7 (Listener-E is a passive observer, does not participate in
ranging) → omitted.* The 7/8 ceiling ≈ `0.978` (≈ 750/767 sweeps) recurs because that is the
physical max with anchor-5 structural.

### The a18 corpus is enormous and consistent
`wand3_mc*` (321) + `overnight_thermal` (88) = **409 captures on one unchanged a18 image**, median
best-ge7 0.972–0.973, all-tags-healthy 0.97, over an overnight thermal soak. This is not a lucky
single run — it is the single most-replicated high-ge7 evidence in the whole month.

### Per-anchor-count distribution — the clean signature
- **a18 healthy** (`wand3_mc4_rep113`, 6/29): every sweep lands at exactly **7** anchors
  (BS9336 66×"7", BS955A 83×"7", BSCCF4 73×"7", one "5"). Clean 7/8 ceiling.
- **a19 collapse** (`verify_resp2_120s`, 6/30): smeared **1–6, never 7** (BS955A: 5×431 / 4×248 /
  3×199 / 6×87 / **7×0**). Each anchor independently drops ~half the sweeps → count centres at 4–5,
  never all 7 → ge7 = 0. This is the ~50 % per-anchor coin-flip in raw histogram form.

---

## Layer 2 — Map to freeze / build (why it's stable + can we redeploy)

### Deploy timeline (from `payload_guard_anchor.json` / `tag_version_query.log` mtimes)
- **Anchor:** last OTA before the good corpus = **6/26 23:55** (`anchor_a18_rollback_*`) →
  `altbcast-responder-a18-g1200-r1000-20260512`. Next anchor OTA = **6/30 20:05**
  (`flash_rfdiagv2_anchor_20260630`, marker `rfdiag-v2-g1200-r1000` = a19). **⟹ one stable a18
  bracket spans all of 6/27–6/29 + 6/30-morning.**
- **Tag:** 6/27 runs on `a7win`; 6/29 `wand3_mc` runs on the **6/28 baseline** (last tag OTA
  before them = 6/28 01:14). Both tag families gave 0.97–0.98 → **anchor is the ge7 driver**, tag
  fw within this family does not move ge7.

### The most-stable config = FREEZE_4PIECE_20260628 (exact match to the 6/29 corpus)

| piece | marker / build dir | on-disk artifact | status |
|---|---|---|---|
| **[3] Anchor** (×8, BLE-OTA) | `altbcast-responder-a18-g1200-r1000-20260512_154806` | `build-anchor-unified-ota-altbcast-responder-a18-g1200-r1000-20260512_154806/dfu_application.zip` (168 880 B) | **present, sha `b1288ef0…` ✓ matches freeze** |
| **[1] Tag** (×6, BLE-OTA) | `compact-sampled-tdmafix-nodiag-a7win-baseline-20260628` | `build-tag-…-baseline-20260628/{tag/zephyr/zephyr.signed.bin, dfu_application.zip}` (201–202 kB) | **present** |
| **[2] Master-Tag carrier** (B120 1050070698) | `…-master-tag-lfrc-a7win-reroll-20260628` | `…/zephyr/merged_domains.hex` (1 504 288 B) | **present** |
| **[4] Master-Anchor carrier** (B120 960148546, **PROTECTED**) | `…-embed-altbcast-responder-a18-…-20260512_154806` | `…/zephyr/merged_domains.hex` (1 403 072 B) | **present** |

**⟹ The most-stable set is fully re-deployable from disk.** Restore commands are in
`FREEZE_4PIECE_20260628.md` (tag: `prepare_alt_ota_payload.py` + `ota_deploy_tag_set.py`; anchor:
already the embedded fw in carrier [4]). Note the **PROTECTED** master-anchor B120 (960148546) — its
frozen a18 carrier was overwritten by the a19 carrier on 6/30 but is restorable from the [4] hex on
disk; J-Link reflash needs `BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1`.

---

## Layer 3 — Most-stable (a18) vs current-broken (a19)

| axis | **a18 (most stable)** | a19 (current, broken) |
|---|---|---|
| anchor marker | `altbcast-responder-a18-g1200-r1000-20260512` | `rfdiag-v2-g1200-r1000` (a18 + `APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE=1`) |
| response payload | bare V1, 20 B, **no** anchor diagnostics | V3, 36 B, **+ anchor ΔP** (FP/CIR/rxPACC…) |
| per-response cost | none extra | `dwt_readdiagnostics()` **before** `dwt_starttx` (~55–90 µs @ 8 MHz SPI, no slack guard) |
| params | g=1200 / r=1000 (identical) | g=1200 / r=1000 (identical) |
| measured ge7 | **0.97–0.98**, sweeps land at 7/8 | **0** (verify runs) / ~0.5 single-tag; sweeps 1–6, never 7 |
| root cause | — | pre-TX diag read intermittently busts the delayed-TX deadline → HPDWARN → dropped response → ~50 % coin-flip (memory `a19-diag-timing-proxy-blocker`) |

**If you want known-stable ranging *right now*:** re-deploy FREEZE_4PIECE (roll anchors back to a18).
Difference from current = drop the V2 diag payload; g/r unchanged. **Cost:** you lose anchor ΔP →
the listener-proxy experiment stays blocked until `fixed-a19` (diag read moved *after* `dwt_starttx`,
all ranks). a18 and `fixed-a19` are the two endpoints: a18 = stable-but-no-ΔP, fixed-a19 =
stable-**and**-ΔP (not yet built).

---

## Gaps / skipped (read-only limits)
- Firmware markers are **not** in `summary.json`/`commands.json` (firmware is deploy-time) → anchor
  fw mapped by OTA-timeline bracketing, not per-capture stamp. High confidence (single a18 bracket
  6/26 23:55 → 6/30 20:05), but not a per-file stamp.
- `overnight_thermal` (88 caps, 6/30) assigned to a18 because its ge7 (0.97) is only possible
  pre-20:05; not individually cross-checked against the 20:05 flash minute.
- Numerous ge7=0 captures are **operational**, not firmware (e.g. `a18_rollback_*skipanchorpreflight`
  = anchors left in matrix mode; `verify_postPC` = post-power-cycle; many 6/12 `old3/smoke/nameonly`
  = dead-battery / no-OTA). Excluded from the firmware ranking.
