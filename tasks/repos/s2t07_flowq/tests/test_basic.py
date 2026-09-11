import pytest

from flowq import Engine, SpecError, persistence, report, spec


def wf(jobs, backoff_s=1):
    return spec.parse({"backoff_s": backoff_s, "jobs": jobs})


def test_parse_defaults_and_names():
    w = wf({"Build": {}, "Test": {"deps": ["build"], "duration": 5, "priority": 3}})
    b = w.job("BUILD")
    assert (b.duration_s, b.priority) == (1.0, 0) and w.job("test").duration_s == 5
    assert w.job("test").dep_keys == ("build",)
    assert [j.name for j in w] == ["Build", "Test"]


def test_invalid_documents():
    with pytest.raises(SpecError):
        wf({"a": {}, "A": {}})
    with pytest.raises(SpecError):
        wf({"a": {"deps": ["missing"]}})
    with pytest.raises(SpecError):
        wf({"a": {"deps": ["b"]}, "b": {"deps": ["a"]}})
    with pytest.raises(SpecError):
        wf({"a": {"colour": "red"}})
    with pytest.raises(SpecError):
        spec.parse({"version": 9, "jobs": {}})


def test_plan_orders_by_priority_then_name():
    w = wf({"b": {}, "a": {}, "c": {"priority": 5}, "d": {"deps": ["a", "b"]}})
    assert w.plan() == ["c", "a", "b", "d"]


def test_chain_runs_in_dependency_order():
    w = wf({"build": {"duration": 10}, "test": {"deps": ["build"], "duration": 5},
            "ship": {"deps": ["test"], "duration": 2}})
    eng = Engine(w)
    s = eng.run()
    assert s["succeeded"] == ["build", "test", "ship"]
    assert s["t"] == 17
    assert [(e.job, e.t) for e in eng.log.of_kind("started")] == [("build", 0), ("test", 10), ("ship", 15)]


def test_retries_with_backoff():
    calls = []

    def flaky(name, attempt):
        calls.append(attempt)
        return attempt >= 3

    w = wf({"fetch": {"duration": 2, "retries": 2}}, backoff_s=5)
    eng = Engine(w, {"fetch": flaky})
    s = eng.run()
    assert calls == [1, 2, 3] and s["succeeded"] == ["fetch"]
    assert [e.t for e in eng.log.of_kind("started")] == [0, 7, 19]


def test_failure_skips_descendants():
    w = wf({"a": {}, "b": {"deps": ["a"]}, "c": {"deps": ["b"]}, "d": {}})
    s = Engine(w, {"a": lambda name, attempt: False}).run()
    assert (s["failed"], s["skipped"], s["succeeded"]) == (["a"], ["b", "c"], ["d"])


def test_parallelism_is_limited():
    w = wf({n: {"duration": 4} for n in "abcde"})
    eng = Engine(w, max_parallel=2)
    s = eng.run()
    assert [(e.job, e.t) for e in eng.log.of_kind("started")] == [
        ("a", 0), ("b", 0), ("c", 4), ("d", 4), ("e", 8)]
    assert s["t"] == 12


def test_higher_priority_starts_first():
    w = wf({"low": {"duration": 3}, "high": {"duration": 3, "priority": 9},
            "mid": {"duration": 3, "priority": 5}})
    eng = Engine(w, max_parallel=1)
    eng.run()
    assert eng.log.started() == ["high", "mid", "low"]


def test_resume_after_crash_does_not_repeat_finished_work():
    w = wf({"build": {"duration": 10}, "test": {"deps": ["build"], "duration": 10},
            "ship": {"deps": ["test"], "duration": 5}})
    first = Engine(w)
    first.run(until=15)
    snap = persistence.snapshot(first)
    assert snap["jobs"]["build"]["state"] == "succeeded"
    second = persistence.restore(w, snap)
    s = second.run()
    assert second.log.started() == ["test", "ship"]
    assert s["succeeded"] == ["build", "test", "ship"] and s["t"] == 30


def test_negative_backoff_and_retries_are_rejected():
    with pytest.raises(SpecError):
        wf({"a": {}}, backoff_s=-1)
    with pytest.raises(SpecError):
        wf({"a": {"retries": -1}})


def test_summary_and_render():
    s = Engine(wf({"Build Docs": {"duration": 2}})).run()
    text = report.render(s)
    assert "Build Docs" in text and "succeeded" in text
