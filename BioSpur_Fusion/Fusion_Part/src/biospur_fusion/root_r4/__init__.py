"""Experimental C1 Root-R4 raw-range/T4 common-root evidence package.

Importing this package has no side effects and cannot modify M1, UWB evidence,
firmware, product defaults, or emitted production data.
"""

from .contracts import FrameContract, LineageError, FactorLedger

__all__ = ["FrameContract", "LineageError", "FactorLedger"]
