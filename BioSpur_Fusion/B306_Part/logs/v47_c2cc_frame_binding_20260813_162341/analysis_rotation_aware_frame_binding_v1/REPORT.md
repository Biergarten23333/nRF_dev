# BLOCKED_ROTATION_AWARE_MODEL_UNOBSERVABLE

The historical verdict remains `BLOCKED_INSUFFICIENT_EXCITATION` for the old 12 deg/s limited-rotation model. The original capture did not lack translational excitation. It violated the old near-pure-translation assumption because the manually carried plate rotated during vertical motion.

The rotation-aware calibration replay accepts ordinary 41–43 deg/s carrier motion and uses time-varying repaired-Q1 attitude. It recovers a stable V4 physical-up direction from short vertical reversal strokes, but it does not establish a unique full binding. Mount A's horizontal-1 polarity is nearly opposite the candidate supported by physical up and horizontal-2. Mount B retains four horizontal-1 and two horizontal-2 reversal constraints, but their signed directions conflict; both horizontal actions fail the frozen signed-fit allowance. The two independently estimated V4-up directions also differ by 13.096 deg, exceeding the frozen 10.000 deg cross-mount limit. Both candidate matrices are retained only as diagnostics; neither is an accepted transform.

The supplied `ProPrj_eFlake_Synapse_2026-08-13.epro` identifies U4/DWM1001C and U7/JY901S on the two Streichholz PCB documents. Their reference origins are only 3.580 mm apart, but neither the antenna phase center nor IMU die center is marked. The CAD-proven component-envelope planar separation is at most 32.181 mm. A deliberately larger 50 mm full-3D sensitivity bound changes any extracted stroke direction by at most 0.651 deg, so lever uncertainty is not the blocking cause.

Because calibration failed before held-out opening, `A_VALIDATION` and `B_VALIDATION` samples were not read, no V4 IMU-only or fused trajectory was published, and cross-mount held-out consistency was not scored. A short prospective frame-binding validation remains necessary before ten-node arbitrary-wear T-Pose calibration. It should enforce visible endpoint holds in both horizontal directions and record a separately prescribed first-direction polarity.

This derivation was completely offline. It did not access serial, BLE, J-Link/SWD/RTT, AutoPos, anchors, motor, power, or any Fusion PCB.
