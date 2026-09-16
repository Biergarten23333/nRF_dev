from benchmark_c2_clock_adaptation import _evaluate, _measure_interval, _run, _summary


def test_clock_benchmark_summary_enforces_count_duration_and_reports_cv():
    result = _summary([1.0, 1.1, 0.9, 1.0, 1.0])
    assert result["median_s"] == 1.0
    assert 0.0 < result["cv"] < 0.15
    assert result["duration_ok"] is True
    summaries, ratio, passed = _evaluate({
        "scalar": [1.0] * 5, "batch": [0.49] * 5,
    })
    assert summaries["batch"]["duration_ok"] is False
    assert ratio == 0.49 and passed is False


def test_measured_interval_executes_exactly_four_fresh_passes():
    calls = []

    def run(mode, workload, clock, *, collect):
        calls.append((mode, workload, clock, collect))

    serial = iter(range(4))
    assert _measure_interval("batch", ("prebuilt",), run=run,
                             clock_factory=lambda: next(serial)) >= 0.0
    assert calls == [("batch", ("prebuilt",), index, False) for index in range(4)]


def test_each_mode_run_constructs_one_immutable_owner_and_reuses_it():
    for mode in ("scalar", "batch"):
        observed = []

        def one(received_mode, rows, clock, previous, region_owner):
            observed.append((received_mode, id(region_owner),
                             id(region_owner.full_session), rows, previous))
            return (), 1, 1

        _run(mode, (("r1",), ("r2",), ("r3",)), object(),
             collect=False, one=one)
        assert {row[0] for row in observed} == {mode}
        assert len({row[1] for row in observed}) == 1
        assert len({row[2] for row in observed}) == 1
        assert [row[3] for row in observed] == [("r1",), ("r2",), ("r3",)]
