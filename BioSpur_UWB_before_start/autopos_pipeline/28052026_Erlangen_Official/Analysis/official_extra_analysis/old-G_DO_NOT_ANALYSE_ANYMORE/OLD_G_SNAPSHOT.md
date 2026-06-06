# Old-G Analysis Snapshot

This directory preserves the previous official extra-analysis outputs that were
computed before the corrected full OptiTrack re-export was adopted as the
authoritative reference.

Reason for archival:

- The original OptiTrack static export had a confirmed Anchor-G marker/model
  issue: `Gtop` and `Glong` were swapped.
- The corrected complete export is now under `opti_captures/full`.
- The analysis outputs in this `old-G` directory are retained as legacy
  provenance and comparison material only.

This snapshot intentionally excludes:

- `old-G/` itself
- `full_opti_reexport_audit/`
- Python `__pycache__` directories
- `run_meta.json.lock`

Use the parent `official_extra_analysis/` directory for the next full rerun
against the corrected `opti_captures/full` reference.
