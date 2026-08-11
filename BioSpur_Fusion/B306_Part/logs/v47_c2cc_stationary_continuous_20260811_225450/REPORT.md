# BSFC2CC continuous stationary repeat

Primary verdict: `C2CC_STATIONARY_HELDOUT_PASS`

One Fusion serial open and one uninterrupted raw timeline covered collector open, warm-up, CDC drain, the in-stream T0 marker, the 600-second formal window and clean stop. Collector open was `2026-08-11T22:54:50.861+02:00`, live catch-up was established at monotonic `278188`, T0 was `2026-08-11T22:56:23.015+02:00`, and stop was `2026-08-11T23:06:23.015+02:00` after `600.0002091499628` seconds.

Warm-up lasted `92.1542630410404` seconds and retained `612612` bytes before T0. It captured `1` IMU and `1` UWB stale-prefix-to-live transition, with `0` unexplained missing IMU samples and `0` unexplained missing UWB sweeps. Warm-up dirt does not fail the registered formal gate. Previous-gap hypothesis: `SUPPORTED`.

Formal lossless gate: `PASS`. Frozen S2P: `PASS`. Frozen S2R: `PASS`. Zero published RMS, if present, is lock semantics rather than absolute positioning accuracy. No new capture value changed a frozen parameter.
