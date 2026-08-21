from __future__ import annotations

import math
from numbers import Real
from typing import Mapping, Sequence


class ComparisonMismatch(AssertionError):
    pass


def _angle_distance(actual: float, expected: float, modulus: float) -> float:
    return abs((float(actual) - float(expected) + modulus / 2.0) % modulus - modulus / 2.0)


def compare_recursive(
    actual: object,
    expected: object,
    *,
    tolerance: float = 1e-12,
    modes: Mapping[str, str] | None = None,
    path: str = "",
) -> None:
    pointer = path or "/"
    mode = (modes or {}).get(pointer, "LINEAR")
    if isinstance(expected, bool) or isinstance(actual, bool):
        if type(actual) is not type(expected) or actual != expected:
            raise ComparisonMismatch(f"{pointer}: expected={expected!r} actual={actual!r} mode=EXACT_BOOL")
        return
    if isinstance(expected, Real) and isinstance(actual, Real):
        left, right = float(actual), float(expected)
        if not math.isfinite(left) or not math.isfinite(right):
            raise ComparisonMismatch(f"{pointer}: expected={expected!r} actual={actual!r} mode=FINITE_REQUIRED")
        distance = (
            _angle_distance(left, right, 2.0 * math.pi) if mode == "MODULO_2PI" else
            _angle_distance(left, right, math.pi) if mode == "MODULO_PI" else
            abs(left - right)
        )
        if distance > tolerance:
            raise ComparisonMismatch(f"{pointer}: expected={expected!r} actual={actual!r} mode={mode}")
        return
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        if set(actual) != set(expected):
            raise ComparisonMismatch(f"{pointer}: expected={sorted(expected)} actual={sorted(actual)} mode=EXACT_KEYS")
        for key in expected:
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            compare_recursive(actual[key], expected[key], tolerance=tolerance, modes=modes, path=f"{path}/{escaped}")
        return
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)) and isinstance(actual, Sequence) and not isinstance(actual, (str, bytes)):
        if len(actual) != len(expected):
            raise ComparisonMismatch(f"{pointer}: expected={len(expected)} actual={len(actual)} mode=EXACT_LENGTH")
        for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
            compare_recursive(left, right, tolerance=tolerance, modes=modes, path=f"{path}/{index}")
        return
    if type(actual) is not type(expected) or actual != expected:
        raise ComparisonMismatch(f"{pointer}: expected={expected!r} actual={actual!r} mode=EXACT")
