import numpy as np
import pytest

from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT, SEGMENT_TO_NODE
from biospur_fusion.c2_coupled_progressive.frontend import EpisodeFrontend, NodeSeries
from tools.c2_continuous_pose_inputs import ContinuousPoseAlignedSpans


def fixture_episode(*, gap=False, boot=False, shifted=False):
    nodes, native = {}, {}
    for index, node in enumerate(NODE_TO_SEGMENT):
        ticks = np.arange(240, dtype=np.int64)
        clock = ticks * 5000
        common = 234836221471621 + ticks * 5_000_100 + index * 1000
        span = np.zeros(240, dtype=np.int64)
        epochs = np.ones(240, dtype=np.int64)
        if gap:
            clock[120:] += 10000
            common[120:] += 10_000_200
            # Native missing tick alone must split even without span metadata.
        if boot:
            epochs[120:] += 1
        if shifted and node == SEGMENT_TO_NODE['torso']:
            common += 5_000_100
        acc = np.column_stack([ticks, ticks * 0, ticks * 0]).astype(float)
        nodes[node] = NodeSeries(common.astype(float)/1000, epochs, acc, acc.copy(),
                                 np.tile([1., 0, 0, 0], (240, 1)), span, np.zeros(240))
        native[node] = dict(time_us=clock, common_global_ns=common,
                            boot_epoch=epochs, contiguous_span_id=span)
    return EpisodeFrontend(0, '00', nodes, {'malformed_stale_report': {}}), native


@pytest.mark.parametrize('gap,boot,expected', [(False, False, 9), (True, False, 18), (False, True, 18)])
def test_native_boundaries_and_scaled_common_clock(gap, boot, expected):
    episode, native = fixture_episode(gap=gap, boot=boot)
    provider = ContinuousPoseAlignedSpans()
    provider.register(episode, native)
    spans = provider(episode)
    assert len(spans) == expected
    for span in spans:
        np.testing.assert_allclose(np.diff(span.parent_time_s), .005, atol=1e-12)
        assert np.median(np.diff(span.time_root_s)) == pytest.approx(.0050001, abs=1e-10)
        assert not span.timing_audit['historical_lag_metadata_used']
        assert not span.timing_audit['rows_cross_gap']
        assert not span.parent_indices.flags.writeable


def test_common_clock_association_preserves_parent_child_row_order():
    episode, native = fixture_episode(shifted=True)
    provider = ContinuousPoseAlignedSpans()
    provider.register(episode, native)
    span = next(s for s in provider(episode) if s.edge == 'pelvis_torso')
    np.testing.assert_array_equal(span.parent_indices, np.arange(1, 240))
    np.testing.assert_array_equal(span.child_indices, np.arange(239))
    np.testing.assert_array_equal(span.parent_acc_mps2[:, 0], span.parent_indices)
    np.testing.assert_array_equal(span.child_acc_mps2[:, 0], span.child_indices)


def test_sidecar_mismatch_fails_closed():
    episode, native = fixture_episode()
    node = next(iter(native))
    native[node]['boot_epoch'] = native[node]['boot_epoch'] + 1
    with pytest.raises(ValueError, match='boot mismatch'):
        ContinuousPoseAlignedSpans().register(episode, native)
