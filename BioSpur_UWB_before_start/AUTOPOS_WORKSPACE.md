# AutoPos Workspace

The main workspace is now organized around AutoPos work.

## Active Areas

- `scripts/autopos_*.py`
  - Pair extraction, layout solving, comparison, holdout evaluation, and report generation.
- `scripts/run_autopos_*.py`
  - Live capture and AutoPos sweep orchestration helpers.
- `src/uwb_anchor_layout.c`
  - Runtime anchor layout source used by Tag-side positioning.
- `logs/`
  - Top-level AutoPos logs remain here.

## Archived SS-TWR Work

SS-TWR and Alt SS-TWR content has been moved to:

```text
SS-TWR/
```

That archive includes the frozen broadcast baseline and old SS-TWR build/log artifacts.

## Archived AutoPos Bundles

Old convenience bundles have been moved to:

```text
AutoPos_archive/
```

Those folders are kept for reference only. Active AutoPos development should use the top-level `scripts/` and `docs/` directories.

## Suggested Next Task

Resume from AutoPos layout calibration:

1. Get reliable inter-anchor sweep data.
2. Solve/update anchor layout.
3. Update `src/uwb_anchor_layout.c`.
4. Re-tune RMS and speed gates for the frozen broadcast baseline.
