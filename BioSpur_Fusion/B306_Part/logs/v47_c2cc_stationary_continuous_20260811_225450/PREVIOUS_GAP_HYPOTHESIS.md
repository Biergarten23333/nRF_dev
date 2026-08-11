# Previous-gap hypothesis

Classification: `SUPPORTED`

The old run contained 370 missing IMU samples and 16 missing UWB sweeps, while its decoded totals exceeded nominal by approximately 357 IMU samples and 15 UWB sweeps. The excess durations (1.785 s IMU, 1.800 s UWB) closely match the missing durations (1.850 s, 1.920 s). In the retained old CDC log, the synchronized IMU discontinuity arrived only `0.06646100000943989` seconds after its first decoded sensor record and the host-minus-Master offset dropped by `4332.538999974728` ms. This is direct stale-burst-to-live evidence. Listener continuity and the absence of reset/reconnect evidence reject a board reboot explanation.

The continuous repeat retained startup bytes and moved T0 only after a measured live plateau. Its formal gap result is `True`. The evidence supports the stale CDC/Master prefix followed by a live-sequence jump hypothesis. It does not identify whether the buffered owner was the OS USB path or the Master application/CDC queue. The old registered verdict remains unchanged.
