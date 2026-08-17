# Open-source execution and architecture comparison

| Project | Frozen revision | Actual Phase 3-R use |
|---|---|---|
| VQF | `86ba56bdd3158b9b05f9f9fe5596866ba326438c` (`v2.1.2`) | Official 6D batch execution for all ten nodes, B0 and initialization; deterministic replay tested; never truth or duplicate factor. |
| qmt | `0fa8d32eb461e14d78e9ddbd569664ea59bcea19` (`v0.2.4`) | Official resetAlignment, hinge-axis estimation and heading correction executed on applicable real windows; confidence-gated conditional products only. |
| PIP | `4a281440618e90cbfd4209d83de376d21f9145de` | Causal hierarchy and physical-consistency architecture crosswalk only. No weights loaded. |
| TransPose | `2b31fd1bf1534c6f9c973fa091c0c8cbcccc7310` | Hierarchical leaf/body/joint design crosswalk only. No weights loaded. |

PIP/TransPose checkpoints require an incompatible six-sensor layout including a
head channel and aligned orientation/acceleration inputs. Phase 3-R rejects
fabricated head data, zero fill, copied channels and checkpoint loading. The
BioSpur topology has ten directly observed segments, no head IMU and independent
six-axis heading drift, so transferring their pretrained output would not be a
valid BioSpur pose result.

