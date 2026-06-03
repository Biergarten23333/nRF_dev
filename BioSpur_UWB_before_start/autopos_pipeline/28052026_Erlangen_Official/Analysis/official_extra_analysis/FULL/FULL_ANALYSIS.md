# FULL Analysis

This directory is the clean analysis workspace for the corrected complete
OptiTrack export.

Canonical optical reference:

```text
opti_captures/full
```

Important provenance:

- The original OptiTrack static export had a confirmed Anchor-G marker/model
  error: `Gtop` and `Glong` were swapped.
- The corrected complete export in `opti_captures/full` is the authoritative
  OptiTrack reference for this workspace.
- The previous analysis computed with the old G definition is archived under
  `../old-G`.
- Canonical FULL results keep all eight anchors. G-removal sensitivity analyses
  must not be generated inside this directory.

Path hygiene:

- Scripts in `FULL/scripts` default to this `FULL` directory for outputs.
- Static OptiTrack truth loaders in `FULL/scripts` default to
  `opti_captures/full`.
- Filtered deployment scripts are separately copied under
  `FULL/filtered_deployment/scripts`.
- Do not write new FULL outputs into the parent `official_extra_analysis`
  directory.
