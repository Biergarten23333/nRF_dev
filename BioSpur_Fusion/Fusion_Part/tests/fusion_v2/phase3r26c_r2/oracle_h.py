from __future__ import annotations

import math


def wrap_2pi(value: float) -> float:
    return (float(value) + math.pi) % (2.0 * math.pi) - math.pi


def oracle_h_covariance(k_value: float, psi_value: float, alpha: float) -> dict[str, float]:
    shifted_psi = wrap_2pi(psi_value + alpha)
    h_value = wrap_2pi(k_value + psi_value)
    shifted_h = wrap_2pi(k_value + shifted_psi)
    recovered_k = wrap_2pi(shifted_h - shifted_psi)
    return {
        "shifted_psi": shifted_psi,
        "h": h_value,
        "shifted_h": shifted_h,
        "recovered_k": recovered_k,
    }
