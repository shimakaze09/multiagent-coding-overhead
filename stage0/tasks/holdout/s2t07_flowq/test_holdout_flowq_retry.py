"""Held-out verifier: version-2 retry policies, with version 1 kept exactly."""

import json
import warnings

import pytest

import flowq
from flowq import Engine, SpecError, persistence, spec


def RetryPolicy(*args):  # noqa: N802 - resolved per test, so each test fails on its own
    return flowq.RetryPolicy(*args)


def v2(jobs):
    return spec.parse({"version": 2, "jobs": jobs})


V1_DOC = {"backoff_s": 3, "jobs": {
    "fetch": {"duration": 2, "retries": 3},
    "parse": {"deps": ["fetch"], "duration": 4, "retries": 1, "priority": 2},
    "report": {"deps": ["parse"], "duration": 1}}}


def v1():
    with pytest.warns(DeprecationWarning):
        return spec.parse(json.loads(json.dumps(V1_DOC)))


def test_c1_version_2_retry_policies():
    w = v2({"a": {"retry": {"max_attempts": 4, "backoff": {"initial_s": 2, "factor": 3, "max_s": 50}}},
            "b": {}})
    assert w.job("a").retry == RetryPolicy(4, 2.0, 3.0, 50.0)
    assert w.job("b").retry == RetryPolicy()
    for bad in ({"max_attempts": 0}, {"backoff": {"factor": 0.5}}, {"backoff": {"initial_s": -1}}):
        with pytest.raises(SpecError):
            v2({"a": {"retry": bad}})


def test_c2_backoff_and_the_engine_follow_the_policy():
    p = RetryPolicy(5, 2, 3, 50)
    assert [p.delay(n) for n in range(1, 5)] == [2, 6, 18, 50]
    w = v2({"a": {"duration_s": 1,
                  "retry": {"max_attempts": 4, "backoff": {"initial_s": 2, "factor": 3, "max_s": 5}}}})
    eng = Engine(w, {"a": lambda name, attempt: attempt == 4})
    assert eng.run()["succeeded"] == ["a"]
    assert [e.t for e in eng.log.of_kind("started")] == [0, 3, 9, 15]


def test_c3_version_1_means_retries_plus_one():
    w = v1()
    assert w.job("fetch").retry == RetryPolicy(4, 3.0, 2.0, 60.0)
    assert w.job("parse").retry == RetryPolicy(2, 3.0, 2.0, 60.0)
    assert w.job("report").retry.max_attempts == 1


def test_c3_version_1_timeline_is_unchanged():
    fails = {"fetch": {1, 2}, "parse": {1}}
    handlers = {n: (lambda name, attempt, f=f: attempt not in f) for n, f in fails.items()}
    eng = Engine(v1(), handlers)
    eng.run()
    kinds = ("started", "retry_scheduled", "succeeded", "failed")
    assert [(e.kind, e.job, e.t) for e in eng.log if e.kind in kinds] == [
        ("started", "fetch", 0), ("retry_scheduled", "fetch", 2), ("started", "fetch", 5),
        ("retry_scheduled", "fetch", 7), ("started", "fetch", 13), ("succeeded", "fetch", 15),
        ("started", "parse", 15), ("retry_scheduled", "parse", 19), ("started", "parse", 22),
        ("succeeded", "parse", 26), ("started", "report", 26), ("succeeded", "report", 27)]
    eng = Engine(v1(), {"report": lambda name, attempt: False})
    s = eng.run()
    assert s["failed"] == ["report"] and len(eng.log.of_kind("started")) == 3


def test_c4_dump_writes_version_2_and_round_trips():
    w = v1()
    doc = w.dump()
    assert doc["version"] == 2
    assert doc["jobs"]["fetch"]["retry"] == {"max_attempts": 4,
                                             "backoff": {"initial_s": 3.0, "factor": 2.0, "max_s": 60.0}}
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        again = spec.parse(json.loads(json.dumps(doc)))
    key = lambda j: (j.name, tuple(j.deps), j.duration_s, j.priority, j.retry)  # noqa: E731
    assert [key(j) for j in again] == [key(j) for j in w]


def test_c5_retries_is_a_deprecated_alias():
    w = v2({"a": {"retry": {"max_attempts": 3}}})
    with pytest.warns(DeprecationWarning):
        assert w.job("a").retries == 2


def test_c6_version_2_is_silent_and_strict():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        w = v2({"a": {"duration_s": 2}})
        Engine(w).run()
    with pytest.raises(SpecError):
        spec.parse({"version": 3, "jobs": {}})
    with pytest.raises(SpecError):
        v2({"a": {"retries": 2}})
    with pytest.raises(SpecError):
        v2({"a": {"duration": 2}})


def test_r_resume_keeps_retry_state():
    w = v2({"a": {"duration_s": 2, "retry": {"max_attempts": 3, "backoff": {"initial_s": 10}}}})
    handlers = {"a": lambda name, attempt: attempt == 3}
    first = Engine(w, handlers)
    first.run(until=5)
    second = persistence.restore(w, json.loads(json.dumps(persistence.snapshot(first))), handlers)
    s = second.run()
    assert [(e.t, e.data["attempt"]) for e in second.log.of_kind("started")] == [(12, 2), (34, 3)]
    assert s["succeeded"] == ["a"]
