from __future__ import annotations

import pytest


@pytest.mark.parametrize("letter", list("ABCDEFGHIJKLMNOPQRST"))
def test_synthetic_gate(letter, qualification):
    assert qualification["gates"][letter]["pass"], qualification["gates"][letter]


def test_exact_gate_inventory(qualification):
    assert qualification["gate_order"] == list("ABCDEFGHIJKLMNOPQRST")
    assert set(qualification["gates"]) == set("ABCDEFGHIJKLMNOPQRST")
    assert qualification["summary"]["executed_gate_count"] == 20
    assert qualification["summary"]["all_pass"]


def test_synthetic_noise_is_explicitly_nonproduction(qualification):
    assert qualification["summary"]["synthetic_noise_provenance"] == "ROOT_R6A1A_SYNTHETIC_TEST_ONLY"
