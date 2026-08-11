import json

import numpy as np

from analyze_v47_c2cc_rotation import amend_shortened_protocol, clean, first_quiet, first_run, sustained_runs


def test_sustained_run_rejects_short_spike():
    t=np.arange(0,2,.1);flag=(t>=.5)&(t<.6)
    assert sustained_runs(t,flag,.2)==[]


def test_independent_motion_interval():
    t=np.arange(0,3,.1);flag=(t>=.5)&(t<=1.5)
    runs=sustained_runs(t,flag,.2)
    assert first_run(runs,.4,2)==.5


def test_motor_off_is_not_settle():
    t=np.arange(0,5,.1);motion=t<3
    assert first_quiet(t,motion,1,1)>=3


def test_interval_can_remain_ambiguous():
    assert first_run([],0,1) is None


def test_nested_numpy_values_are_json_safe():
    assert clean({"flag": np.bool_(True), "count": [np.int64(2)]}) == {"flag": True, "count": [2]}


def test_operator_shortening_requires_and_preserves_final_static(tmp_path):
    (tmp_path/"RUN_MANIFEST.json").write_text(json.dumps({"stop_reason":"STOPPED_BY_OPERATOR"}))
    rows=[{"token":"END_SEQUENCE_AFTER_CYCLE_2","monotonic":10.0,"wall":"a"},
          {"token":"ABORT_MOTOR_TEST","monotonic":75.0,"wall":"b"}]
    (tmp_path/"OPERATOR_TOKENS.jsonl").write_text("\n".join(json.dumps(x) for x in rows)+"\n")
    result=amend_shortened_protocol(tmp_path)
    assert result["final_stationary_duration_s"]==65
    assert json.loads((tmp_path/"RUN_MANIFEST.json").read_text())["stop_reason"]=="OPERATOR_SHORTENED_PROTOCOL"
