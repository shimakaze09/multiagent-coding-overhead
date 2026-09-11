"""Stage-2 difficulty candidates (PREREGISTRATION 19.3): mechanical controls, no Claude session.

For every candidate task:
* the unfixed state fails the held-out verifier (partially), the reference
  solution passes it and changes exactly the analysis-only expected files;
* every plausible naive variant still fails the held-out verifier;
* the visible tests pass on the unfixed code and after the reference fix;
* per codebase, every task's fixed tree is the same correct codebase;
* the statement describes the unfixed code and names no analysis-only file;
* design metadata and difficulty labels never reach a prompt;
* held-out tests map onto the design's constraints and partial scores compute;
* the leakage detectors flag this task's held-out and design material;
* the base commit is deterministic.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from types import SimpleNamespace

import pytest

import config
from analysis import isolation, leakage, quality
from arms import multi_nl, single
from harness import telemetry
from harness import workspace as ws_mod
from tasks import registry, stage2_design

CANDIDATES = config.STAGE2_CANDIDATES

PROBES = {
    "s2t01_ledgerly": ('from datetime import date\n'
                       'from ledgerly import Account, Chart, Entry, Ledger, Line, Money, importers, reports\n'
                       'rates = importers.parse_rates_csv(open("examples/rates_2024_03.csv").read())\n'
                       'ledger = Ledger(Chart([Account("1010", "Bank GBP", "asset"), Account("4000", "Sales", "income")]), rates)\n'
                       'for eid, on, amount in (("R-1", date(2024, 3, 5), "5000"), ("R-2", date(2024, 3, 20), "3000")):\n'
                       '    m = Money(amount, "GBP")\n'
                       '    ledger.post(Entry(eid, on, [Line("1010", m), Line("4000", -m)]))\n'
                       'print(reports.format_money(ledger.balance_in("1010", date(2024, 3, 21))))\n',
                       "9,342.76 EUR", "9,318.06 EUR"),
    "s2t02_ledgerly": ('from ledgerly import Money\n'
                       'try:\n    print(Money("1000", "JPY").rounded())\n'
                       'except Exception as e:\n    print(type(e).__name__)\n', "UnknownCurrency", "1000 JPY"),
    "s2t03_ledgerly": ('from ledgerly import Line, Money\n'
                       'try:\n    print(Line("1000", Money("-5", "EUR")).side)\n'
                       'except Exception as e:\n    print(type(e).__name__)\n', "ValueError", "credit"),
    "s2t04_ledgerly": ('from datetime import date\n'
                       'from ledgerly import Account, Chart, Ledger, Money, billing\n'
                       'chart = Chart([Account("1100", "Receivables", "asset"), Account("4000", "Licences", "income"),\n'
                       '               Account("4100", "Support", "income"), Account("4200", "Training", "income")])\n'
                       'ledger = Ledger(chart)\n'
                       'try:\n'
                       '    ledger.post(billing.split_invoice("INV-7", date(2024, 4, 1), Money("100.00", "EUR"), "1100",\n'
                       '                                      {"4000": 1, "4100": 1, "4200": 1}))\n'
                       '    print("posted")\n'
                       'except Exception as e:\n    print(f"{type(e).__name__}: {e}")\n',
                       "UnbalancedEntry: INV-7: EUR lines do not balance (0.01 EUR)", "posted"),
    "s2t05_flowq": ('from flowq import Engine, persistence, spec\n'
                    'wf = spec.load("examples/release.json")\n'
                    'first = Engine(wf)\n_ = first.run(until=20)\n'
                    'second = persistence.restore(wf, persistence.snapshot(first))\n_ = second.run()\n'
                    'print(second.log.started())\n',
                    "['Build', 'Lint', 'Unit Tests', 'Package', 'deploy']", "['Unit Tests', 'Package', 'deploy']"),
    "s2t06_flowq": ('from flowq import Engine, spec\n'
                    'try:\n'
                    '    w = spec.parse({"version": 2, "jobs": {"t": {"resources": {"gpu": 1}}}})\n'
                    '    print(Engine(w, capacity={"gpu": 1}).capacity)\n'
                    'except Exception as e:\n    print(type(e).__name__)\n', "SpecError", "{'gpu': 1}"),
    "s2t07_flowq": ('import flowq\nprint(hasattr(flowq, "RetryPolicy"))\n', "False", "True"),
    "s2t08_flowq": ('from flowq import Engine, spec\n'
                    'wf = spec.parse({"version": 2, "jobs": {"first": {"duration_s": 2, "priority": 9},\n'
                    '    "zeta": {"duration_s": 1}, "alpha": {"deps": ["first"], "duration_s": 1}}})\n'
                    'eng = Engine(wf, max_parallel=1)\n_ = eng.run()\n'
                    'print([(e.job, e.t) for e in eng.log.of_kind("started")])\n',
                    "[('first', 0.0), ('alpha', 2.0), ('zeta', 3.0)]",
                    "[('first', 0.0), ('zeta', 2.0), ('alpha', 3.0)]"),
    "s2t09_docpipe": ('import re\nfrom docpipe import Options, render\n'
                      'out = render("## Configuring [the CLI](cli.md)\\n\\n# Setup[^f]\\n\\n[^f]: x\\n", options=Options(toc=True))\n'
                      'print(re.findall(r\'href="#([^"]+)"\', out)[:2] == re.findall(r\'<h\\d id="([^"]+)"\', out))\n',
                      "False", "True"),
    "s2t10_docpipe": ('from docpipe import render\nprint("footnote-ref" in render("A[^1].\\n\\n[^1]: n\\n"))\n',
                      "False", "True"),
    "s2t11_docpipe": ('import docpipe\nprint(hasattr(docpipe, "HtmlRenderer"))\n', "False", "True"),
    "s2t12_docpipe": ('from docpipe import render\nprint(render("[click me](javascript:alert(document.cookie))\\n"))\n',
                      '<p><a href="javascript:alert(document.cookie)">click me</a></p>', "<p>click me</p>"),
}


def _ws(task, tmp_path, name="w"):
    return ws_mod.prepare_workspace(task.source_tree, tmp_path / name,
                                    exclude=(task.verifier_dest_name,),
                                    read_only_paths=task.read_only_paths)


def _verify(task, ws):
    return ws_mod.verify(ws, verifier_source=task.verifier_path,
                         verifier_dest_name=task.verifier_dest_name, command=task.verifier_command)


def _apply(ws, src):
    for p in sorted(src.rglob("*")):
        if p.is_file():
            dest = ws.path / p.relative_to(src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)


def _probe(ws, code):
    res = ws_mod.run_command(ws, [sys.executable, "-c", code])
    return (res.stdout.strip().splitlines() or [""])[-1] if res.exit_code == 0 else "ERROR: " + res.stderr[-300:]


def _scores(task, stdout):
    return quality.held_out_scores(stdout, task.verifier_path.read_text(encoding="utf-8"),
                                   stage2_design.load_design(task.task_id))


@pytest.fixture(params=CANDIDATES)
def task(request):
    return registry.get_task(request.param)


@pytest.fixture
def design(task):
    return stage2_design.load_design(task.task_id)


# --------------------------------------------------------------------------
# Definition, design metadata and isolation from prompts
# --------------------------------------------------------------------------


def test_candidate_pool_spans_four_families_and_three_codebases():
    designs = [stage2_design.load_design(t) for t in CANDIDATES]
    assert len(CANDIDATES) == 12
    assert {d["task_family"] for d in designs} == set(stage2_design.FAMILIES)
    assert {d["codebase"] for d in designs} == {"ledgerly", "flowq", "docpipe"}
    for fam in stage2_design.FAMILIES:
        assert sum(d["task_family"] == fam for d in designs) == 3


def test_task_is_registered_with_complete_design_metadata(task, design):
    assert task.category in registry.CATEGORIES and task.read_only_paths == ("tests/*",)
    cmd = list(task.verifier_command)
    assert cmd[-1] == task.verifier_dest_name and "--noconftest" in cmd and "-rA" in cmd
    assert "-rA" in task.visible_test_command
    assert design["analysis_only"] is True and design["task_id"] == task.task_id
    assert set(design["design_features"]) == set(stage2_design.DESIGN_FEATURES)
    assert design["design_features"]["task_family"] in stage2_design.FAMILIES == stage2_design.FAMILIES
    assert set(design["difficulty_dimensions"]) == set(stage2_design.DIFFICULTY_DIMENSIONS)
    assert set(design["difficulty_dimensions"].values()) <= set(stage2_design.DIMENSION_LEVELS)
    assert design["design_features"]["hidden_constraints_count"] == len(design["heldout"]["constraints"])
    assert design["reference_files"] == list(task.expected_edit_paths)
    assert not isinstance(design["design_features"].get("difficulty_score"), (int, float))
    for rel in task.analysis_only_paths:
        assert (task.source_tree / rel).is_file(), rel


def test_held_out_tests_map_onto_the_constraints(task, design):
    names = quality.defined_tests(task.verifier_path.read_text(encoding="utf-8"))
    constraints = design["heldout"]["constraints"]
    assert len(names) >= 6
    for n in names:
        assert n.startswith("test_r_") or any(n.startswith(f"test_{c.lower()}_") for c in constraints), n
    for c in constraints:
        assert any(n.startswith(f"test_{c.lower()}_") for n in names), c
    assert any(n.startswith("test_r_") for n in names)


def test_statement_reveals_no_file_role_and_no_design(task, design):
    s = task.statement
    assert ".py" not in s and "ANALYSIS ONLY" not in s and task.notes not in s
    for rel in task.analysis_only_paths:
        assert rel not in s and rel.rsplit("/", 1)[-1] not in s
    for word in stage2_design.FAMILIES + ("task_family", "stratum", "very_hard", "difficulty"):
        assert word not in s.lower()


def _prompts(task):
    return [single.build_solo_prompt(task), multi_nl.coordinator_kickoff_prompt(task),
            multi_nl.investigator_prompt(task, "instruction"),
            multi_nl.coordinator_after_investigation_prompt("report"),
            multi_nl.implementer_prompt(task, "instruction", "report"),
            multi_nl.coordinator_wrapup_prompt("report")]


def test_design_metadata_and_labels_never_reach_a_prompt(task, design):
    forbidden = [task.notes, *design["design_features"]["plausible_root_causes"],
                 *design["heldout"]["constraints"].values(), *stage2_design.FAMILIES,
                 *stage2_design.DESIGN_FEATURES,
                 *(d for d in stage2_design.DIFFICULTY_DIMENSIONS if "_" in d),
                 "difficulty_dimensions", "very_hard", "easy_control", "stratum", "calibration"]
    for p in _prompts(task):
        for rel in task.analysis_only_paths:
            assert rel not in p
        for text in forbidden:
            assert text not in p, text


# --------------------------------------------------------------------------
# Workspace, visible tests, statement probes
# --------------------------------------------------------------------------


def test_verifier_and_solution_material_are_withheld(task, tmp_path):
    ws = _ws(task, tmp_path)
    assert not (ws.path / task.verifier_dest_name).exists()
    names = set(ws.files())
    assert not {"design.json", "reference", "naive"} & {n.split("/")[0] for n in names}
    assert ws.read_only_files and all(f.startswith("tests/") for f in ws.read_only_files)


def test_base_commit_is_deterministic(task, tmp_path):
    a, b = _ws(task, tmp_path, "a"), _ws(task, tmp_path, "b")
    assert (a.base_commit, a.tree_hash()) == (b.base_commit, b.tree_hash())


def test_visible_tests_pass_on_the_unfixed_code(task, tmp_path):
    res = ws_mod.run_command(_ws(task, tmp_path), task.visible_test_command)
    assert res.exit_code == 0, res.stdout[-2000:]


def test_the_statement_describes_the_unfixed_code(task, tmp_path):
    code, unfixed, _fixed = PROBES[task.task_id]
    assert _probe(_ws(task, tmp_path), code) == unfixed


# --------------------------------------------------------------------------
# Held-out verifier: unfixed, reference, naive variants
# --------------------------------------------------------------------------


def test_unfixed_code_fails_the_held_out_verifier_partially(task, tmp_path):
    res = _verify(task, _ws(task, tmp_path))
    assert res.solved is False
    s = _scores(task, res.stdout)
    assert s["basis"] == "per_test" and s["q2_heldout_fraction"] < 1
    assert s["q5_constraints_satisfied"] < s["q5_constraints_total"]


def test_reference_solution_passes(task, tmp_path):
    ws = _ws(task, tmp_path)
    _apply(ws, stage2_design.reference_dir(task.task_id))
    assert sorted(c["path"] for c in ws.changed_paths()) == sorted(task.expected_edit_paths)
    res = _verify(task, ws)
    assert res.solved is True, res.stdout[-3000:]
    s = _scores(task, res.stdout)
    assert s["q2_heldout_fraction"] == 1 and s["q5_constraint_fraction"] == 1
    (ws.path / task.verifier_dest_name).unlink()
    assert ws_mod.run_command(ws, task.visible_test_command).exit_code == 0
    assert _probe(ws, PROBES[task.task_id][0]) == PROBES[task.task_id][2]


def test_plausible_naive_variants_fail(task, design, tmp_path):
    variants = sorted(p for p in stage2_design.naive_dir(task.task_id).iterdir() if p.is_dir())
    assert [v.name for v in variants] == sorted(design["naive_variants"]) and variants
    for v in variants:
        ws = _ws(task, tmp_path, v.name)
        _apply(ws, v)
        res = _verify(task, ws)
        assert res.solved is False, v.name
        assert _scores(task, res.stdout)["q2_heldout_fraction"] > 0, v.name


def _digest_without_tests(root):
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not rel.startswith(("tests/", ".git/")) and "__pycache__" not in rel:
            h.update(rel.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


@pytest.mark.parametrize("codebase", ["ledgerly", "flowq", "docpipe"])
def test_every_fixed_tree_of_a_codebase_is_the_same_codebase(codebase, tmp_path):
    digests = set()
    for t in (c for c in CANDIDATES if c.endswith(codebase)):
        task = registry.get_task(t)
        dest = tmp_path / t
        shutil.copytree(task.source_tree, dest)
        ref = stage2_design.reference_dir(t)
        for p in ref.rglob("*"):
            if p.is_file():
                shutil.copyfile(p, dest / p.relative_to(ref))
        digests.add(_digest_without_tests(dest))
    assert len(digests) == 1


# --------------------------------------------------------------------------
# Leakage detection for this task's material
# --------------------------------------------------------------------------


def _repo_texts(task):
    return {p.relative_to(task.source_tree).as_posix(): p.read_text(encoding="utf-8")
            for p in task.source_tree.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def test_content_detector_flags_this_tasks_held_out_content(task):
    repo = _repo_texts(task)
    held_out = task.verifier_path.read_text(encoding="utf-8")
    protected = leakage.protected_lines(held_out, repo, task.statement)
    assert len(protected) >= 10
    body = "\n".join(l for l in held_out.splitlines() if "holdout" not in l.lower())
    assert leakage.check_texts([("s", "t", body)], protected)
    for rel, text in repo.items():
        assert not leakage.check_texts([("s", rel, text)], protected), rel
    assert not leakage.check_texts([("s", "stmt", task.statement)], protected)


def _raw(ws_path, calls):
    lines = [json.dumps({"type": "system", "subtype": "init", "session_id": "s", "apiKeySource": "none"})]
    for i, (name, inp, result) in enumerate(calls):
        lines.append(json.dumps({"type": "assistant", "message": {"id": f"m{i}", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}}))
        lines.append(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": result}]}}))
    return {"metadata": {"workspace": {"path": str(ws_path)}},
            "sessions": [SimpleNamespace(session_key="01_solo", parsed=telemetry.parse_stream(lines))]}


def test_isolation_check_flags_design_reference_and_naive_material(task, request):
    ws_path = request.config.rootpath / "runs" / "X_r1" / "workspace"
    for target in (stage2_design.design_path(task.task_id), stage2_design.reference_dir(task.task_id),
                   stage2_design.naive_dir(task.task_id), task.verifier_path):
        raw = _raw(ws_path, [("Read", {"file_path": str(target)}, "x")])
        assert isolation.check_run(raw)["breach_suspected"] is True, target
    raw = _raw(ws_path, [("Bash", {"command": f"cat ../../../tasks/holdout/{task.task_id}/design.json"}, "x")])
    assert isolation.check_run(raw)["breach_suspected"] is True
