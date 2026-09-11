"""Held-out verifier: per-resource capacity across spec, scheduler, engine,
snapshots and reports."""

import json

import pytest

from flowq import Engine, SpecError, persistence, report, spec


def wf(jobs):
    return spec.parse({"version": 2, "jobs": jobs})


def intervals(eng):
    starts, out = {}, []
    for e in eng.log:
        if e.kind == "started":
            starts[e.job] = e.t
        elif e.kind in ("succeeded", "failed", "retry_scheduled"):
            out.append((starts.pop(e.job), e.t, eng.workflow.job(e.job).resource_map()))
    return out


def peak(ivs, resource):
    points = sorted({t for s, f, _ in ivs for t in (s, f)})
    return max((sum(r.get(resource, 0) for s, f, r in ivs if s <= t < f) for t in points), default=0)


def test_c1_resources_are_declared_validated_and_dumped():
    w = wf({"train": {"resources": {"cpu": 2, "gpu": 1}}, "etl": {}})
    assert w.job("train").resource_map() == {"cpu": 2, "gpu": 1}
    assert w.job("etl").resource_map() == {"cpu": 1}
    for bad in ({"cpu": -1}, {}, {"cpu": 1.5}):
        with pytest.raises(SpecError):
            wf({"x": {"resources": bad}})
    doc = w.dump()
    assert doc["jobs"]["train"]["resources"] == {"cpu": 2, "gpu": 1}
    assert spec.parse(doc).job("train").resource_map() == {"cpu": 2, "gpu": 1}


def test_c2_capacity_is_never_exceeded():
    needs = [{"cpu": 1}, {"cpu": 2}, {"cpu": 1, "gpu": 1}, {"cpu": 3}, {"gpu": 1}, {"cpu": 2, "gpu": 1}]
    jobs = {f"j{i:02d}": {"duration_s": 1 + (i * 7) % 5, "priority": (i * 3) % 4,
                          "resources": needs[i % len(needs)]} for i in range(18)}
    jobs["j17"]["deps"] = ["j03", "j05"]
    eng = Engine(wf(jobs), capacity={"cpu": 4, "gpu": 1})
    s = eng.run()
    assert len(s["succeeded"]) == 18
    ivs = intervals(eng)
    assert peak(ivs, "cpu") <= 4 and peak(ivs, "gpu") <= 1
    assert s["peak"] == {"cpu": peak(ivs, "cpu"), "gpu": peak(ivs, "gpu")}


def test_c3_a_blocked_job_blocks_lower_priority_jobs():
    w = wf({"seed": {"duration_s": 1, "priority": 9},
            "warm": {"duration_s": 10, "resources": {"cpu": 2}},
            "big": {"deps": ["seed"], "duration_s": 3, "priority": 5, "resources": {"cpu": 4}},
            "small": {"deps": ["seed"], "duration_s": 1}})
    eng = Engine(w, capacity={"cpu": 4})
    eng.run()
    assert [(e.job, e.t) for e in eng.log.of_kind("started")] == [
        ("seed", 0), ("warm", 0), ("big", 10), ("small", 13)]


def test_c4_engine_validates_resources_and_capacity_arguments():
    with pytest.raises(SpecError):
        Engine(wf({"x": {"resources": {"gpu": 1}}}))
    with pytest.raises(SpecError):
        Engine(wf({"x": {"resources": {"cpu": 2}}}), capacity={"cpu": 1})
    w = wf({"x": {}})
    with pytest.raises(ValueError):
        Engine(w, capacity={"cpu": 2}, max_parallel=2)
    assert Engine(w, max_parallel=3).capacity == {"cpu": 3}
    assert Engine(w).capacity == {"cpu": 2}


def test_c5_snapshots_carry_capacity_and_old_ones_still_load():
    w = wf({"a": {"duration_s": 5, "resources": {"cpu": 1, "gpu": 1}},
            "b": {"duration_s": 5, "resources": {"cpu": 1, "gpu": 1}}})
    first = Engine(w, capacity={"cpu": 2, "gpu": 1})
    first.run(until=2)
    snap = json.loads(json.dumps(persistence.snapshot(first)))
    assert snap["capacity"] == {"cpu": 2, "gpu": 1}
    second = persistence.restore(w, snap)
    second.run()
    assert second.capacity == {"cpu": 2, "gpu": 1}
    # the interrupted job re-queues at the snapshot time, behind the one already waiting
    assert [(e.job, e.t) for e in second.log.of_kind("started")] == [("b", 2), ("a", 7)]
    old = {"version": 2, "t": 0, "max_parallel": 1,
           "jobs": {"x": {"state": "ready", "attempts": 0, "ready_since": 0, "retry_at": None},
                    "y": {"state": "ready", "attempts": 0, "ready_since": 0, "retry_at": None}}}
    eng = persistence.restore(wf({"x": {"duration_s": 2}, "y": {"duration_s": 2}}), old)
    assert eng.capacity == {"cpu": 1}
    eng.run()
    assert [(e.job, e.t) for e in eng.log.of_kind("started")] == [("x", 0), ("y", 2)]


def test_c6_summary_reports_peak_usage():
    w = wf({"a": {"resources": {"cpu": 1, "gpu": 1}}, "b": {"resources": {"cpu": 1}}})
    s = Engine(w, capacity={"cpu": 3, "gpu": 1}).run()
    assert s["peak"] == {"cpu": 2, "gpu": 1} and s["capacity"] == {"cpu": 3, "gpu": 1}
    assert "gpu 1/1" in report.render(s)


def test_r_default_capacity_behaviour_unchanged():
    eng = Engine(wf({n: {"duration_s": 4} for n in "abc"}))
    eng.run()
    assert [(e.job, e.t) for e in eng.log.of_kind("started")] == [("a", 0), ("b", 0), ("c", 4)]
