"""Shared calibration remains five-C2-only across a new process session."""
import json
import os
import subprocess
import sys

import pytest

import run_c2_five_isolated as isolated


@pytest.mark.parametrize('stage', ['shared-probe', 'shared-fit', 'shared-c2-probe',
                                 'shared-c2-resource', 'shared-c2-replay'])
def test_shared_stages_do_not_mount_holdout_or_reference(tmp_path, monkeypatch, stage):
    monkeypatch.setattr(isolated, 'ROOT', tmp_path)
    out = tmp_path/'logs'/'shared'
    out.mkdir(parents=True)
    (out/'TASK_CONTRACT.json').write_text(json.dumps({'schema':'synthetic'}))
    readonly, data, _ = isolated.mount_plan(out, stage)
    assert {p.name for p in data} == {
        'v47_subject_surface_anthropometry_20260828.json',
        'CALIBRATION_CONTINUOUS_INPUT.npz', 'CALIBRATION_INPUT_AUDIT.json'}
    assert all('HOLDOUT' not in str(p) and 'REFERENCE' not in str(p) for p in readonly+data)
    (out/'REFERENCE.json').write_text('{}')
    with pytest.raises(ValueError, match='outside isolated'):
        isolated.mount_plan(out, stage)


def test_shared_holdout_mounts_exact_five_input_and_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(isolated, 'ROOT', tmp_path)
    out = tmp_path/'logs'/'shared'
    out.mkdir(parents=True)
    (out/'TASK_CONTRACT.json').write_text('{}')
    _, data, _ = isolated.mount_plan(out, 'shared-h')
    assert {p.name for p in data if 'HOLDOUT' in p.name} == {
        'HOLDOUT_CONTINUOUS_INPUT.npz', 'HOLDOUT_INPUT_AUDIT.json'}
    assert all('REFERENCE' not in str(p) for p in data)
    assert '--diagnostic-only' in isolated.STAGES['shared-h'][1]


def test_rss_watchdog_sees_child_in_a_different_session():
    child = subprocess.Popen([sys.executable, '-c',
        'import os,time; os.setsid(); print(os.getpid(), flush=True); time.sleep(10)'],
        stdout=subprocess.PIPE, text=True)
    try:
        pid = int(child.stdout.readline())
        assert os.getpgid(pid) != os.getpgid(os.getpid())
        descendants, rss = isolated.descendant_memory(os.getpid())
        assert pid in descendants and rss > 0
    finally:
        child.terminate()
        child.wait(timeout=5)
