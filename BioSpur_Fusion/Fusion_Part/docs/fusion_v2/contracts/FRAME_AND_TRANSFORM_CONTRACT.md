# Frame and transform contract

All frames are right-handed SI. `T_A_B` maps B coordinates into A; rotations are active Hamilton quaternions `[w,x,y,z]`, normalized, with q and -q equivalent. Errors are right-multiplicative SO(3) tangent vectors expressed at the stated linearization point; covariance order and cross-references are mandatory.

`W` requires a session `FrameRealizationId`. Each node has independent `L0_i`; it conveys no anatomy. Future shared `L0` exists only after Phase 2 mapping/extrinsics and Phase 3 observability. Every `T_L0_L0i` is `UNDEFINED` before then. Future root/pelvis, segment, sensor, PCB, antenna-phase-centre and fixed-anchor frames must be explicit. Parent tree and measurement loops are distinct; gauge-fixing is coordinates, not evidence.
