#!/usr/bin/env python3
"""Compatibility import for the Fusion-owned Q1 implementation.

New algorithm development belongs in ``Fusion_Part/src/biospur_fusion``.
This module remains only so historical B306 transport-analysis scripts replay.
"""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "Fusion_Part" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from biospur_fusion.imu.q1 import *  # noqa: F401,F403,E402
