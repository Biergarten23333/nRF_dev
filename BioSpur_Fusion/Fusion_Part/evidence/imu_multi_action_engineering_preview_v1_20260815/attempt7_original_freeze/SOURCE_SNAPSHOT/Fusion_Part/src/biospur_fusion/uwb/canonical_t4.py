"""Canonical frozen UWB_TAG_T4 solver binding owned by Fusion."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


EXPECTED_ANCHOR_SLOT_IDS = tuple(range(8))
_PROJECT = Path(__file__).resolve().parents[4]
_SOLVER_PATH = (
    _PROJECT
    / "UWB_Part"
    / "2026-07-15-FREEZE"
    / "scripts"
    / "solvers"
    / "erlangen_deployment_v4io_t4"
    / "stage2_position_T4_pristine"
)


def validate_anchor_slot_identity(anchor_ids) -> None:
    """Require serializer slots A--H to retain canonical identities 0--7."""
    observed = tuple(int(value) for value in anchor_ids)
    if observed != EXPECTED_ANCHOR_SLOT_IDS:
        raise ValueError(
            f"anchor slot identity mismatch: expected={EXPECTED_ANCHOR_SLOT_IDS} "
            f"observed={observed}"
        )


def validate_delay_ownership(*, transport_applies_v4_delay: bool,
                             solver_applies_v4_delay: bool) -> None:
    """Enforce that exactly the frozen T4 solver owns residual-delay use."""
    if transport_applies_v4_delay and solver_applies_v4_delay:
        raise ValueError("V4 anchor delay would be double-applied")
    if not solver_applies_v4_delay:
        raise ValueError("frozen UWB Tag solver must own the V4 anchor delay")


def load_canonical_t4_solver():
    """Load exactly UWB_TAG_T4, never U5 or an intermediate solver tree."""
    if not _SOLVER_PATH.is_dir():
        raise FileNotFoundError(f"canonical UWB_TAG_T4 solver missing: {_SOLVER_PATH}")
    package = "biospur_tag_positioning_offline_solver"
    for name in tuple(sys.modules):
        if name == package or name.startswith(package + "."):
            del sys.modules[name]
    solver_text = str(_SOLVER_PATH)
    if solver_text in sys.path:
        sys.path.remove(solver_text)
    sys.path.insert(0, solver_text)
    models = importlib.import_module(f"{package}.models")
    layout_io = importlib.import_module(f"{package}.layout_io")
    c_solver = importlib.import_module(f"{package}.c_solver")
    return models, layout_io, c_solver
