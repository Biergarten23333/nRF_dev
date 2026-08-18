"""Phase 3-R2.3 common-heading identifiability audit.

This package is deliberately separate from the failed articulated-pose P path.
It consumes only the archived development IMU cache and official VQF/qmt APIs.
"""

from .core import SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION"]
