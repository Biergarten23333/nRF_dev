# Sensor-to-V4 frame binding audit

The current-room Anchor geometry is capture-bound and authorized as `RELATIVE_GEOMETRY_ONLY`. It does not bind any B306/JY61P sensor axis to V4-io. A single static gravity vector constrains two tilt degrees of freedom only; yaw remains unobservable, the boards may have different headings, and V4 +Z has not been surveyed as physical up. The two unknown moves have no external attitude or trajectory truth. Consequently the signed sensor-to-V4 transform is not identifiable.

Main S1 therefore uses CV propagation in V4, IMU only as independent stationarity/motion evidence, and timestamped T4 positions as asynchronous measurements. No acceleration vector is rotated into V4. Full vector propagation and spatial H2/H5/H3 reproduction are `BLOCKED_FRAME_BINDING`.
