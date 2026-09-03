from __future__ import annotations

import pytest

from biospur_fusion.root_r4.data import load_c1


@pytest.fixture(scope="session")
def c1():
    return load_c1()
