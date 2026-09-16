import json

import pytest
import numpy as np

from biospur_fusion.c2_five_calibration.holdout import FROZEN_FILES, frozen_inputs, reuse_neural
from biospur_fusion.c2_sparse_nodes.inputs import sha


def candidate(tmp_path):
    for name in FROZEN_FILES:
        (tmp_path / name).write_text('{}')
    (tmp_path / 'C2_VALIDATION.npz').write_bytes(b'fixture')
    gates = dict(coverage=True, offset_update_convergence=False, offset_design_rank=True,
                 offset_bounds=True, no_labelled_angles=True, calibration_acceleration=True,
                 fixed_pose_offset_consistency=True)
    (tmp_path / 'C2_VALIDATION.json').write_text(json.dumps(dict(
        calibration_sha256=sha(tmp_path / 'PHYSICAL_CALIBRATION.json'),
        geometry_sha256=sha(tmp_path / 'GEOMETRY.json'),
        output_sha256=sha(tmp_path / 'C2_VALIDATION.npz'), gates=gates)))
    return tmp_path


def test_failed_c2_cannot_enter_accepted_h_path(tmp_path):
    out = candidate(tmp_path)
    with pytest.raises(ValueError, match='diagnostic-only'):
        frozen_inputs(out, diagnostic_only=False)
    assert set(frozen_inputs(out, diagnostic_only=True)) == set(FROZEN_FILES)
    assert not (out / 'ASSESSMENT.json').exists()


def test_reference_assessment_cannot_change_runtime_bindings(tmp_path):
    out = candidate(tmp_path)
    before = frozen_inputs(out, diagnostic_only=True)
    (out / 'ASSESSMENT.json').write_text('deliberately unreadable reference report')
    assert frozen_inputs(out, diagnostic_only=True) == before


@pytest.mark.parametrize('gates', [{}, {'coverage': 'true'}])
def test_missing_or_nonboolean_inertial_gates_cannot_authorize_h(tmp_path, gates):
    out = candidate(tmp_path)
    path = out / 'C2_VALIDATION.json'
    record = json.loads(path.read_text())
    record['gates'] = gates
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='complete five-node validation'):
        frozen_inputs(out, diagnostic_only=True)


def test_diagnostic_scope_cannot_bypass_changed_calibration(tmp_path):
    out = candidate(tmp_path)
    (out / 'PHYSICAL_CALIBRATION.json').write_text('{"changed": true}')
    with pytest.raises(ValueError, match='input changed'):
        frozen_inputs(out, diagnostic_only=True)


def test_neural_reuse_rejects_different_geometry_before_loading_inputs(tmp_path):
    np.savez_compressed(tmp_path / 'H_REPLAY.npz', unused=np.zeros(1))
    record = {'output_sha256': sha(tmp_path / 'H_REPLAY.npz'),
              'frozen_inputs': {'FRONTEND.json': 'same', 'GEOMETRY.json': 'old'}}
    (tmp_path / 'H_REPLAY.json').write_text(json.dumps(record))
    with pytest.raises(ValueError, match='GEOMETRY.json'):
        reuse_neural(tmp_path, {'FRONTEND.json': 'same', 'GEOMETRY.json': 'new'})
