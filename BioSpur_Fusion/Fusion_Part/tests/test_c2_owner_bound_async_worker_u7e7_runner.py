from pathlib import Path
import queue
import sys
import threading
import time

import numpy as np
import pytest

import run_c2_owner_bound_async_worker_u7e6_action04 as u7e6
import run_c2_owner_bound_async_worker_u7e7_action04 as u7e7


def test_u7e6_default_command_is_byte_unchanged(monkeypatch):
    output = Path("logs/fresh")
    expected = (
        "timeout --signal=TERM --kill-after=5s 300s env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "
        "MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONPATH=src:tools:. .venv-v0/bin/python "
        "tools/run_c2_owner_bound_async_worker_u7e6_action04.py --output logs/fresh"
    )
    assert u7e6._resolve_command(output) == expected


def test_u7e7_command_binds_executed_identity_argv_and_output(monkeypatch):
    identity = u7e6.U7E7_RUNNER.resolve()
    output = Path("logs/exact-u7e7-output")
    monkeypatch.setattr(sys, "argv", [str(identity), "--output", str(output)])
    expected = u7e6._build_command(identity, output)
    assert u7e6._resolve_command(output, expected, identity) == expected
    assert "tools/run_c2_owner_bound_async_worker_u7e7_action04.py" in expected
    assert expected.endswith("--output logs/exact-u7e7-output")


@pytest.mark.parametrize("command,identity", (("x", None), (None, Path("x"))))
def test_one_sided_authority_rejects(command, identity):
    with pytest.raises(ValueError, match="supplied together"):
        u7e6._resolve_command(Path("logs/fresh"), command, identity)


def test_arbitrary_command_and_spoof_identity_reject(monkeypatch):
    identity = u7e6.U7E7_RUNNER.resolve(); output = Path("logs/fresh")
    monkeypatch.setattr(sys, "argv", [str(identity)])
    with pytest.raises(ValueError, match="deterministic"):
        u7e6._resolve_command(output, "python arbitrary.py", identity)
    spoof = identity.with_name("spoof.py")
    monkeypatch.setattr(sys, "argv", [str(spoof)])
    with pytest.raises(ValueError, match="executed U7E7"):
        u7e6._resolve_command(output, u7e6._build_command(spoof, output), spoof)


def test_u7e7_action_only_lazy_pose_owner_constructs_without_raw_access():
    trajectory, owners, audit = u7e7._verified_action_pose_inputs()
    assert set(owners) == {u7e7.ACTION}
    assert owners[u7e7.ACTION].action == u7e7.ACTION
    assert len(trajectory["trajectory"]) == 0
    assert audit["loaded_actions"] == [u7e7.ACTION]
    assert audit["deferred_trajectory_materialization"] is True
    assert audit["raw_uwb_opened"] is False
    assert audit["H01_H02_opened_or_hashed"] is False


def test_u7e7_future_contract_binds_failures_and_promoted_diagnostic_reference(monkeypatch):
    monkeypatch.setattr(u7e7.replay, "SEALS", {})
    monkeypatch.setattr(u7e7.replay, "FILES", ())
    monkeypatch.setattr(u7e7.replay, "PRIOR_BLOCKED_NON_PROMOTED", False)
    u7e7._bind_prior_failures()
    assert u7e7.replay.SEALS[u7e7.BLOCKED_REVISION_003] == u7e7.BLOCKED_REVISION_003_SHA256
    assert u7e7.BLOCKED_REVISION_003_SHA256 == "7a9984a77b3dc8745bfa69d624ca5dac4a4e645eec523051043c4169ef20a5f8"
    assert u7e7.replay.SEALS[u7e7.BLOCKED_REVISION_004] == u7e7.BLOCKED_REVISION_004_SHA256
    assert u7e7.replay.SEALS[u7e7.REFERENCE_PREFLIGHT] == u7e7.REFERENCE_PREFLIGHT_SHA256
    assert u7e7.replay.PRIOR_BLOCKED_NON_PROMOTED is True
    assert u7e7.replay.CURRENT_CONTRACT_REFERENCE == u7e7.CURRENT_CONTRACT_REFERENCE
    assert u7e7.replay.CURRENT_CONTRACT_REFERENCE_SHA256 == u7e7.CURRENT_CONTRACT_REFERENCE_SHA256
    assert u7e7.replay.CURRENT_CONTRACT_REFERENCE_CLASS == "CURRENT_CONTRACT_DIAGNOSTIC_REFERENCE"
    assert u7e7.replay.CURRENT_CONTRACT_REFERENCE_PROMOTED is True
    assert all(value.endswith("NON_PROMOTED")
        for value in u7e7.replay.HISTORICAL_NUMERIC_REFERENCE_STATUS.values())


def test_promoted_current_contract_reference_is_complete_and_hash_bound():
    u7e7._bind_prior_failures()
    rows = u7e6._load_current_contract_reference()
    assert len(rows) == 41
    assert sum(len(row["nodes"]) for row in rows) == 410
    assert u7e6.legacy.sha256(u7e7.CURRENT_CONTRACT_REFERENCE) == u7e7.CURRENT_CONTRACT_REFERENCE_SHA256


