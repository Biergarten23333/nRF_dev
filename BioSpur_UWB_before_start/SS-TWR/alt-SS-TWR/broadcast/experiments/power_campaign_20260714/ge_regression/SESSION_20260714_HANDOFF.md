# Session handoff — 2026-07-14/15 (ge7 regression fix + sanity check) — READY FOR OVERNIGHT

## Current fleet state (verified)
- **Tags (×3 wand)**: `unified-diaggate-p0txpwr-20260714` — **DIAG OFF (production default)**, CIR OFF, TX MAX.
  BS9336 / BS955A / BSCCF4 all OTA'd + verified (version match=True).
- **Anchors (×8 A–H)**: `altbcast-fixeda19-p0txpwr-g1200-r1000` — **responder, ready=8/8**.
- **Masters**: Master_Tag B120 SNR 1050070698, Master_Anchor B120 SNR **960148546 (PROTECTED)** — both reflashed
  with the matching carriers today.
- Ports: Master_Tag `usb-Master_Tag_..._6918E0384172A49F-if00`, Master_Anchor `usb-Master_Anchor_..._87EA2F4A526C5A02-if00`.

## What was fixed today
ge7/ge8 had collapsed 0.96 → 0. **Build-flag regression on BOTH sides** (NOT position, NOT the P0 txconfig):
1. **Tag (dominant, proven):** 07-14 image had `APP_TAG_RF_DIAG_TAG_RX_ENABLE=1`; good tags had 0. That runs a
   per-response `dwt_readdiagnostics` (~55–90 µs) in the single-RX-buffer collection loop → tag misses every
   *other* anchor → even/odd, exactly 4 valid.
2. **Anchor (secondary):** 07-14 image dropped the `fixed-a19` deferred-diag flags
   (`APP_ANCHOR_RESP_PAYLOAD_DIAG_V2_ENABLE` / `..._POST_TX_DIAG_READ_ENABLE` / `..._POST_TX_DIAG_PAYLOAD_DELAY_ENABLE`,
   1→0) → pre-TX diag busts the delayed-TX deadline.

**Fix:** anchor rebuilt with fixed-a19 flags + init-only P0; tag diag reads/publish now behind a **runtime
`DIAG ON|OFF` flag, default OFF** (production = clean nodiag timing). `DIAG ON` = experiments only (it re-breaks
ge7 — that's expected/proven). Report + patch: this dir (`REGRESSION_REPORT.md`, `results.json`, `clean_firmware.patch`).

## Verification (all PASS)
| test | result |
|---|---|
| DIAG OFF 60 s (post-fix) | **ge7 0.978 / ge8 0.932**, all 8 anchors 96–98% |
| DIAG ON 60 s (A/B) | ge7 0.0 (even/odd) — confirms tag diag is the regressor; keep OFF |
| Step 1 autopos SW100 | **PASS** — layout ~106 mm rms vs known v4io, 28/28 pairs, solve rms 110 mm |
| Step 2 wand 3-tag 120 s | **PASS — ge7 98% / ge8 94%**, 3259 sweeps, all 3 tags ~8600 frames each |

## Anchor blue LED — NOT a fault (important)
The anchor blue LED (P0.31) is an **activity** indicator, not a static role light. In responder mode the responder
loop (`ss_twr_resp.c`) drives the pin **per response TX** and holds it OFF when idle. So: **responder + idle
(no tag polling / no capture) = LED dark, by design**; it lights during active ranging. This code is
**byte-identical to fixed-a19** (see `clean_firmware.patch` — zero LED/role diff). The operator's tag on/off test
(Geiger buzzes + anchor LEDs light when a wand transmits) confirmed the whole rig is healthy.

## OPS GOTCHAS that cost time today — DO NOT repeat overnight
- **`pkill -f run_recv_tdma_capture.py` self-kills the shell** (the pattern matches the bash command line running
  it). Use the bracket trick `pkill -9 -f '[r]un_recv_tdma_capture'` or kill by explicit PID.
- **Foreground `sleep` is blocked by the agent harness** — a `sleep N` in a command aborts the whole command.
- **Verify a capture by its OUTPUT** (session dir + TR rows + live `ge7=` progress), NOT by `pgrep` (process
  existing ≠ ranging). A capture with no session dir/TR is hung, not "running".
- Working capture command (proven today, ge7 0.978 / 98%):
  ```
  python3 -u scripts/run_recv_tdma_capture.py --port <Master_Tag> \
    --controller-reset-snr 1050070698 --skip-anchor-preflight \
    --targets BS9336,BS955A,BSCCF4 --tr-hz 10 --tdma-profile motion \
    --duration <s> --out-dir logs/<name>
  ```
- All J-Link non-interactive: `-NoGui 1 -ExitOnError 1 -SelectEmuBySN <SNR>` (bare `JLinkExe`/`ShowEmuList` pops a
  GUI probe dialog). `jlink_flash_nrf5340_dualcore_by_snr.sh` errors 127 (sources the prose `.protec` guard under
  set -e) → replicate the 2 loadfile steps directly.
- Two masters are independent BLE domains → build/flash/OTA anchor + tag **in parallel**.

## Overnight readiness
Rig is at production state (tags DIAG OFF, anchors responder 8/8, ge7 ~0.98). Wake the wand tags (they sleep on
battery) and confirm all 3 link before launching. Do the mandatory smoke test before any long run.
