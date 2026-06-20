# Source excerpt from c_solver.py/docs/version_chain.md
# T4_V6_IMU_GATE: T4 plus IMU-scaled temporal prior for low-redundancy frames.

def imu_prior_scale(frame):
    imu = getattr(frame, "imu", None)
    if imu is None or not getattr(imu, "valid", False) or getattr(imu, "sample_count", 0) < 3:
        return None
    sigma_acc_mps2 = imu.acc_norm_std_mg * 0.00980665
    scale = exp(-log(2.0) * sigma_acc_mps2 / imu_gate_half_sigma_mps2)
    return clamp(scale, imu_gate_min_scale, 1.0)

# If scale exists, wrapper sets temporal_prior_sigma_mm = base_sigma / sqrt(scale)
# before calling the C method-4 solver. Full-anchor frames still use T1 path.
