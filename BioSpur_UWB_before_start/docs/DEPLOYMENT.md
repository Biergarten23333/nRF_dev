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
