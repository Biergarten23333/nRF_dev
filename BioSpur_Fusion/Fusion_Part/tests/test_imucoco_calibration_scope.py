"""Prevent execution success and protocol prose from masquerading as calibration."""
import numpy as np
import pytest

from biospur_fusion.c2_imucoco import protocol
from biospur_fusion.c2_imucoco.diagnostics import _axis


def test_proposed_shoulder_fit_is_not_reported_as_implemented(monkeypatch):
    rows = [dict(action=name, role='fit', purpose='old proposed purpose', acquired=True)
            for name in ('04_shoulder_left', '10_knee_left_seated')]
    monkeypatch.setattr(protocol, 'calibration_ledger', lambda *args: {'actions': rows})
    result = protocol.implemented_ledger({}, {})
    shoulder, knee = result['actions']
    assert shoulder['implemented_role'] == 'input_statistics_only'
    assert shoulder['fitted_products'] == []
    assert 'offset' not in str(knee['fitted_products'])
    assert all(not row['calibration_validation_passed'] for row in result['actions'])
    assert result['calibration_status'] == 'INCOMPLETE'


def test_unaccepted_replay_requires_explicit_diagnostic_scope():
    with pytest.raises(ValueError, match='calibration is incomplete'):
        protocol.require_replay_scope(diagnostic_only=False)
    protocol.require_replay_scope(diagnostic_only=True)
    from biospur_fusion.c2_imucoco.workflow import replay
    with pytest.raises(ValueError, match='calibration is incomplete'):
        replay(None, None)  # Reject before reading models, files or H inputs.


def test_repeatability_exposes_inconsistent_axis_and_insufficient_motion():
    rows = np.zeros((6000, 11))
    rows[:, 0] = 100.+np.arange(len(rows))*.005
    rows[:3000, 8] = np.sin(np.arange(3000)*.02)
    rows[3000:, 9] = np.sin(np.arange(3000)*.02)
    first, _ = _axis(rows, 0., 15.)
    second, _ = _axis(rows, 15., 30.)
    assert abs(first@second) < 1e-12
    rows[:, 8:11] = 0
    axis, report = _axis(rows, 0., 30.)
    assert axis is None and report['status'] == 'INSUFFICIENT_EXCITATION'
