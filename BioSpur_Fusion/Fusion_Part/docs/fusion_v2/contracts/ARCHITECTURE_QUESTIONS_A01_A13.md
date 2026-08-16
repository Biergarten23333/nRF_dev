# Architecture questions A-01–A-13

1. **A-01:** How do ten-node IMU and ten-node individual UWB ranges enter one precise time axis?  
   Provenance: DIRECT_HISTORICAL_PROVENANCE; status: READ_ONLY_REPRODUCED_SAMPLE_AGE_OPEN.
2. **A-02:** How are world, initial-local, pelvis, segment, sensor, board, antenna and anchor frames plus gauges defined?  
   Provenance: DIRECT_HISTORICAL_PROVENANCE; status: NOT_PROVEN.
3. **A-03:** With known hardware IDs but unknown one-to-one Node→body association, how is mapping inferred reliably?  
   Provenance: RECONSTRUCTED_FROM_EXPLICIT_REQUIREMENTS; status: NOT_PROVEN.
4. **A-04:** How does a dedicated pose/action protocol jointly calibrate mapping, extrinsics, lever arms, subject geometry and joints?  
   Provenance: RECONSTRUCTED_FROM_EXPLICIT_REQUIREMENTS; status: NOT_PROVEN.
5. **A-05:** How do raw gyro/accelerometer streams produce orientation, bias, velocity and dynamic IMU states?  
   Provenance: RECONSTRUCTED_FROM_EXPLICIT_REQUIREMENTS; status: NOT_PROVEN.
6. **A-06:** What is the complete articulated state/FK for ten instrumented carrier segments and the boundary for virtual outputs?  
   Provenance: RECONSTRUCTED_FROM_EXPLICIT_REQUIREMENTS; status: NOT_PROVEN.
7. **A-07:** How are fixed bone lengths combined with nonrigidity, soft tissue and nonideal joints?  
   Provenance: RECONSTRUCTED_FROM_EXPLICIT_REQUIREMENTS; status: NOT_PROVEN.
8. **A-08:** With no UWB, how are root translation, contact/ZUPT, yaw gauges and degradation handled?  
   Provenance: RECONSTRUCTED_FROM_EXPLICIT_REQUIREMENTS; status: NOT_PROVEN.
9. **A-09:** How do fixed-anchor raw ranges provide low-rate correction, bad-range rejection and smooth relock?  
   Provenance: RECONSTRUCTED_FROM_EXPLICIT_REQUIREMENTS; status: NOT_PROVEN.
10. **A-10:** How are state/parameter uncertainty, dropout growth and observability computed?  
   Provenance: RECONSTRUCTED_FROM_EXPLICIT_REQUIREMENTS; status: NOT_PROVEN.
11. **A-11:** What is the interface for pose usability, local/world mode, validity, quality and degraded reason?  
   Provenance: RECONSTRUCTED_FROM_EXPLICIT_REQUIREMENTS; status: NOT_PROVEN.
12. **A-12:** How are IMU-only/fusion compared fairly and dev, contaminated regression, sealed and new validation separated?  
   Provenance: RECONSTRUCTED_FROM_EXPLICIT_REQUIREMENTS; status: NOT_PROVEN.
13. **A-13:** How are source, dependencies, lineage, branch/worktree, commits and Python→C governed?  
   Provenance: RECONSTRUCTED_FROM_EXPLICIT_REQUIREMENTS; status: NOT_PROVEN.
