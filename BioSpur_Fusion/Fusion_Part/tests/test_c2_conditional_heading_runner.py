"""Reject incompatible conditional-heading execution before opening data."""
from pathlib import Path

import pytest

from run_c2_continuous_archive_ab import run


@pytest.mark.parametrize('options', [
    {},
    {'natural_geometry_only': True, 'articulated_tilt_restoration': True},
    {'natural_geometry_only': True, 'contact_leg_only': True},
])
def test_conditional_heading_incompatible_modes_fail_before_io(tmp_path, options):
    output = tmp_path / 'not-created'
    with pytest.raises(ValueError, match='conditional IMU heading requires'):
        run(Path('missing-frontend'), Path('missing-pose'), output, 5.,
            conditional_imu_heading=True, **options)
    assert not output.exists()
