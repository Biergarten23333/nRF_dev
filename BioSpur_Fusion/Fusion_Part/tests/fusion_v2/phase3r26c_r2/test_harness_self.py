from __future__ import annotations

import math

import pytest

from .recursive_compare import ComparisonMismatch, compare_recursive


def test_recursive_comparator_controls():
    compare_recursive({"a": [1.0, {"b": "x"}]}, {"a": [1.0, {"b": "x"}]})
    compare_recursive(1.0 + 5e-13, 1.0)
    with pytest.raises(ComparisonMismatch):
        compare_recursive(1.0 + 2e-12, 1.0)
    with pytest.raises(ComparisonMismatch):
        compare_recursive({"a": 1, "extra": 2}, {"a": 1})
    with pytest.raises(ComparisonMismatch):
        compare_recursive({"a": 1}, {"a": 1, "missing": 2})
    with pytest.raises(ComparisonMismatch):
        compare_recursive([1, 2], [1])
    with pytest.raises(ComparisonMismatch):
        compare_recursive(True, 1)
    with pytest.raises(ComparisonMismatch):
        compare_recursive(float("nan"), 0.0)
    with pytest.raises(ComparisonMismatch):
        compare_recursive(float("inf"), 0.0)
    compare_recursive(-math.pi + 1e-13, math.pi - 1e-13, modes={"/": "MODULO_2PI"})
    compare_recursive({"x": -math.pi + 1e-13}, {"x": math.pi - 1e-13}, modes={"/x": "MODULO_2PI"})
    compare_recursive(-math.pi / 2 + 1e-13, math.pi / 2 - 1e-13, modes={"/": "MODULO_PI"})
    compare_recursive({"x": -math.pi / 2 + 1e-13}, {"x": math.pi / 2 - 1e-13}, modes={"/x": "MODULO_PI"})
    compare_recursive(math.pi, -math.pi, modes={"/": "MODULO_2PI"})
    compare_recursive(0.0, math.pi, modes={"/": "MODULO_PI"})
