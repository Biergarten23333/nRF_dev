# MVP manufacturing/EOL IMU policy

No 18+4 calibration is required per unit.  In a simple stationary fixture, acquire at least the runtime-policy startup interval and verify 200 Hz continuity, timestamp/sequence integrity, accelerometer norm/noise plausibility, gyro zero-rate stability, and absence of repeated/burst transients.  Reject or quarantine obvious outliers; do not repair them by copying another unit's bias.

Run full arbitrary-pose calibration only on development characterization units, sampled units from new lots, units affected by sensor/PCB/process changes, or units that fail EOL/self-test.  BMD101 is outside this policy.
