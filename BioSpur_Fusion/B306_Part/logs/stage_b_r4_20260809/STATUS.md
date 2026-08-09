# Stage B / R4 status — BSF6C53, end of 2026-08-09

**Board: on `b306-v45r7-val`, alive, `verify=PASS`. Probe released.**

## Done

- Three watchdog-side changes built, gated and flashed: dwell 20 s -> 12 s,
  one `wdt_feed()` inside `v45_capture()`, `.noinit` watchdog witness on
  `V45 STATUS`. Watchdog timeout deliberately unchanged.
- 18/18 gates pass (11 C + 7 Python).
- `test_bsf_v45_detector.c` had been red since R4; arm C and CONN_RELEASED had
  shipped with no passing host coverage. Fixed and covered.
- Six false-verdict tools catalogued in the runbook, with the hand-check rule.
  Two of them were caught by that rule within an hour of writing it.
- F3b: no pool leaking, but the sample is weak (max depth 2 buffers) because an
  earlier reset had already wiped the usage history.
- New tools: `parse_pools.py`, `contact_probe.jlink`, `seg_flash_gated.sh`.

## Open, in priority order

1. **B acceptance failed in an unexpected way** — software reset at the dwell,
   no corpse, `dog=0`, stall recovery `rc=0`. Either a corpse-retention
   regression or an unrelated reset. See `R7_ACCEPTANCE.md`.
   Next step is free: read `boot_id`/`reset_reason` from the boot banner
   before spending a probe press.
2. Detector still rides the system workqueue — structural non-coverage, see
   `DETECTOR_COVERAGE_SCORECARD.md`. Unfixed by design; needs a dedicated
   thread, which is real work.
3. r7-val is on the board. Flash-back to r7-prod is undecided and deliberately
   not scheduled.
4. `dog=` must be trended in the field, not merely present.
