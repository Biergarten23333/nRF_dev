import json

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world import archive_persistence as writer


def test_uncompressed_roundtrip_preserves_all_dtypes(tmp_path):
    arrays = {'float':np.array([np.nan, -0., 3.],dtype=np.float64),
              'uint':np.array([65535],dtype=np.uint16),
              'text':np.array(['one','two']), 'bool':np.array([True,False]),
              'scalar':np.asarray(1.25), 'empty':np.empty((0,3))}
    writer.atomic_npz(tmp_path/'core.npz', arrays)
    with np.load(tmp_path/'core.npz',allow_pickle=False) as loaded:
        assert set(loaded.files)==set(arrays)
        for key,value in arrays.items():
            assert loaded[key].dtype==value.dtype
            assert loaded[key].shape==value.shape
            assert loaded[key].tobytes()==value.tobytes()


def test_interrupted_archive_preserves_previous_final(tmp_path, monkeypatch):
    path=tmp_path/'core.npz'
    writer.atomic_npz(path, {'x':np.arange(3)})
    previous=path.read_bytes()
    def fail(stream, **arrays):
        stream.write(b'incomplete')
        raise InterruptedError('simulated interrupted writer')
    monkeypatch.setattr(writer.np,'savez',fail)
    with pytest.raises(InterruptedError):
        writer.atomic_npz(path, {'x':np.arange(4)})
    assert path.read_bytes()==previous
    assert [p.name for p in tmp_path.iterdir()]==['core.npz']


def test_interrupted_first_archive_has_no_final(tmp_path, monkeypatch):
    def fail(stream, **arrays):
        stream.write(b'incomplete')
        raise InterruptedError()
    monkeypatch.setattr(writer.np,'savez',fail)
    with pytest.raises(InterruptedError):
        writer.atomic_npz(tmp_path/'core.npz', {})
    assert not list(tmp_path.iterdir())


def test_auxiliary_failure_leaves_core_and_incomplete_manifest(tmp_path):
    writer.atomic_npz(tmp_path/'CONTINUOUS_AB.npz',{'x':np.arange(2)})
    writer.atomic_json(tmp_path/'COVERAGE.json',{'core_complete':True,
        'ancillary_complete':False,'numerical_complete':False})
    def fail(staging):
        assert (tmp_path/'CONTINUOUS_AB.npz').exists()
        writer.atomic_json(staging/'first.json',{'partial':True})
        raise InterruptedError()
    with pytest.raises(InterruptedError):
        writer.persist_ancillary(tmp_path,fail)
    assert not (tmp_path/'first.json').exists()
    assert json.loads((tmp_path/'COVERAGE.json').read_text())=={
        'core_complete':True,'ancillary_complete':False,'numerical_complete':False}


def test_auxiliary_success_promotes_complete_files(tmp_path):
    writer.persist_ancillary(tmp_path,lambda staging:
        writer.atomic_npz(staging/'CONTACT.npz',{'x':np.arange(2)}))
    with np.load(tmp_path/'CONTACT.npz') as result:
        np.testing.assert_array_equal(result['x'],np.arange(2))
    assert len(list(tmp_path.iterdir()))==1
