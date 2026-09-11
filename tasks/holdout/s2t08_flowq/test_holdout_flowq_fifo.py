"""Held-out verifier: first come, first served among equal priority - including
retries and across a crash/resume."""

import json

from flowq import Engine, persistence, spec


def wf(jobs):
    return spec.parse({"version": 2, "jobs": jobs})


def starts(eng):
    return [(e.job, e.t) for e in eng.log.of_kind("started")]


def test_c1_earliest_ready_job_starts_first():
    w = wf({"first": {"duration_s": 2, "priority": 9}, "zeta": {"duration_s": 1},
            "alpha": {"deps": ["first"], "duration_s": 1}})
    eng = Engine(w, max_parallel=1)
    eng.run()
    assert starts(eng) == [("first", 0), ("zeta", 2), ("alpha", 3)]


def test_c2_a_retried_job_queues_behind_jobs_already_waiting():
    w = wf({"r": {"duration_s": 1, "retry": {"max_attempts": 2, "backoff": {"initial_s": 3}}},
            "w": {"duration_s": 1}, "x": {"duration_s": 5}, "z": {"deps": ["w"], "duration_s": 1}})
    eng = Engine(w, {"r": lambda name, attempt: attempt == 2}, max_parallel=1)
    eng.run()
    assert starts(eng) == [("r", 0), ("w", 1), ("x", 2), ("z", 7), ("r", 8)]


def test_c3_priority_first_then_ready_time_then_name():
    w = wf({"b": {"duration_s": 1}, "a": {"duration_s": 1}, "gate": {"duration_s": 2, "priority": 1},
            "late": {"deps": ["gate"], "duration_s": 1, "priority": 4}})
    eng = Engine(w, max_parallel=1)
    eng.run()
    assert starts(eng) == [("gate", 0), ("late", 2), ("a", 3), ("b", 4)]


def test_c4_resume_keeps_the_waiting_order():
    w = wf({"first": {"duration_s": 2, "priority": 9}, "ant": {"duration_s": 3},
            "yak": {"deps": ["first"], "duration_s": 1}})
    first = Engine(w, max_parallel=1)
    first.run(until=4)
    snap = json.loads(json.dumps(persistence.snapshot(first)))
    second = persistence.restore(w, snap)
    second.run()
    assert starts(first) == [("first", 0), ("ant", 2)]
    assert starts(second) == [("yak", 4), ("ant", 5)]


def test_c4_several_waiting_jobs_across_a_resume():
    jobs = {"hold": {"duration_s": 10, "priority": 9}}
    for i, name in enumerate(["kiwi", "fig", "date", "cherry"]):
        jobs[f"t{i}"] = {"duration_s": i + 1, "priority": 5}
        jobs[name] = {"deps": [f"t{i}"], "duration_s": 1}
    w = wf(jobs)
    full = Engine(w, max_parallel=2)
    full.run()
    expected = [j for j, _t in starts(full)]
    first = Engine(w, max_parallel=2)
    first.run(until=7)
    second = persistence.restore(w, json.loads(json.dumps(persistence.snapshot(first))))
    second.run()
    resumed = [j for j, _t in starts(first) if j not in {j2 for j2, _ in starts(second)}]
    assert [j for j in expected if j in ("kiwi", "fig", "date", "cherry")] == ["kiwi", "fig", "date", "cherry"]
    assert [j for j, _t in starts(second) if j in ("kiwi", "fig", "date", "cherry")] == [
        j for j in ("kiwi", "fig", "date", "cherry") if j not in resumed]


def test_r_plan_and_capacity_rules_unchanged():
    w = wf({"b": {}, "a": {}, "c": {"priority": 5}, "d": {"deps": ["a", "b"]}})
    assert w.plan() == ["c", "a", "b", "d"]
    old = {"version": 1, "t": 5, "max_parallel": 1,
           "jobs": {"x": {"state": "ready", "attempts": 0, "retry_at": None},
                    "y": {"state": "succeeded", "attempts": 1, "retry_at": None}}}
    eng = persistence.restore(wf({"x": {"duration_s": 2}, "y": {}}), old)
    eng.run()
    assert starts(eng) == [("x", 5)]
