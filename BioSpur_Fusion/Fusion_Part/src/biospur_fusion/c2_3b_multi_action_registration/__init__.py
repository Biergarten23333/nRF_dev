"""Source-bound C2 3B multi-action registration composition.

The package delegates functional-axis estimation and vector registration to
the pinned QMT implementation and delegates inverse kinematics to official
OpenSim/OpenSense.  It owns only contract validation, data orchestration, and
qualification bookkeeping.
"""

from .contracts import APPROVED_GATE_SHA256, load_approved_contract

__all__ = ["APPROVED_GATE_SHA256", "load_approved_contract"]
