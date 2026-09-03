"""Contract-bound Capture-2 progressive calibration implementation.

This package is intentionally separate from the preserved ``c2_basis`` draft.
The latter remains useful failure evidence but owns an obsolete axial-offset
model and must not be imported by the repaired run.
"""

from .range_reader import (
    CaptureWideImuState,
    DecodedAction,
    DecodedEvaluationAction,
    SealedFrozenEvaluationRangeReader,
    SealedHeldoutRangeReader,
    SealedPrefitRangeReader,
)

__all__ = [
    "CaptureWideImuState",
    "DecodedAction",
    "DecodedEvaluationAction",
    "SealedFrozenEvaluationRangeReader",
    "SealedHeldoutRangeReader",
    "SealedPrefitRangeReader",
]
