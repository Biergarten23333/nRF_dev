import pytest

from biospur_fusion.uwb.canonical_t4 import (
    load_canonical_t4_solver,
    validate_anchor_slot_identity,
    validate_delay_ownership,
)


def test_canonical_binding_loads_only_t4():
    models, layout_io, c_solver = load_canonical_t4_solver()
    assert models.__name__.endswith(".models")
    assert layout_io.__name__.endswith(".layout_io")
    assert c_solver.__name__.endswith(".c_solver")


def test_anchor_identity_and_delay_owner_fail_closed():
    validate_anchor_slot_identity(range(8))
    validate_delay_ownership(
        transport_applies_v4_delay=False,
        solver_applies_v4_delay=True,
    )
    with pytest.raises(ValueError):
        validate_anchor_slot_identity((0, 1, 2, 3, 4, 5, 7, 6))
    with pytest.raises(ValueError):
        validate_delay_ownership(
            transport_applies_v4_delay=True,
            solver_applies_v4_delay=True,
        )
