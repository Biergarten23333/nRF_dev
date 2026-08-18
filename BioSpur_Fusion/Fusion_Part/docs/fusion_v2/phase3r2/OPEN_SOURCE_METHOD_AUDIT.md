# Phase 3-R2 open-source method audit

This audit is pinned to the local, clean source checkouts and commits recorded
in `PHASE3R2_DEPENDENCY_LOCK.json`. The qualification test verifies both Git
tips, clean status, the VQF extension hash, and executes both packages.

| Method | Decision | Phase 3-R2 use |
|---|---|---|
| VQF v2.1.2, commit `86ba56b` | ADOPTED with a narrow boundary | Official 6-D B0 comparator and, at most, one session/boot initializer. It is never recreated at an action boundary and contributes zero production factors. |
| qmt v0.2.4, commit `0fa8d32` | ADAPTED | Functional-axis or heading products may initialize a parameter only after confidence and lineage checks. Same-source derived evidence is not inserted again as an independent production factor. The current real path is structural zero because the time gate failed before fit. |
| OpenSim/OpenSense IMU Placer static calibration | ADAPTED as architecture reference | Its sensor-to-model calibration separation motivates a frozen session bundle. A single gravity direction is not promoted to an observed full SO(3). No OpenSim runtime is imported. |
| PIP | REJECTED for execution | Architecture reference only. Its sensor layout and learned distribution do not match the ten-node system; no pretrained weights are loaded. |
| DIP | REJECTED for execution | Architecture reference only. No checkpoint, synthetic sensor, or zero-filled channel is accepted. |
| TransPose | REJECTED for execution | Architecture reference only. No six-sensor pretrained model is used to claim ten-node success. |

The primary chain is the continuous raw-IMU 9-state frontend followed by the
30-D articulated solver. Calibration uncertainty is marginalized into the
orientation likelihood; it is not counted as an extra factor. The qmt
alignment residual can only confirm that a requested algebraic transform ran,
not that a physical sensor-to-segment calibration is accurate.
