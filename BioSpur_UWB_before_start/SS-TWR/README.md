# SS-TWR Archive

This directory contains the archived SS-TWR / Alt SS-TWR work that used to live in the main workspace.

## Contents

- `alt-SS-TWR/`
  - Frozen unicast and broadcast Alt SS-TWR workspaces.
  - Broadcast baseline is frozen at:
    - Tag: `alt-bcast-b55-noconsole-8anc-g1200-r1000-rms0`
    - Anchor: `alt-bcast-a13-nosleep-hotpath-g1200-r1000`
  - See `alt-SS-TWR/broadcast/BROADCAST_BASELINE_FREEZE.md`.
- `legacy_builds/top_level/`
  - Old top-level SS-TWR-era build directories and `.source` records.
  - These are archived for traceability, not active development.
- `legacy_logs/`
  - Old SS-TWR, TDMA, resp1000, timingcf, listener smoke, OTA, and responder verification logs.
- `manifests/`
  - Move manifests generated during the workspace split.

## Active Direction

SS-TWR / Alt SS-TWR is considered stable enough to archive here.

Main workspace development should now focus on AutoPos algorithms, layout calibration, and validation.

Do not modify frozen SS-TWR baseline files unless creating a new versioned experiment.
