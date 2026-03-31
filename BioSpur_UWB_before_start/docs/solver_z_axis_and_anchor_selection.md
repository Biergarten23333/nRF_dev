# Solver Z-Axis And Anchor Selection

## Executive Summary
- The current solver already enforces cross-plane geometry safeguards that help Z-axis stability:
  - requires at least `2` lower-plane anchors and `2` upper-plane anchors
  - rejects subsets below a tetra-volume threshold
- Candidate acquisition and final subset selection are separate concepts:
  - runtime tracking can collect about `6` candidate anchors
  - solver picks a final subset from those candidates
- Before this round, final subset size was `>=4` (not strict).
- In this round, normal tracking solve is patched to use **exactly 4** final anchors, while full/refresh remains `>=4`.

## Current Behavior
- Candidate generation:
  - builds candidates from valid measurements and anchor layout metadata.
  - source: `uwb_tag_loc_build_candidates(...)`.
- Candidate pruning:
  - limits candidate count while preserving lower/upper diversity by quality.
  - source: `uwb_tag_loc_prune_candidates(...)`.
- Subset solve loop:
  - iterates bitmask subsets and evaluates solution score.
  - previously accepted any subset with `popcount >= 4`.
  - source: `uwb_tag_loc_solve(...)`.

## What Protects Z-Axis Accuracy Now

### 1) Non-coplanar tendency via tetra-volume gate
- Each candidate subset is checked by tetra-volume quality.
- Subsets below `UWB_TAG_LOC_MIN_TETRA_VOLUME_M3` are rejected.
- This avoids near-coplanar combinations that are weak in Z.

### 2) Explicit 2+2 cross-plane enforcement
- After residual computation, subsets with fewer than:
  - `2` lower-plane anchors, or
  - `2` upper-plane anchors
  are rejected.
- This is a direct Z-stability safeguard.

### 3) Residual-based scoring and bounds penalty
- Solver score includes RMS and max residual components.
- Additional overshoot penalty discourages unrealistic positions outside geometry bounds.

## What Still Diverges From Intended Policy
- Candidate-side behavior is already near intended (`~6` in tracking path).
- The missing part was final subset strictness:
  - old behavior allowed `5`/`6` final anchors when score won.
  - intended policy is `6 candidates -> final exactly 4` in normal tracking.

## What This Round Implements
- Adds explicit subset policy control for solver:
  - `MIN4` mode: allow `>=4` subsets.
  - `EXACT4` mode: allow only `4`-anchor subsets.
- Runtime binding:
  - normal tracking solve uses `EXACT4`.
  - full/refresh uses `MIN4` (unchanged maintenance behavior).

## Relevant Source Locations
- Candidate build/prune/solve core:
  - `src/uwb_tag_loc.c`
  - `include/uwb_tag_loc.h`
- Runtime sweep plan and solver invocation:
  - `src/ss_twr_init.c`
- Cross-plane layout helpers:
  - `src/uwb_anchor_layout.c`
  - `include/uwb_anchor_layout.h`

