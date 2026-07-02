# FREEZE — UWB co-located listener firmware, self-heal 2-fix (2026-07-02)

Permanent robustness fixes so the 5 co-located listeners never again need USB
power-cycling ("修一次一劳永逸"). Both acceptance tests PASS (see below).

## Artifact
- Source app: `SS-TWR/alt-SS-TWR/broadcast/UWB_listener/` (poll-diag build)
- Build dir:  `SS-TWR/alt-SS-TWR/broadcast/build-uwb-listener-poll-diag-selfheal2fix_20260702/`
- HEX:        `.../zephyr/zephyr.hex`
- HEX sha256: `bb7deb18a9d9d24b013359176c8ac5d719fad927008b6df1680f08acb68a38de`
- Size:       FLASH 28882 B (5.51% / 512 KB), RAM 14216 B (21.69% / 64 KB)
- Board:      decawave_dwm1001_dev (nRF52832 + DW1000), NCS v2.8.0
- Distinct from prior build `build-uwb-listener-poll-diag-polldiag2_20260701`.

## The two fixes (UWB_listener/src/main.c)
FIX 2 (primary — flood drain-starvation):
  - Bug: `drain_ring_one()` lived only in the RX-idle `else` branch → under sustained
    RXFCG (flood) the drain never ran → ring filled → 0 LPD despite hearing frames.
  - Fix: unconditional bounded drain every loop pass, `LISTENER_DRAIN_BUDGET=4` (line 78),
    drained at main loop ~640-644 regardless of RX state.

FIX 1 (idle-wedge RX-stall watchdog):
  - Bug: main loop only acted on RXFCG or RX_ERR/RX_TO. A silent RX stall (no status bit)
    never re-armed → listener frozen until USB power-cycle (JLink reset did NOT revive).
  - Fix: `LISTENER_RX_STALL_WATCHDOG_MS=15000` (line 75); if `good_frames` does not advance
    for 15 s → `listener_radio_full_recover()` (helper ~line 196: forcetrxoff +
    uwb_hw_bringup_and_init() HW reset + reconfigure + re-arm) and `self_recover++`.
  - Under traffic good_frames advances every ~1 ms so the watchdog stays quiet; in silent
    air it self-heals every 15 s and receives immediately when traffic returns. Recover is
    idempotent (same bringup the anchor app re-runs).

LSTAT gained 4 trailing fields: `ring_drops;self_recover;rx_enable_failures;fps`
  (print_status ~line 589). Host parser extended back-compatibly:
  `scripts/capture_uwb_poll_listener.py` (optional LSTAT_RE trailing group + lstat_fields).

Deliberately NOT changed (already correct in shipped code):
  - boot HW reset — `uwb_hw_bringup_and_init()` already does uwb_port_hw_reset (DW1000 RSTn)
  - error-branch re-arm — already calls listener_restart_rx() (forcetrxoff+rxreset+enable)
  - ring drop-oldest + ring_drops counter — already existed (only surfaced it in LSTAT)

## SNR -> identity -> co-location (authority: docs/broadcast_tag_inventory.md)
FLASHED with this hex (all 5):
  - L-955A  760186081  co-located Tag BS955A    (tag-side EXP)
  - L-9336  760186071  co-located Tag BS9336    (tag-side LOS control)
  - L-B     760184545  co-located Anchor B      (anchor-side EXP; ex-BS1396 retired)
  - L-E     760184767  co-located Anchor E      (anchor-side LOS control)
  - L-F     760184964  co-located Anchor F      (anchor-side EXP; ex-BS7724)
OFF-LIMITS (never listener targets):
  - 盖格 760185886 legacy air monitor (UWB_listener_old) — NEVER reflash without go
  - Master_Tag 1050070698 / Master_Anchor 960148546 (PROTECTED)

## Acceptance results
FLOOD (Fix 2) — 10 min, 3 wand tags @10 Hz, non-target not silenced
  (logs/floodtest_20260702_004519): all 5 listeners good_frames 0->~43k, fps med 78/max 84,
  LPD ~14.4k rows, minute-buckets-with-LPD 10/10 (continuous), ring_drops=0, LRD ~28.8k,
  M1 ge7=96.2%. self_recover 9->14 occurred ONLY in the 63 s pre-ranging silence, then froze
  at 14 under traffic (watchdog correctly quiet under load). PASS.
  Caveat: 78 fps = operating density, not the ~930 fps saturation; ring_drops never engaged.
  Fix is structurally starvation-proof (drain unconditional) at any fps.

IDLE SOAK (Fix 1) — 90 min silent + 60 s traffic restore
  (logs/idlesoak_20260702_005914): pre-check all 5 QUIET (good_frames +0, fps 0).
  Phase B (90 min): self_recover ~385-386/node (watchdog fired through silent gaps),
  lstat_rows ~1085 growing (loop alive throughout), good_frames crept 42.9k->48.5k
  (~1 fps room background — node handled intermittent frames + recoveries without loss).
  Phase C (restore): per node self_recover 389-390, lstat_rows 1103-1104, restore-LPD 740-744,
  resumed=True. ALL 5 PASS — survived ~385 recover cycles and resumed receiving instantly.

## Flash / restore
  HEX=SS-TWR/alt-SS-TWR/broadcast/build-uwb-listener-poll-diag-selfheal2fix_20260702/zephyr/zephyr.hex
  for SNR in 760186081 760186071 760184545 760184767 760184964; do
    BIOSPUR_LISTENER_SN=$SNR bash SS-TWR/alt-SS-TWR/broadcast/scripts/flash_uwb_listener_jlink.sh "$HEX"
  done
  # NEVER pass 760185886 (盖格) / 1050070698 / 960148546.

## 盖格 (UWB_listener_old) diff — reported, NOT flashed
  - Fix 1 (watchdog): APPLIES — same silent-stall vulnerability; has good_frames/last_frame_ms
    + uwb_hw_bringup_and_init(); ~15-line port. Hold for explicit go (preserves AUTO-follow build).
  - Fix 2 (budget-drain): N/A — no ring->UART decoupling; handles one frame inline per loop.

## Design note (accepted tradeoff)
  In prolonged silence the watchdog fires every 15 s (self_recover climbs ~4/min). Harmless
  and idempotent; proven receivable after 385 cycles. Not backed off, by design — self-heal
  aggressiveness is the point. During captures (always >0 traffic) it never fires.
