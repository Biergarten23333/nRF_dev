# Source excerpts from tagpos_solver.c
# T1: robust WLS multilateration; only unknown is tag position p.

# pred = ||p - anchor_i|| + anchor_delay_i + tag_delay
# residual = pred - ranges[i]
# sigma = max(anchor_sigma_i, min_sigma_mm)
# weight = robust_weight(cfg, residual, sigma)
# normal equations accumulate J^T W J and J^T W r; solve 3x3 Gauss-Newton step.

# Default config:
# max_iters=8, min_sigma_mm=5, max_step_mm=500, convergence_mm=0.02,
# robust_loss=Huber, huber_delta_mm=30.
