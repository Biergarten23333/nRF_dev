# Source excerpt from c_solver.py
# T4: Python adaptive policy over C method 4.

if config.method in {"T4", "T4_V6_IMU_GATE"}:
    t4_full_anchor_c_config = make_c_config(replace(config, method="T1"))

if method in {"T4", "T4_V6_IMU_GATE"} and n >= 8:
    # Full-anchor frames use memory-free T1 path.
    run_c(t4_full_anchor_c_config, None)
else:
    # Low-redundancy frames use T3-style quality/residual EMA and temporal prior.
    run_c(c_config, previous_position)