def test_current_contract_reference_comparison_is_node_keyed_and_exact():
    class Actual:
        state = np.arange(9., dtype=float)
        decision = "ACCEPTED"
        link_count = 16
        diagnostic_decisions = (("node-b", False, "REJECTED"), ("node-a", True, "ACCEPTED"))
        diagnostic_weights = (("node-b", np.full(8, .2)), ("node-a", np.full(8, .1)))
        nis = (2., 1.)
        condition = (4., 3.)
        rank = (2, 3)

    reference = {"root_state": list(np.arange(6., dtype=float)), "transaction_reason": "ACCEPTED",
        "link_count": 16, "nodes": [
            {"node":"node-a", "decision":["node-a",True,"ACCEPTED"], "rank":3,
             "prior_nis":1., "condition":3., "weights":[.1]*8},
            {"node":"node-b", "decision":["node-b",False,"REJECTED"], "rank":2,
             "prior_nis":2., "condition":4., "weights":[.2]*8},
        ]}
    errors = u7e6._compare_current_contract_reference([Actual()]*41, [reference]*41)
    assert errors == {"root_first6":0., "nis":0., "condition":0., "weights":0.}
    broken = dict(reference); broken["link_count"] = 15
    with pytest.raises(RuntimeError, match="group discrete mismatch"):
        u7e6._compare_current_contract_reference([Actual()]*41, [broken]*41)


def test_interleaved_output_drain_is_ordered_lossless_beyond_two_queue_capacities():
    class ProcessView:
        exitcode = None
        def __init__(self, thread): self.thread = thread
        def join(self, timeout):
            self.thread.join(timeout)
            if not self.thread.is_alive(): self.exitcode = 0
        def is_alive(self): return self.thread.is_alive()

    class Worker:
        def __init__(self):
            self._in=queue.Queue(64);self._out=queue.Queue(64);self._closed=False;self.hwm=0;self.submit_ms=[]
            def run():
                count=0
                while True:
                    value=self._in.get()
                    if value is None: break
                    self._out.put(("RESULT",value));count+=1
                self._out.put(("FINAL",{"count":count,"sentinel":True,"rss":0}))
            self.thread=threading.Thread(target=run,name="u7e7-fixture-worker",daemon=False);self.thread.start();self._p=ProcessView(self.thread)
        def submit(self,item):
            started=time.perf_counter_ns();self._in.put(item,timeout=1);self.submit_ms.append((time.perf_counter_ns()-started)*1e-6);self.hwm=max(self.hwm,self._in.qsize())
        def _close_queues(self): pass
        def abort(self):
            try:self._in.put_nowait(None)
            except queue.Full:pass
            self.thread.join(2);self._closed=True

    worker=Worker();timeline=[(float(index),0,index) for index in range(160)]
    results,final=u7e6._submit_and_collect_interleaved(worker,timeline,wall_paced=False)
    assert results==list(range(160)) and final["count"]==160 and final["sentinel"]
    assert final["qsize"]==0 and final["drain_thread_alive"] is False
    assert not worker.thread.is_alive() and worker._closed is True
    assert not any(thread.name in {"u7e7-output-drain","u7e7-fixture-worker"} for thread in threading.enumerate())


def test_final_group_overhang_selects_context_only_imus_without_relabeling():
    rows = [{"time_s": value, "identity": index} for index, value in enumerate((
        .005, .010, .015, .020, .025, .030, .035, .040, .045, .050, .055, .060,
    ))]
    metric, context, submitted = u7e6._select_metric_and_context_imu_rows(
        rows, start_s=0., metric_stop_s=.050, final_group_availability_s=.063,
    )
    assert [row["time_s"] for row in metric] == [.005,.010,.015,.020,.025,.030,.035,.040,.045]
    assert [row["time_s"] for row in context] == [.050,.055,.060]
    assert submitted == metric + context
    assert all(selected is rows[selected["identity"]] for selected in submitted)
    assert [row["time_s"] for row in rows] == [.005,.010,.015,.020,.025,.030,.035,.040,.045,.050,.055,.060]


def test_context_imu_omission_and_over_horizon_fail_closed():
    rows = [{"time_s": value} for value in (.005,.010,.015,.020,.025,.030,.035,.040,.045,.050)]
    with pytest.raises(RuntimeError, match="coverage incomplete"):
        u7e6._select_metric_and_context_imu_rows(
            rows, start_s=0., metric_stop_s=.050, final_group_availability_s=.061,
        )
    with pytest.raises(ValueError, match="context horizon exceeded"):
        u7e6._select_metric_and_context_imu_rows(
            rows, start_s=0., metric_stop_s=.050,
            final_group_availability_s=.050 + u7e6.DIAGNOSTIC_HORIZON_S + 1e-9,
        )


def test_context_results_are_excluded_from_scored_inventory():
    class Result:
        def __init__(self, kind, sequence):
            self.kind = kind; self.sequence = sequence
    actual = [Result("IMU", 1), Result("UWB", 10), Result("IMU", 2), Result("IMU", 3)]
    expected = [Result("IMU", 1), Result("UWB", 10), Result("IMU", 2), Result("IMU", 3)]
    scored, context = u7e6._partition_scored_and_context_results(
        actual, expected, metric_imu_sequences={1, 2})
    assert [(value.kind, value.sequence) for value, _ in scored] == [
        ("IMU", 1), ("UWB", 10), ("IMU", 2)]
    assert [(value.kind, value.sequence) for value, _ in context] == [("IMU", 3)]
