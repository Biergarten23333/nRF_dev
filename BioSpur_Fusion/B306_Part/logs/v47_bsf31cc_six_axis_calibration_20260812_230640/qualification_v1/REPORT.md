# BSF31CC six-axis intrinsic calibration

**BSF31CC_DEVICE_CALIBRATION_VALIDATED**

The device-specific `FULL_SPD` accelerometer model was fitted from 18 training poses only and frozen before four held-out poses. It is not BSFC2CC's numerical profile. Frozen bias is `[-0.00136448311397468, 0.0017623252844895425, 0.00365168152255672]` g and the correction matrix is `[[0.9958193694269935, 0.0024332239583103217, -0.0009136338354679415], [0.0024332239583103213, 0.9969002764420442, -0.0008010324489476995], [-0.0009136338354679413, -0.0008010324489476994, 0.9963290633678679]]`. Training coverage minimum eigenvalue is 0.275455819, design condition 1.894887182, and minimum inter-pose direction angle 21.161138 degrees. The training-only gyro zero-rate bias is `[0.10668770227256176, -0.21386137615999223, -3.301897731686341e-05]` dps.

Gate A is **PASS**. On 16030 strictly held-out samples, gravity-norm RMSE changed from 0.006536968 g to 0.002147240 g; corrected P95/P99 are 0.001432670/0.002084269 g. All four poses improved or met the frozen equivalence rule. Independent training replay selected `FULL_SPD` again; maximum bias/matrix deltas were 1.286e-08/6.646e-08, below one hundredth of one accelerometer LSB (4.883e-06).

Raw transient diagnostic is **OBSERVED_NON_BLOCKING**: 10 retained events in 16030 samples, all isolated with maximum consecutive run 1. Exact sequences, timestamps, raw/corrected values and neighbours remain in `RAW_TRANSIENT_EVENTS.csv`. Their rate and exact 95% confidence interval remain diagnostic and do not directly block calibration under policy V2.

Runtime outlier containment is **PASS**. Each detected event was replayed causally through repaired Q1; all were rejected before gravity correction, quaternion remained numerically continuous, covariance stayed finite/positive and Cholesky-valid, motion state did not falsely become moving, and the next nominal measurement was accepted. This validates only the observed isolated single-sample anomaly class, not arbitrary sustained bursts.

Capture integrity is **PASS**: one serial open, one raw timeline, 18+4 accepted windows, closed 13782334 raw bytes, no accepted-window sequence/timestamp gap, duplicate, reconnect, queue drop, reader error or payload error. The single startup decode fragment and one-byte shutdown tail are outside all accepted windows. The earlier zero-pose tooling abort remains separate and was not merged.

The observed temperature span was 0.570 C, so no temperature model was fitted. BMD101 records/functionality were retained if present but excluded from fitting, validation, metrics and verdict. No BMD101 work occurred.

This device calibration does not establish V4 frame binding, body mounting extrinsics, lever arm, yaw reference, UWB accuracy or deployment readiness. No OTA, upload, pending/PREPARE/COMMIT, firmware write, reboot, power cycle, J-Link, SWD, RTT, AutoPos, BMD101 configuration or automatic physical action occurred. Operator actions were 25 manual placements: 18 accepted training placements, four accepted held-out placements and three rejected held-out placement attempts, all recorded in `OPERATOR_ACTIONS.json`.
