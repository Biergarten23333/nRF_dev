from __future__ import annotations

import pytest

from biospur_fusion.root_r5a.data import load_authorized_c1


@pytest.fixture(scope="session")
def c1():
    return load_authorized_c1()
