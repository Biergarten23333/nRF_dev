# Frame conventions

`B` is the physical JY61P register/board frame; its signed physical directions remain unmeasured. `N` is a local gravity-aligned frame with +Z up and gravity `[0,0,-9.80665] m/s²`. `V4` is the current `RELATIVE_GEOMETRY_ONLY` AutoPos frame. `S` is a future body-segment frame.

`q_NB=[w,x,y,z]` is scalar-first Hamilton and actively maps B vectors to N. Body gyro increments multiply on the right; error attitude is right-multiplicative. Quaternion normalization and sign continuity occur after every propagation/correction. Static gravity initializes roll/pitch; yaw is an arbitrary zero gauge with π-radian prior sigma, not an absolute heading.

`R_V4_N`, signed board axes, physical V4 up and lever arm are not fabricated. Real acceleration therefore propagates local attitude only. T4 corrections fail closed with `SPATIAL_ACCELERATION_COUPLING_BLOCKED_FRAME_BINDING` until a provenance-bound proper rotation and origin are supplied.
