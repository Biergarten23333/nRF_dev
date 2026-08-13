# Repair diff summary

The failing regression test first proved that the frame-binding capture and derivation selected `V4IO/anchor_layout.json` instead of the layout named and hashed by `CAPTURE_BOUND_GEOMETRY_MANIFEST.json`. The minimal correction replaces that intermediate path with `V4IO_LAYOUT.json` in the capture, C2CC derivation and the other current-room rotation replay that carried the same path.

No threshold, sensor parameter, accelerometer calibration, time offset, stroke direction or historical artifact was changed. The raw ranges are replayed through the same T4 solver with the authoritative geometry. This repairs the mirror handedness and removes the historical horizontal signed conflict. It does not waive the independent frozen 10° cross-mount-up gate, which still fails; consequently no validation block was opened and no freeze manifest was written.
