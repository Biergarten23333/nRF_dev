# Timestamp and adapter note

IMU uses `base_us + delta_us`; UWB uses `strobe_us`. Both are B306 hardware-clock timestamps. Host receipt monotonic time is used only to bracket operator protocol regions. No resampling, nonlinear warp, semantic-axis mapping or hidden smoothing occurs in the estimator; the documented 1 Hz T4 low-pass only defines robust chronological position factors and three-dimensional speed minima.
