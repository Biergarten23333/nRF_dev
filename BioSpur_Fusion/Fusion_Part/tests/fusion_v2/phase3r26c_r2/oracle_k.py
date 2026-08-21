from __future__ import annotations

import math


def wrap_mod_pi(value: float) -> float:
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError("finite numeric input required")
    return (float(value) + math.pi / 2.0) % math.pi - math.pi / 2.0


def oracle_k_residual(
    k_value: float,
    measurement_protocol_relative: float,
    modulus: str,
) -> float:
    if modulus != "MODULO_PI":
        raise ValueError("K oracle supports only modulo-pi factors")
    return wrap_mod_pi(float(k_value) - float(measurement_protocol_relative))


def oracle_hinge_residual(
    left_k: float,
    right_k: float,
    measurement_protocol_relative: float,
    modulus: str,
) -> float:
    if modulus != "MODULO_PI":
        raise ValueError("K oracle supports only modulo-pi factors")
    return wrap_mod_pi(
        float(left_k) - float(right_k) - float(measurement_protocol_relative)
    )
