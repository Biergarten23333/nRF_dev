# Bounded real shadow critical path

Synthetic R6A2A-R2 is qualified, but no real fusion or body-state update was run. A first read-only bounded real shadow has four minimum blockers:

1. Bind full signed physical axes for both hardware families and record a labelled, session-bound neutral donning action with finite angular uncertainty.
2. Provide separate common-nine and BSF31CC CAD nominal IMU-to-UWB levers with finite IMU-origin/RF phase-centre uncertainty. Never reuse common-nine mechanical transforms for BSF31CC.
3. Qualify the capture-specific `T_N_V4` bridge from survey or labelled gravity plus heading evidence, with covariance.
4. Define the physical `torso_top` landmark unambiguously; a broad initial bound is sufficient.

The named capture's anchor positions/delays and ten ClockModels are already hash-bound and importable. Common-nine and BSF31CC family identities are also sealed. Subject anthropometry, joint/rest geometry, and skin slip may enter only as explicit bounded priors with sensitivity reporting; exact ten-device noise qualification blocks production, not the first read-only shadow. Distal landmark metrology is an optional accuracy improvement. World gauge is derived after the V4/navigation bridge and is not an independent fit.

All 87 real slots remain null and `FROZEN_UNCERTAIN`; none is written or declared collectively blocking.
