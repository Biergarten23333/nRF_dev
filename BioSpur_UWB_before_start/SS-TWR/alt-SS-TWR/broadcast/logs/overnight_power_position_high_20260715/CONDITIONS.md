# Run conditions — position_high overnight power sweep (20260715)

**Marked 2026-07-15 09:57:04 by operator.**

## ⚠ Human movement in the room DURING capture
The operator was **walking around indoors during this run (and earlier today)** — NOT a
static, unattended environment like the room-center run (20260714, which ran overnight with
nobody moving).

Implications for analysis (do NOT blindly reuse the center run's "environment stable /
0 contaminated cells" narrative):
- A walking person is a **moving scatterer/blocker** → transient multipath + possible
  LOS blockage on tag↔anchor and tag↔listener links.
- Expect: intermittent **cir_pwr excursions** at the 7 listeners, possible **bias steps /
  lock events**, and inflated **positioning scatter** in some cells — these may be MOVEMENT
  artifacts, not a power effect or a geometry effect.
- The 7-listener fleet is itself a **movement detector**: a real person perturbs several
  listeners' received power coincidentally in time. `detect_movement.py` looks for
  cross-listener coincident cir excursions and timestamps them, so movement can be
  attributed and (where needed) excluded rather than assumed away.

## Other conditions (same as center run)
- Wand physically **raised** vs center run; otherwise identical rig (anchors/layout/listeners
  not moved), DIAG OFF, anchors locked MAX, only tag TX swept via runtime TXPWR.
- Round-1 MAX cell is dead (collision with a leftover sweep, since killed); MAX still has
  2 clean cells (rounds 2-3).
