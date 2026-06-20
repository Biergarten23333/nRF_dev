# Source excerpts from tagpos_solver.c
# T2: T1 plus quality-aware sigma inflation.

def quality_penalty(method, cfg, quality, quality_ema, idx):
    if method < T2: return 1.0
    q = blend_current_quality_and_ema(quality[idx], quality_ema[idx])
    bad = max(0.0, (100.0 - q) / 50.0)
    penalty = 1.0 + cfg.quality_penalty_scale * bad * bad
    return clamp(penalty, 1.0, cfg.quality_penalty_cap)
