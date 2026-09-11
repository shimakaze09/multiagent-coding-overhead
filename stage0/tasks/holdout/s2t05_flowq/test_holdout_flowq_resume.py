"""Held-out verifier: resuming never repeats finished work, whatever the names."""

import json

from flowq import Engine, persistence, spec

RELEASE = "examples/release.json"


def release():
    return spec.load(RELEASE)


def respelled():
    doc = json.loads(open(RELEASE, encoding="utf-8").read())
    rename = {"Build": "BUILD", "Unit Tests": "unit   tests", "Lint": " lint", "Package": "PACKAGE",
              "deploy": "Deploy"}
    jobs = {}
    for name, job in doc["jobs"].items():
        job = dict(job)
        job["deps"] = [d.upper() for d in job.get("deps", [])]
        jobs[rename[name]] = job
    return spec.parse({"version": 2, "jobs": jobs})


def crash_and_resume(until, workflow=None, second_workflow=None, handlers=None):
    first = Engine(workflow or release(), handlers)
    first.run(until=until)
    snap = json.loads(json.dumps(persistence.snapshot(first)))
    second = persistence.restore(second_workflow or workflow or release(), snap, handlers)
    return first, snap, second, second.run()


def finished(engine):
    return {r.spec.key for r in engine.runs.values() if r.state == "succeeded"}


def restarted(second):
    return {second.workflow.job(n).key for n in second.log.started()}


def test_c1_finished_jobs_never_run_again():
    names = sorted(j.name for j in release())
    for until in range(0, 50):
        first, _snap, second, s = crash_and_resume(until)
        assert not restarted(second) & finished(first), until
        assert sorted(s["succeeded"]) == names, until


def test_c2_attempts_and_retry_schedule_survive_a_resume():
    doc = {"version": 2, "jobs": {
        "Build": {"duration_s": 10},
        "Unit Tests": {"deps": ["Build"], "duration_s": 15,
                       "retry": {"max_attempts": 3, "backoff": {"initial_s": 4}}}}}
    handlers = {"Unit Tests": lambda name, attempt: attempt >= 2}
    w = spec.parse(doc)
    first, snap, second, s = crash_and_resume(27, w, handlers=handlers)
    assert snap["jobs"]["Unit Tests"]["state"] == "retry_wait"
    started = [(e.job, e.t, e.data.get("attempt")) for e in second.log.of_kind("started")]
    assert started == [("Unit Tests", 29.0, 2)]
    assert [(r["job"], r["attempts"]) for r in s["jobs"]] == [("Build", 1), ("Unit Tests", 2)]


def test_c3_snapshot_files_keep_display_names(tmp_path):
    first = Engine(release())
    first.run(until=20)
    persistence.save(first, tmp_path / "snap.json")
    raw = json.loads((tmp_path / "snap.json").read_text(encoding="utf-8"))
    assert set(raw["jobs"]) == {"Build", "Unit Tests", "Lint", "Package", "deploy"}
    second = persistence.load(release(), tmp_path / "snap.json")
    second.run()
    assert second.log.started() == ["Unit Tests", "Package", "deploy"]


def test_c4_resume_after_the_names_were_respelled():
    for until in (12, 20, 30, 40):
        first, _snap, second, s = crash_and_resume(until, second_workflow=respelled())
        assert not restarted(second) & finished(first), until
        assert len(s["succeeded"]) == 5


def test_c5_interrupted_and_new_jobs():
    doc = json.loads(open(RELEASE, encoding="utf-8").read())
    doc["jobs"]["Notify"] = {"deps": ["DEPLOY"], "duration_s": 1}
    extended = spec.parse(doc)
    first, snap, second, s = crash_and_resume(12, second_workflow=extended)
    assert {snap["jobs"][n]["state"] for n in ("Unit Tests", "Lint")} == {"running"}
    assert second.log.started() == ["Lint", "Unit Tests", "Package", "deploy", "Notify"]
    attempts = {r["job"]: r["attempts"] for r in s["jobs"]}
    assert attempts == {"Build": 1, "Unit Tests": 1, "Lint": 1, "Package": 1, "deploy": 1, "Notify": 1}


def test_r_lower_case_workflows_and_display_names():
    w = spec.parse({"version": 2, "jobs": {"build": {"duration_s": 5}, "test": {"deps": ["build"]}}})
    first, snap, second, s = crash_and_resume(3, w)
    assert second.log.started() == ["build", "test"]
    full = Engine(release()).run()
    assert [r["job"] for r in full["jobs"]] == ["Build", "Unit Tests", "Lint", "Package", "deploy"]
    assert full["t"] == 45
