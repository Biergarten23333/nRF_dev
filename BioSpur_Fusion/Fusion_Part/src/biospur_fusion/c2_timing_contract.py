"""Shared source-time limits for the C2 native-200 publication contract."""

import math


def canonical_clock_global_ns(value_ns: float) -> int:
    """Quantize one finite clock-model result to the canonical integer ns."""
    if not math.isfinite(float(value_ns)):
        raise ValueError("clock mapping produced a nonfinite global time")
    value = int(round(float(value_ns)))
    if value < 0:
        raise ValueError("clock mapping produced an absurd global time")
    return value

NATIVE200_PERIOD_US = 5_000
NATIVE200_PERIOD_NS = NATIVE200_PERIOD_US * 1_000

# A strict-floor lookup admits one 5 ms native-200 period plus the frozen
# 5 us integer-clock/rounding margin used by the accepted C2 pose publishers.
NATIVE200_POSE_AGE_MARGIN_NS = 5_000
MAXIMUM_POSE_AGE_NS = float(NATIVE200_PERIOD_NS + NATIVE200_POSE_AGE_MARGIN_NS)
