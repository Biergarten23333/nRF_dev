# S2P versus S2R

Both modes used the identical raw adapter, hardware-time windows, T4 geometry and frozen S2 parameters. Their state transitions are identical because the frozen state controller is shared, but their dynamic estimators are not equivalent. S2P remained room-scale (global position RMS 0.488 m; maximum phase speed 2.24 m/s), whereas S2R reached a global position RMS of 63.835 m, phase position norms up to 556.405 m and speed up to 14.583 m/s. These are self-consistency failures, not absolute-accuracy measurements.

- LOW: S2P release=1.73994488049 s, S2R release=1.73994488049 s; S2P relock=0.0 s (ALREADY_STATIONARY_AT_INDEPENDENT_SETTLE), S2R relock=0.0 s (ALREADY_STATIONARY_AT_INDEPENDENT_SETTLE).
- MEDIUM: S2P release=1.44495422547 s, S2R release=1.44495422547 s; S2P relock=None s (SETTLE_TIME_AMBIGUOUS), S2R relock=None s (SETTLE_TIME_AMBIGUOUS).
- HIGH: S2P release=4.88984509521 s, S2R release=4.88984509521 s; S2P relock=None s (SETTLE_TIME_AMBIGUOUS), S2R relock=None s (SETTLE_TIME_AMBIGUOUS).
- CYCLE_1: S2P release=4.9248439865 s, S2R release=4.9248439865 s; S2P relock=None s (SETTLE_TIME_AMBIGUOUS), S2R relock=None s (SETTLE_TIME_AMBIGUOUS).
- CYCLE_2: S2P release=1.19496214496 s, S2R release=1.19496214496 s; S2P relock=0.0 s (ALREADY_STATIONARY_AT_INDEPENDENT_SETTLE), S2R relock=0.0 s (ALREADY_STATIONARY_AT_INDEPENDENT_SETTLE).
