"""Regression: root-only release must not silently replace a body snapshot."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest


spec = importlib.util.spec_from_file_location(
    'publication_tool', Path(__file__).parents[1] / 'tools/publish_c2_finite_horizon_ab.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def fixture(path):
    time = np.arange(5) * .03
    root = np.column_stack((time, time * 0, time * 0))
    state = np.zeros((len(time), 9))
    state[:, :3], state[:, 3] = root, 1.
    arrays = dict(time_s=time, roots_b_posterior=root, root_state_b=state,
                  roots_b=root - .4, joints_relative=-root[:, None, :],
                  uwb_time_s=np.array([.015]),
                  absolute_position_delta_m=np.array([[.2, 0., 0.]]))
    np.savez(path, **arrays)
    return arrays


def test_opt_in_posterior_keeps_one_snapshot_and_stationary_contact(tmp_path):
    source, output = tmp_path / 'source.npz', tmp_path / 'result.npz'
    before = fixture(source)
    report = tool.publish(source, output, mode='posterior')
    with np.load(output) as result:
        np.testing.assert_array_equal(result['roots_b'], before['roots_b_posterior'])
        np.testing.assert_array_equal(result['published_root_state_b'], before['root_state_b'])
        np.testing.assert_array_equal(result['roots_b'] + result['joints_relative'][:, 0], 0.)
        for key in ('time_s', 'root_state_b', 'joints_relative', 'absolute_position_delta_m'):
            np.testing.assert_array_equal(result[key], before[key])
        assert not result['publication_withheld_m'].any()
    assert report['publication_mode'] == 'posterior'
    assert report['maximum_release_delay_s'] == 0.


def test_legacy_is_explicit_and_labels_its_inconsistency(tmp_path):
    source, output = tmp_path / 'source.npz', tmp_path / 'legacy.npz'
    fixture(source)
    report = tool.publish(source, output, mode='legacy-root-release')
    assert report['contact_consistent'] is False
    with np.load(output) as result:
        assert np.max(np.abs(result['roots_b'] + result['joints_relative'][:, 0])) > .1
        assert not np.array_equal(result['published_root_state_b'][:, 3:6], result['root_state_b'][:, 3:6])


def test_mismatched_snapshot_and_existing_metadata_rejected(tmp_path):
    source, output = tmp_path / 'source.npz', tmp_path / 'result.npz'
    arrays = fixture(source)
    arrays['root_state_b'][:, 0] += .1
    np.savez(source, **arrays)
    with pytest.raises(ValueError, match='different snapshots'):
        tool.publish(source, output)
    assert not output.exists()
    output.with_suffix('.json').write_text(json.dumps({'protected': True}))
    with pytest.raises(FileExistsError):
        tool.publish(source, output)


def test_posterior_does_not_replay_any_future_correction(tmp_path):
    source, output = tmp_path / 'source.npz', tmp_path / 'result.npz'
    arrays = fixture(source)
    arrays['absolute_position_delta_m'][:] = 999
    arrays['uwb_time_s'][:] = 10000
    np.savez(source, **arrays)
    tool.publish(source, output, mode='posterior')
    with np.load(output) as result:
        np.testing.assert_array_equal(result['roots_b'], arrays['roots_b_posterior'])


def test_old_explicit_period_cannot_silently_relabel_a_comparison(tmp_path):
    source, output = tmp_path / 'source.npz', tmp_path / 'result.npz'
    fixture(source)
    with pytest.raises(ValueError, match='explicit legacy'):
        tool.publish(source, output, period=.12, mode='posterior')
    assert not output.exists()


def test_failed_candidate_does_not_change_existing_default(tmp_path):
    source = tmp_path / 'source.npz'
    fixture(source)
    report = tool.publish(source, tmp_path / 'default.npz', period=.12)
    assert report['publication_mode'] == 'legacy-root-release'
    assert report['contact_consistent'] is False
