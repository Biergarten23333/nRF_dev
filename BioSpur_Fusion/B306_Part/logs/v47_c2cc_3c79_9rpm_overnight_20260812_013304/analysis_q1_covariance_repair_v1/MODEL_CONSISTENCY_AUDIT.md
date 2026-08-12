# Model consistency audit

Nominal state is `[p_N, v_N, q_NB, b_a_B, b_g_B]`; the 15-vector right error is `[dp, dv, dtheta, dba, dbg]`. `q_NB` is scalar-first Hamilton and actively maps B to N. Body angular increments multiply on the right. The error dynamics use `Fθθ=-[ω]x`, `Fθbg=-I`; right-error injection is `q <- q Exp(dtheta)` and the first-order reset Jacobian is `I-0.5[dtheta]x`. The Joseph measurement update occurs once, followed by one reset transform.

For a validated bound frame, the spatial blocks are `Fpv=I`, `Fvθ=-R[accel]x`, `Fvba=-R`; acceleration noise maps through `-R`. Their signs match the nominal `a_N=R(a_m-ba)+g_N`. T4 observes position, ZUPT observes velocity, and gravity observes attitude plus accelerometer bias.

The frozen real replay claimed `SPATIAL_ACCELERATION_COUPLING_BLOCKED_FRAME_BINDING` while still integrating nominal p/v and retaining all spatial F/G blocks. T4 was never applied, so this produced an unobservable integrated p/v chain and inconsistent scientific semantics. Repaired unbound mode retains the API-compatible 15-state vector but makes p/v dormant and isolates unavailable spatial couplings in both nominal and error dynamics. Accelerometer-bias uncertainty remains represented as its own random walk. A validated `R_V4_N` restores the complete 15-state architecture; it is not silently dropped.

Yaw remains unobservable without an external direction and is allowed to grow. Gravity can constrain roll/pitch only when its update is enabled. No gravity/ZUPT/T4 measurement was enabled during sustained rotation, and all available T4 observations remain explicitly counted as frame-blocked.
