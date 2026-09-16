from pathlib import Path

import numpy as np

import run_c2_robust_authoritative_root_u8_action04 as runner
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import DirectOwnerSequence
from test_c2_owner_bound_async_worker import sequence


def test_u8_runner_binds_revision002_transport_and_current_source_hashes_without_raw():
    runner.legacy.verify_seal(runner.REVISION_002,runner.REVISION_002_SEAL)
    runner.legacy.verify_seal(runner.TRANSPORT_REVISION,runner.TRANSPORT_REVISION_SEAL)
    assert runner.REVISION_002_SEAL=="9e24eba8b1d5b6369709ac42fdef288cb271d1d02bf4161583011364f9adcfd2"
    assert runner.TRANSPORT_REVISION_SEAL=="e2fca0e42acb5cc772aff163bd29679a07471d0dabb0c5e8fa87c6a236a72ba8"
    for relative,digest in runner.EXPECTED_SOURCE_SHA256.items():
        assert runner.legacy.sha256(runner.ROOT/relative)==digest


def test_u8_command_is_the_exact_authorized_one_shot_identity():
    output=Path("logs/c2_robust_authoritative_root_u8_action04_20260906T210000Z")
    assert runner._command(output)==(
        "/usr/bin/time -v timeout --signal=TERM --kill-after=5s 300s env OMP_NUM_THREADS=1 "
        "OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONPATH=src:tools:. "
        ".venv-v0/bin/python tools/run_c2_robust_authoritative_root_u8_action04.py --output "
        "logs/c2_robust_authoritative_root_u8_action04_20260906T210000Z")
    source=Path(runner.__file__).read_text()
    assert "run_c2_owner_bound_async_worker_u7e7" not in source


def test_u8_exact_comparator_covers_robust_publication_contract():
    owner,items=sequence();left=DirectOwnerSequence(owner);right=DirectOwnerSequence(owner)
    for item in items:
        a=left.process(item);b=right.process(item)
        assert runner._exact_result(a,b)
        if a.kind=="UWB":
            assert len(a.effective_r)==len(a.effective_s)==len(a.robust_nis)==10
            assert len(a.external_information_weights)==10
            assert set(a.cross_covariance_status)=={"UNAVAILABLE_NOT_PROPAGATED"}
            broken=type(a)(**{**a.__dict__,"state":a.state+np.r_[1e-9,np.zeros(8)]})
            assert not runner._exact_result(a,broken)
            break


def test_action_pose_owner_is_action_only_and_lazy_without_uwb_raw():
    trajectory,owners,audit=runner._verified_action_pose_inputs()
    assert set(owners)=={runner.ACTION} and set(trajectory["trajectory"])==set()
    assert audit["loaded_actions"]==[runner.ACTION]
    assert audit["raw_uwb_opened"] is False and audit["H01_H02_opened_or_hashed"] is False
