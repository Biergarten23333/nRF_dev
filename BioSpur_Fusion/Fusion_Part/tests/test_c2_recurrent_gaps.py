"""Missing input must not become a recurrent observation or change its clock."""
import numpy as np
import pytest

from biospur_fusion.c2_imucoco.backend import ChunkedPoseStream


class RecordingStream(ChunkedPoseStream):
    def __init__(self):
        self.state = np.zeros(3)
        self.calls = []
        self.vertices = list(range(5))
        self.mapping = np.arange(5)

    def forward(self, features):
        self.calls.append(np.asarray(features).copy())
        output = []
        for row in features:
            # Three dependent recurrent states expose encoder/pose history.
            self.state[0] = .7*self.state[0]+row[0, 0]
            self.state[1] = .8*self.state[1]+self.state[0]
            self.state[2] = .9*self.state[2]+self.state[1]
            output.append(self.state.copy())
        return {'prediction':np.stack(output)}


def test_gap_poison_cannot_change_later_predictions_or_hidden_state():
    features = np.random.default_rng(1).normal(size=(31, 5, 9))
    valid = np.ones(31, dtype=bool)
    valid[4:12] = False
    valid[20:23] = False
    valid[-2:] = False
    poisoned = features.copy()
    poisoned[~valid] = np.nan
    expected = RecordingStream()
    compact, _ = expected.run(features[valid], chunk_size=3)
    actual = RecordingStream()
    result, audit = actual.run(poisoned, input_valid=valid, chunk_size=5)
    np.testing.assert_array_equal(result['prediction'][valid], compact['prediction'])
    np.testing.assert_array_equal(actual.state, expected.state)
    for i in np.flatnonzero(~valid):
        np.testing.assert_array_equal(result['prediction'][i], result['prediction'][i-1])
    assert len(result['prediction']) == len(features)
    assert sum(len(x) for x in actual.calls) == valid.sum()
    assert audit['recurrent_update_frames'] == valid.sum()
    assert audit['withheld_invalid_frames'] == (~valid).sum()
    assert not audit['recurrence_reset_between_chunks']
    assert not audit['elapsed_time_during_gap_modeled']


def test_all_valid_preserves_original_batches_and_values():
    features = np.random.default_rng(2).normal(size=(19, 5, 9))
    old = RecordingStream()
    expected = np.concatenate([old.forward(features[i:i+7])['prediction'] for i in range(0, 19, 7)])
    stream = RecordingStream()
    result, audit = stream.run(features, chunk_size=7, input_valid=np.ones(19, dtype=bool))
    np.testing.assert_array_equal(result['prediction'], expected)
    assert [len(x) for x in stream.calls] == [7, 7, 5]
    assert audit['withheld_invalid_frames'] == 0


@pytest.mark.parametrize('chunk_size', [1, 2, 7, 120])
def test_gap_results_do_not_depend_on_chunk_boundaries(chunk_size):
    features = np.random.default_rng(3).normal(size=(20, 5, 9))
    valid = np.array([True]*3+[False]*8+[True]*7+[False]*2)
    expected, _ = RecordingStream().run(features, chunk_size=1, input_valid=valid)
    result, _ = RecordingStream().run(features, chunk_size=chunk_size, input_valid=valid)
    np.testing.assert_array_equal(result['prediction'], expected['prediction'])


@pytest.mark.parametrize('valid', [np.zeros(4, dtype=bool), np.ones(3, dtype=bool),
                                  np.ones(4), np.ones((4,1), dtype=bool)])
def test_bad_mask_fails_before_any_state_update(valid):
    stream = RecordingStream()
    with pytest.raises(ValueError):
        stream.run(np.zeros((4,5,9)), input_valid=valid)
    assert not stream.calls
    np.testing.assert_array_equal(stream.state, 0)


@pytest.mark.parametrize('chunk_size', [0, -1, True, 1.5])
def test_bad_chunk_size_fails_before_update(chunk_size):
    stream = RecordingStream()
    with pytest.raises(ValueError, match='chunk_size'):
        stream.run(np.zeros((4,5,9)), chunk_size=chunk_size)
    assert not stream.calls
