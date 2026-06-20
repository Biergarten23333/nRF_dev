# Source excerpts from tagpos_solver.c
# T3: T2 plus residual-memory sigma inflation and previous-position prior.

def residual_penalty(method, cfg, residual_ema, idx):
    if method < T3 or residual_ema[idx] <= cfg.residual_ema_start_mm:
        return 1.0
    excess = (residual_ema[idx] - cfg.residual_ema_start_mm) / 80.0
    return clamp(1.0 + cfg.residual_penalty_scale * excess, 1.0, cfg.residual_penalty_cap)

# If method >= T3 and x0 is valid, add residual (p - x0) / temporal_prior_sigma_mm.
