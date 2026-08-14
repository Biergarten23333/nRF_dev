# Constrained-analysis verification

- Independent complete derivations: PASS (`run_b`, `run_c`).
- All 21 scientific artifacts, including compressed NPZ products and deterministic SVGs: byte-identical.
- Raw SHA before/after: PASS, `a491520739400064db520377ec87a9331feb6274cd42a7e6d9aad57a2b93d56a`.
- Focused constrained-mapping/body-frame tests plus existing body-calibration, capture, canonical geometry/T4 and repaired-Q1 tests: 73 passed, one third-party deprecation warning.
- Eleven delivery MP4s: H.264, yuv420p, 1920×1080, 30 FPS by `ffprobe`.
- Representative comparison frame visually inspected: labels, fixed view, Q0 arrows, F1 positions and dashed shoulder approximation present.
- No hardware interface was accessed.
