# 3-Tag Demo-Readiness — repeatability study + demo procedure

Question: is 3-tag ranging **demo-reliable** — good on (near-)every cold start AND stable for
the full demo — without luck? Method: **N=10 cold starts per config** (variance is the whole
problem, so a single good run proves nothing). Authoritative per-tag data = Master_Tag TR
stream (ge7 = fraction of sweeps with ≥7 valid anchors, matches prewarm); on-air cross-check =
unfiltered Geiger. Raw data: `results.json`. Harnesses: `demo_readiness.py` (A+B),
`phaseC*.py` (C). Date: 2026-07-17.

## TOP LINE
**Free-run is a ~10% lottery and prewarm-reroll only reaches ~50% — neither is demo-reliable.
Distinct-per-tag-slot TDMA (`tdma auto 1`) is deterministic: 10/10 cold starts all-good and
15 min persistence with zero dips. Use distinct-slot for the demo.** It is a production master
command, not a new mechanism, and it also answers the reverse-SS-TWR open question (distinct
per-tag slots on a common epoch works).

| Config | good cold starts (all 3 ge7≥90%) | persistence |
|---|---|---|
| **A** free-run (no reroll) | **1/10** | good start not reliably reproducible |
| **B** reboot-reroll (prewarm logic, ≤5) | **5/10** locked | inconclusive (see B) |
| **C** distinct-slot (`tdma auto 1`) | **10/10** (all 98/98/98) | **15.2 min, 14/14 bins all-good, 0 dips** |

---

## Phase A — the lottery (free-run, no reroll), N=10
**1/10 all-good.** Only 1 start had all three ≥90%. Every other start had ≥1 victim collapsed to
~1–11% (the tag whose BLE events beat against its UWB RX window), often a 2nd tag degraded to
55–73%. Per-start ge7 (`BS9336 / BS955A / BSCCF4`):
```
A1 11/98/66  A2 1/8/48  A3 2/8/46  A4 8/98/65  A5 98/66/8
A6 98/73/8   A7 96/98/72 A8 96/98/61 A9 2/8/60  A10 98/98/98  <- only good one
```
All three always connect and range at ~9 Hz (n≈275 sweeps/30 s) — the failure is **phase
collision**, not connectivity. **Raw odds a naive boot is demo-usable ≈ 10%.**

## Phase B — reboot-reroll prewarm (probe → re-randomize if any tag <0.85 → repeat ≤5), N=10
**5/10 locked** within the 5-attempt budget (B2:1, B9:1, B8:2, B10:2, B7:3 attempts). B1/B3/B4/B5/B6
exhausted 5 attempts still with a victim. This matches a ~10%-per-draw lottery (`1−0.9⁵≈41%`);
to reach 90% lock you'd need ~22 attempts (~18 min startup). **Persistence: INCONCLUSIVE** — the
hold's "reboot-until-good" search only had ~10–50% odds per try and most likely never found a
good start, so it held a bad one (methodology flaw; not evidence that good decays).
**VERDICT B: NOT demo-reliable.** 50% lock is a coin-flip; more attempts is impractical.

## Phase C — distinct-per-tag-slot TDMA (`tdma auto 1`), N=10
The master has a built-in fixed reference-slot table (`master_multi_app.c:213`): each tag gets a
**distinct slot + distinct address on a shared epoch**, pushed as per-tag `CFG`:
```
BS9336 -> tag 2, slot 2/10, addr 0xB102, epoch~5000
BS955A -> tag 3, slot 3/10, addr 0xB103, epoch~4999
BSCCF4 -> tag 4, slot 5/10, addr 0xB104, epoch~4998   (period 10ms, ~10 Hz each)
```
Non-overlapping slots ⇒ the phases **cannot** beat. Results:
- **Repeatability: 10/10 cold starts all 98/98/98**, `CFG_OK LIVE=1` all three, 3 distinct
  on-air pollers `0xb102/03/04` (Geiger), ~9–10 Hz, rawrange real. Variance collapsed.
- **Persistence: 15.2 min continuous, 14/14 60 s bins all-3-good, 0 dips** (one measurement gap
  during a background-task stop; the session kept running and resumed at 98/98/98).
- Safe entry avoided the stuck-0 caveat every time (cold reboot → single apply).
**VERDICT C: demo-ready.** Deterministic (no luck), every cold start good, holds ≥15 min.

---

## Phase D — THE DEMO PROCEDURE

### Config: distinct-slot TDMA (the winner)

