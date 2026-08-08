# STATUS

Run: BT wedge consolidated raw-data forensics, 2026-08-08.
Mode: offline only. No firmware, no hardware, no raw file modified.

| part | scope | state | wall time |
|---|---|---|---|
| P1 | §1 manifest, clocks, counter semantics, dataflow map, pool IDs | **complete** | ~35 min |
| P2 | §2 detector + registry + label audit + near-miss census, §3 pre-registered matrix | **complete** | ~25 min |
| P3 | §4 event packets, §5 activity census, §7 triggers, §8 terminal sequences | **complete** | ~30 min |
| P4 | §6 latency precursor, §9 pool constraints, §10 boundaries | **complete** | ~20 min |
| P5 | §11 necessity/rate/confound, §12 downtime ledger | **complete** | ~15 min |
| P6 | §13 scorecard, §14 v45 delta, final report | **complete** | ~25 min |

All six parts complete. Nothing is left pending.

## How to reproduce or resume

```
VENV=<scratchpad>/venv/bin/python      # created with --system-site-packages + pyarrow
cd B306_Part/logs/bt_wedge_forensics_20260808
$VENV scripts/p1_extract.py            # ~3 min, writes cache/*.parquet
$VENV scripts/p1_manifest.py           # ~4 min, writes INPUT_MANIFEST.json
$VENV scripts/p2_air.py                # ~1 min, listener air timelines
$VENV scripts/p2_detect.py             # detector -> cache/detect_raw.json
$VENV scripts/p2_registry.py           # -> WEDGE_EVENTS.*, NEAR_MISS_EVENTS.csv
$VENV scripts/p3_packets.py            # -> cache/event_packets.json
$VENV scripts/p4_latency_pools.py      # -> cache/p4.json
$VENV scripts/p5_triggers_ledger.py    # -> cache/p5.json
$VENV scripts/p5_ledger.py             # -> cache/p5b.json
$VENV scripts/p6_plots.py              # -> EVENT_*.parquet / EVENT_*.png
```

Intermediates live in `cache/` (parquet + json) and are regenerable.
`scratch/` holds a throwaway C program used once to get `sizeof()` on the
wire structs.

## INSUFFICIENT items

| item | why | what would obtain it |
|---|---|---|
| whether `hci_cmd_pool` ever emptied | never observed below 2/2, but 2 buffers can be taken and returned entirely between 1 Hz strobes | Δ2 — sub-second `low_water` folded in at allocation |
| whether `hci_rx_pool` was ever held | never observed below 10/10 in 1 289 unbiased strobes; no holder can exist in this configuration | declared undecidable; Δ2 explicitly **removes** this from v45 scope |
| the state of the MPSL/SDC receive path at any onset | no instrumentation exists anywhere below `bt_hci_recv` | Δ1 + Δ3 counters |
| whether the notify worker was inside `bt_gatt_notify()` at onset | `e`/`x` live only in the stall characteristic, which a wedged node cannot answer | Δ1; and Δ7, so the corpse survives the power cycle |
| sub-second ordering of node-internal events vs onset | telemetry is 1 Hz | not needed — §7.2 found no enrichment, so ordering never arises |
| BSF31CC dock-contact/charge history | a between-runs fault, not visible in run logs | out of scope; noted in the ledger rather than scored as zero |
| `FUSION_CONNECTED`/`DISCONNECTED` lifecycle for N5/N7 | those record types are dk-v36 additions and do not exist in dk-v35 runs | ledger for N5/N7 is built from record streams instead; no event is missed because both runs have complete delivery apart from the one N7 wedge |

## Deviations from the brief

1. **A fourth run (N6, `v43_run2_20260807/B_RUN`, 38 s, 10 nodes) was
   included.** The brief names only N5/N7/N8. Excluding a run because it was
   not named would bias the exposure denominator. It contributes 0.11 bh and
   0 events.
2. **§1.6's expectation was wrong and is corrected in place.** Node
   `FUSION_QUEUE`/`FUSION_POOL` records exist in **all** runs, not v44 only,
   so §9 applies to every run. The v44-only records are dk-v36 master-side
   lifecycle lines.
3. **`low_water` semantics corrected mid-run** after reading the two write
   sites: it is a two-point 1 Hz strobe, not a window minimum. An earlier
   draft of `COUNTER_SEMANTICS.md` stated the stronger form; it was corrected
   before any conclusion depended on it, and the correction is what led to
   the unbiased stall-strobe census that changed the leading hypothesis.
4. **The classification reboot test was tightened** from "reboot anywhere in
   the stall window" to "reboot at onset" after the loose form misclassified
   BSFEC35's 15:46 wedge as a brownout on the strength of its own 21:14
   depletion reboot 5½ hours later.
5. **§4.y was pre-registered as a discriminating axis and is not one.** The
   arithmetic (8 buffers ÷ 1.5 notifications per connection event ≈ 250 ms at
   normal cadence) should have been done before writing the matrix. Recorded
   in the scorecard, not fixed in the matrix.
