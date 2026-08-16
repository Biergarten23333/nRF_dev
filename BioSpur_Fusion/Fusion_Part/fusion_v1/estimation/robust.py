from __future__ import annotations
import math

def cauchy_weight(standardized_residual: float, scale: float=2.5) -> float:
    if scale <= 0: raise ValueError("scale must be positive")
    x=standardized_residual/scale
    return 1.0/(1.0+x*x)

class PairHealth:
    """Visible rapid-fall, slow-recovery confidence for one UWB pair."""
    def __init__(self, confidence=1.0, fall=0.35, recovery=0.02):
        self.confidence=float(confidence); self.fall=fall; self.recovery=recovery
    def update(self, standardized_residual=None, available=True):
        if not available or standardized_residual is None:
            self.confidence=max(0.0,self.confidence-self.fall*0.25)
        elif not math.isfinite(standardized_residual) or abs(standardized_residual)>4.0:
            self.confidence=max(0.0,self.confidence-self.fall)
        elif abs(standardized_residual)<2.0:
            self.confidence=min(1.0,self.confidence+self.recovery)
        return self.confidence