> **⚠️ PORT — READ FIRST (2026-07-19).** `demo_start.py` and every script in this dir **hardcode
> `/dev/ttyACM0`.** Each B120 master has **two** CDCs — the console is the **App CDC**
> (`usb-Master_Tag_Master_Tag_Control_*-if00`), NOT the J-Link VCOM (`SEGGER_J-Link_*`). **A power
> cycle renumbers ttyACM**, and today the J-Link VCOM took `ttyACM0` while the console moved to
> `ttyACM2` — so `demo_start.py` as-shipped would open the **wrong device** (opens fine, no response,
> NO-GO). **Before running it:** `ls -l /dev/serial/by-id | grep Master_Tag_Control` → confirm it's
> `ttyACM0`, else point the script (or the capture's `--port`) at the App-CDC by-id. **Robust demo
> path:** `run_recv_tdma_capture.py … --port /dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_*-if00`
> (takes an explicit `--port`, so no hardcode). See `../../../../docs/DEPLOYMENT.md` §8.2.

### Startup sequence (cold → demo-ready in ~30 s; safe, avoids stuck-0)
On the **Master_Tag App CDC** (resolve by-id — see the PORT box above; `dtr=False rts=False`, 115200):
```
cmd_all REBOOT                 # 1. cold boot all tags (clears any stuck-0 / bad phase)
# wait ~16 s for the 3 tags to reconnect
tdma roster BS9336 motion      # 2. define the roster (enables the fixed reference slots)
tdma roster BS955A motion
tdma roster BSCCF4 motion
tdma auto 1                    # 3. assign distinct slots + push per-tag CFG on a shared epoch
```
Expect `CFG assigned … slot=2/3/5 … LIVE=1` for all three.

### <2-min pre-demo CHECK (go/no-go)
Read the Master_Tag TR stream ~30 s and compute per-tag ge7 (fraction of TR with ≥7 valid
anchors). **GO if all three ≥ 90%** (they should read ~98%). Also acceptable: `tdma show`
(roster=auto, 3 profiles) + `cmd_all CFG_STATUS` shows `src=MASTER slot=2/3/5 mode=RUN`.
Cross-check: unfiltered Geiger shows 3 distinct pollers `0xb102/03/04`.

### ONE-COMMAND RECOVERY (rig looks wrong right before demo)
Re-run the **startup sequence** — a cold reboot + re-apply. The reboot clears any stuck-0 /
bad phase; distinct-slot re-locks deterministically. (`demo_start.py` does it.)

### DO NOT
- **Don't rely on free-run** (10% good) or **prewarm-reroll alone** (50%) for a live demo.
- **Don't rapid-fire live `CFG` changes** — that can stick a tag at 0 TX (the stuck-0 caveat).
  If a tag looks stuck/dark, **cold reboot** (the recovery above), don't keep reconfiguring.
- Don't hand-assign per-tag slots — use `tdma auto`; the master's reference table is correct
  (`0xB102/03/04`, matching the deployed wand map / calibration).

### ✅ Capture script alone is demo-ready — END-TO-END CONFIRMED (2026-07-19)
Earlier this study verified distinct-slot via `tdma auto` and read the capture's setup path in
code, but had not run the full capture end-to-end. **Now confirmed empirically:**
`run_recv_tdma_capture.py --targets BS9336,BS955A,BSCCF4 --skip-anchor-preflight` run from a
**clean free-run state**, WITHOUT `demo_start.py`:
- it sets distinct-slot itself (its `tdma clear → roster motion → rebalance`) → on-air
  `0xb102/03/04`, slots 2/3/5; prewarm converged in **1 attempt** (no collision to re-roll);
- **sustained M1 ge7 = 97.8% / ge8 = 96.4%** over 2178 sweeps / 80 s, longest-below-floor 0.0 s,
  worst 1-s bin 97.7%, M2 valid 97.6%;
- **all 3 tags: 0 dropouts, 100% span coverage, balance 0.98** (5824/5896/5704 rows).

**So: just launch the capture — it comes up demo-ready on its own. `demo_start.py` is a
standalone convenience / recovery tool, NOT a prerequisite.**

### ⚠️ Do NOT run a capture on an artificial slow-slot/quiet state
If the rig was put into slow-slot/quiet (e.g. `CFG …PERIOD=9000` for silence), **`cmd_all REBOOT`
to free-run first.** Running the capture on top of the quiet state failed on 2026-07-17
(`link ready 0/3`, 0 rows) — the capture's clean-RECV step drops the links and they don't re-link
from the near-silent state. Reboot to free-run → then capture.

### ⚠️ If the master won't reconnect the tags (nRF5340 dual-core)
Master sees adv names but `conn=0/3` while tags are fine on-air → NET-core BLE stuck; a J-Link
reset (APP core) does NOT fix it — **full USB power cycle of the B120** (renumbers ttyACM →
resolve by-id). See `../../../../docs/DEPLOYMENT.md` §8.1 / `../../../../2026-07-15-FREEZE/HARDWARE_STATE.md`.

### Minor untested edge (not a blocker)
A single tag dropping+reconnecting mid-demo: auto-roster stays enabled (`roster=auto-all-ready`)
so it should re-CFG the reconnecting tag into its slot, but this wasn't stress-tested. If a tag
drops and doesn't recover in a few seconds, use the one-command recovery.
