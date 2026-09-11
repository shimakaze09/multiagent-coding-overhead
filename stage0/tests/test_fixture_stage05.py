"""Stage-0.5 fixtures (tasks 3-6): mechanical controls, no Claude session.

For every new task:
* the unfixed state fails the held-out verifier, and the reference fix passes;
* at least one different-but-correct fix also passes where one exists, because
  correct solutions need not be byte-identical to the reference;
* plausible naive fixes fail;
* the visible tests pass unfixed and do not expose the held-out answer;
* the statement names no file role, and analysis-only roles never reach a prompt;
* the leakage detector flags this task's held-out material;
* the base commit is deterministic and Arm A and Arm B start from identical trees;
* SOLVED comes only from the held-out verifier.
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

import pytest

import config
from analysis import isolation
from arms import finish_run, multi_nl, single, start_run
from harness import telemetry
from harness import workspace as ws_mod
from tasks import registry

NEW_TASKS = ("shipping_inch_dimensions", "settings_list_fields",
             "rename_max_connections", "sla_weekend_hours")
CLI = config.ClaudeCli(path="claude", version="2.1.260 (Claude Code)", discovered_via="test")


def _ws(task, tmp_path, name="w"):
    return ws_mod.prepare_workspace(task.source_tree, tmp_path / name,
                                    exclude=(task.verifier_dest_name,),
                                    read_only_paths=task.read_only_paths)


def _verify(task, ws):
    return ws_mod.verify(ws, verifier_source=task.verifier_path,
                         verifier_dest_name=task.verifier_dest_name,
                         command=task.verifier_command)


def _edit(ws, rel, old, new):
    p = ws.path / rel
    text = p.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {rel}"
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def _write(ws, rel, text):
    (ws.path / rel).write_text(text, encoding="utf-8")


def _probe(ws, code):
    res = ws_mod.run_command(ws, [sys.executable, "-c", code])
    return (res.stdout.strip().splitlines() or [""])[-1] if res.exit_code == 0 else "ERROR"


# --------------------------------------------------------------------------
# Task 3 - shipping_inch_dimensions
# --------------------------------------------------------------------------


def _reference_fix_shipkit(ws):
    _edit(ws, "shipkit/units.py", '"in": 0.3937,', '"in": 2.54,')


def _alt_shipkit(ws):
    _edit(ws, "shipkit/units.py", '"in": 0.3937,', '"in": 1 / 0.3937,')


def _naive_shipkit_local_conversion(ws):
    _edit(ws, "shipkit/parcel.py", "    def dimensions_cm(self):\n",
          "    def dimensions_cm(self):\n"
          "        if self.dim_unit == \"in\":\n"
          "            return tuple(v * 2.54 for v in (self.length, self.width, self.height))\n")


def _naive_shipkit_divide(ws):
    _edit(ws, "shipkit/units.py", "        return value * CM_PER[unit]",
          "        return value / CM_PER[unit]")


# --------------------------------------------------------------------------
# Task 4 - settings_list_fields
# --------------------------------------------------------------------------

_CONFKIT_FIELD = ("    required: bool = False\n", "    required: bool = False\n    item_type: Any = None\n")
_CONFKIT_SCHEMA = (
    "        for f in fields:\n"
    "            if f.type not in SUPPORTED_TYPES:\n",
    "        for f in fields:\n"
    "            if f.type is list:\n"
    "                if f.item_type not in SUPPORTED_TYPES:\n"
    "                    raise ValueError(f\"{f.name}: list settings need a scalar item_type\")\n"
    "            elif f.type not in SUPPORTED_TYPES:\n",
)


def _confkit_schema(ws):
    _edit(ws, "confkit/schema.py", *_CONFKIT_FIELD)
    _edit(ws, "confkit/schema.py", *_CONFKIT_SCHEMA)


def _confkit_coerce(ws, empty_check=True):
    guard = "    if not raw.strip():\n        return []\n" if empty_check else ""
    _edit(ws, "confkit/coerce.py", "def convert(field, raw):\n",
          "def to_list(raw, item_type):\n" + guard +
          "    converter = CONVERTERS[item_type]\n"
          "    return [converter(part) for part in raw.split(\",\")]\n\n\n"
          "def convert(field, raw):\n"
          "    if field.type is list:\n"
          "        return to_list(raw, field.item_type)\n")


def _reference_fix_confkit(ws):
    _confkit_schema(ws)
    _confkit_coerce(ws)


def _naive_confkit_split_in_loader(ws):
    _confkit_schema(ws)
    _edit(ws, "confkit/loader.py", "        try:\n            values[field.name] = coerce.convert(field, raw)",
          "        if field.type is list:\n"
          "            values[field.name] = raw.split(\",\")\n"
          "            continue\n"
          "        try:\n            values[field.name] = coerce.convert(field, raw)")


def _naive_confkit_no_empty_case(ws):
    _confkit_schema(ws)
    _confkit_coerce(ws, empty_check=False)


# --------------------------------------------------------------------------
# Task 5 - rename_max_connections
# --------------------------------------------------------------------------

_DB_CONFIG = '''"""Client configuration, loaded from a mapping (e.g. a parsed TOML file)."""

import warnings
from dataclasses import dataclass

DEFAULTS = {"host": "localhost", "port": 5432, "max_connections": 10, "timeout_s": 30.0}
DEPRECATED_KEYS = {"max_conn": "max_connections"}


@dataclass(frozen=True)
class ClientConfig:
    host: str
    port: int
    max_connections: int
    timeout_s: float


def load_config(data):
    data = dict(data)
    for old, new in DEPRECATED_KEYS.items():
        if old in data:
            warnings.warn(f"config key {old!r} is deprecated; use {new!r}",
                          DeprecationWarning, stacklevel=2)
            value = data.pop(old)
            data.setdefault(new, value)
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    merged = {**DEFAULTS, **data}
    if int(merged["max_connections"]) < 1:
        raise ValueError("max_connections must be at least 1")
    return ClientConfig(
        host=str(merged["host"]),
        port=int(merged["port"]),
        max_connections=int(merged["max_connections"]),
        timeout_s=float(merged["timeout_s"]),
    )
'''

_DB_POOL = '''"""A minimal connection pool (connections are opaque integer handles here)."""

import warnings

_UNSET = object()


class PoolExhausted(RuntimeError):
    pass


class Pool:
    def __init__(self, max_connections=_UNSET, *, max_conn=_UNSET):
        if max_conn is not _UNSET:
            warnings.warn("Pool(max_conn=...) is deprecated; use max_connections",
                          DeprecationWarning, stacklevel=2)
            if max_connections is _UNSET:
                max_connections = max_conn
        if max_connections is _UNSET:
            max_connections = 10
        if max_connections < 1:
            raise ValueError("max_connections must be at least 1")
        self.max_connections = max_connections
        self._in_use = set()
        self._next = 0

    @property
    def in_use(self):
        return len(self._in_use)

    def acquire(self):
        if len(self._in_use) >= self.max_connections:
            raise PoolExhausted(
                f"all {self.max_connections} connections in use "
                f"(max_connections={self.max_connections})"
            )
        self._next += 1
        self._in_use.add(self._next)
        return self._next

    def release(self, conn):
        self._in_use.discard(conn)
'''

_DB_CLI = '''"""Command-line entry point: `dbclient --host db --port 6000 ...`."""

import argparse
import warnings

from .config import load_config


def build_parser():
    parser = argparse.ArgumentParser(prog="dbclient")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--max-connections", type=int, dest="max_connections",
                        help="maximum number of pooled connections")
    parser.add_argument("--max-conn", type=int, dest="max_conn", help=argparse.SUPPRESS)
    parser.add_argument("--timeout-s", type=float, dest="timeout_s")
    return parser


def config_from_args(argv):
    args = build_parser().parse_args(argv)
    data = {k: v for k, v in vars(args).items() if v is not None}
    if "max_conn" in data:
        warnings.warn("--max-conn is deprecated; use --max-connections",
                      DeprecationWarning, stacklevel=2)
        data.setdefault("max_connections", data.pop("max_conn"))
    return load_config(data)
'''

_DB_STATS = '''"""Human-readable pool status for logs and the admin page."""


def describe(pool):
    return (f"pool: {pool.in_use}/{pool.max_connections} in use "
            f"(max_connections={pool.max_connections})")
'''


def _reference_fix_dbclient(ws):
    _write(ws, "dbclient/config.py", _DB_CONFIG)
    _write(ws, "dbclient/pool.py", _DB_POOL)
    _write(ws, "dbclient/cli.py", _DB_CLI)
    _write(ws, "dbclient/stats.py", _DB_STATS)
    _edit(ws, "dbclient/client.py", "Pool(max_conn=config.max_conn)",
          "Pool(max_connections=config.max_connections)")


def _naive_dbclient_search_replace(ws):
    for rel in ("config.py", "pool.py", "cli.py", "stats.py", "client.py"):
        p = ws.path / "dbclient" / rel
        text = p.read_text(encoding="utf-8")
        p.write_text(text.replace("max-conn", "max-connections").replace("max_conn", "max_connections"),
                     encoding="utf-8")


def _naive_dbclient_shim_in_config_only(ws):
    _naive_dbclient_search_replace(ws)
    _write(ws, "dbclient/config.py", _DB_CONFIG)


# --------------------------------------------------------------------------
# Task 6 - sla_weekend_hours
# --------------------------------------------------------------------------

_WORKCAL_BUG = "    return day.weekday() in policy.BUSINESS_DAYS"


def _reference_fix_helpdesk(ws):
    _edit(ws, "helpdesk/workcal.py", _WORKCAL_BUG, "    return day.isoweekday() in policy.BUSINESS_DAYS")


def _alt_helpdesk(ws):
    _edit(ws, "helpdesk/workcal.py", _WORKCAL_BUG, "    return day.weekday() < 5")


def _naive_helpdesk_change_policy(ws):
    _edit(ws, "helpdesk/policy.py", "frozenset({1, 2, 3, 4, 5})", "frozenset({0, 1, 2, 3, 4})")


def _naive_helpdesk_exclude_saturday_only(ws):
    _edit(ws, "helpdesk/workcal.py", _WORKCAL_BUG, _WORKCAL_BUG + " and day.weekday() < 5")


# --------------------------------------------------------------------------
# Per-task specification
# --------------------------------------------------------------------------

SPECS = {
    "shipping_inch_dimensions": {
        "reference": _reference_fix_shipkit,
        "reference_changes": {"shipkit/units.py"},
        "alternatives": [_alt_shipkit],
        "naive": [_naive_shipkit_local_conversion, _naive_shipkit_divide],
        "probe": ("from shipkit import Parcel, quote\n"
                  "print(quote(Parcel(1, 'lb', 10, 8, 4, 'in')), quote(Parcel(1, 'lb', 25.4, 20.32, 10.16, 'cm')))\n"),
        "probe_unfixed": "5.00 9.00",
        "probe_fixed": "9.00 9.00",
        "leaks": ['dim_unit="in"', '"in")', "48.75", "25.4 x 20.3"],
    },
    "settings_list_fields": {
        "reference": _reference_fix_confkit,
        "reference_changes": {"confkit/schema.py", "confkit/coerce.py"},
        "alternatives": [],
        "naive": [_naive_confkit_split_in_loader, _naive_confkit_no_empty_case],
        "probe": ("from confkit import Field, Schema, DictSource, load\n"
                  "s = Schema(Field('cache.hosts', list, item_type=str), Field('cache.ports', list, item_type=int))\n"
                  "print(load(s, [DictSource({'cache.hosts': 'a.local, b.local', 'cache.ports': '7000,7001'})]))\n"),
        "probe_unfixed": "ERROR",
        "probe_fixed": "{'cache.hosts': ['a.local', 'b.local'], 'cache.ports': [7000, 7001]}",
        "leaks": ["item_type", "list,", "APP_CACHE_PORTS"],
    },
    "rename_max_connections": {
        "reference": _reference_fix_dbclient,
        "reference_changes": {"dbclient/config.py", "dbclient/pool.py", "dbclient/cli.py",
                              "dbclient/stats.py", "dbclient/client.py"},
        "alternatives": [],
        "naive": [_naive_dbclient_search_replace, _naive_dbclient_shim_in_config_only],
        "probe": ("from dbclient import load_config\n"
                  "try:\n    print(load_config({'max_connections': 5}).max_connections)\n"
                  "except ValueError:\n    print('rejected')\n"),
        "probe_unfixed": "rejected",
        "probe_fixed": "5",
        "leaks": ["max_connections", "--max-connections", "DeprecationWarning"],
    },
    "sla_weekend_hours": {
        "reference": _reference_fix_helpdesk,
        "reference_changes": {"helpdesk/workcal.py"},
        "alternatives": [_alt_helpdesk],
        "naive": [_naive_helpdesk_change_policy, _naive_helpdesk_exclude_saturday_only],
        "probe": ("from datetime import datetime\nfrom helpdesk import Ticket, breach_report\n"
                  "t = Ticket('T-1', 'urgent', opened_at=datetime(2024, 3, 8, 16, 0), "
                  "first_response_at=datetime(2024, 3, 11, 11, 0))\n"
                  "print(breach_report([t], now=datetime(2024, 3, 11, 12, 0)))\n"),
        "probe_unfixed": "['T-1']",
        "probe_fixed": "[]",
        "leaks": ["2024, 3, 11", "2024, 3, 9", "is_business_day", "2024, 3, 8"],
    },
}


@pytest.fixture(params=NEW_TASKS)
def task(request):
    return registry.get_task(request.param)


@pytest.fixture
def spec(task):
    return SPECS[task.task_id]


# --------------------------------------------------------------------------
# Definition and exposure
# --------------------------------------------------------------------------


def test_the_four_tasks_have_distinct_shapes():
    shapes = {registry.get_task(t).shape for t in NEW_TASKS}
    assert shapes == {"cross_file_bug", "cross_file_feature", "repo_wide_refactor",
                      "misleading_symptom_bug"}


def test_task_is_registered_with_analysis_only_roles(task, spec):
    assert task.category in registry.CATEGORIES
    assert task.read_only_paths == ("tests/*",)
    assert set(task.expected_edit_paths) == spec["reference_changes"]
    for rel in task.analysis_only_paths:
        assert (task.source_tree / rel).is_file(), rel
    assert not set(task.supporting_paths) & set(task.expected_edit_paths)
    assert task.as_dict()["expected_edit_paths"] == list(task.expected_edit_paths)


def test_held_out_verifier_runs_alone(task):
    cmd = list(task.verifier_command)
    assert cmd[-1] == task.verifier_dest_name and "--noconftest" in cmd and "tests" not in cmd


def test_statement_reveals_no_file_role(task):
    assert ".py" not in task.statement
    for rel in task.analysis_only_paths:
        assert rel not in task.statement
        assert rel.rsplit("/", 1)[-1] not in task.statement


def test_analysis_only_fields_never_reach_a_prompt(task):
    prompts = [
        single.build_solo_prompt(task),
        multi_nl.coordinator_kickoff_prompt(task),
        multi_nl.investigator_prompt(task, "instruction"),
        multi_nl.implementer_prompt(task, "instruction", "report"),
    ]
    for p in prompts:
        for rel in task.analysis_only_paths:
            assert rel not in p
        for word in ("symptom_paths", "supporting_paths", "expected_edit", "ANALYSIS ONLY"):
            assert word not in p
        assert task.notes not in p


def test_files_are_small_and_realistic(task):
    for rel in task.analysis_only_paths:
        n = len((task.source_tree / rel).read_text(encoding="utf-8").splitlines())
        assert 5 <= n <= 80, f"{rel}: {n} lines"


def test_visible_tests_do_not_reveal_the_held_out_answer(task, spec):
    visible = (task.source_tree / "tests" / "test_basic.py").read_text(encoding="utf-8")
    held_out = task.verifier_path.read_text(encoding="utf-8")
    for leak in spec["leaks"]:
        assert leak in held_out, leak
        assert leak not in visible, leak


# --------------------------------------------------------------------------
# Workspace and verifier behaviour
# --------------------------------------------------------------------------


def test_verifier_is_withheld_and_visible_tests_are_read_only(task, tmp_path):
    ws = _ws(task, tmp_path)
    assert not (ws.path / task.verifier_dest_name).exists()
    assert ws.read_only_files == ["tests/test_basic.py"]
    with pytest.raises(PermissionError):
        with open(ws.path / "tests" / "test_basic.py", "a", encoding="utf-8") as fh:
            fh.write("# tampered\n")


def test_base_commit_is_deterministic(task, tmp_path):
    a, b = _ws(task, tmp_path, "a"), _ws(task, tmp_path, "b")
    assert a.base_commit == b.base_commit
    assert a.tree_hash() == b.tree_hash()


def test_visible_tests_pass_on_the_unfixed_code(task, tmp_path):
    ws = _ws(task, tmp_path)
    res = ws_mod.run_command(ws, task.visible_test_command)
    assert res.exit_code == 0, res.stdout + res.stderr


def test_the_statement_describes_the_unfixed_code(task, spec, tmp_path):
    assert _probe(_ws(task, tmp_path), spec["probe"]) == spec["probe_unfixed"]


def test_unfixed_code_fails_the_held_out_verifier(task, tmp_path):
    res = _verify(task, _ws(task, tmp_path))
    assert res.solved is False
    assert "failed" in res.stdout


def test_reference_fix_passes(task, spec, tmp_path):
    ws = _ws(task, tmp_path)
    spec["reference"](ws)
    assert {c["path"] for c in ws.changed_paths()} == spec["reference_changes"]
    res = _verify(task, ws)
    assert res.solved is True, res.stdout[-3000:]
    assert ws_mod.run_command(ws, task.visible_test_command).exit_code == 0
    assert _probe(ws, spec["probe"]) == spec["probe_fixed"]


def test_a_different_correct_fix_also_passes(task, spec, tmp_path):
    if not spec["alternatives"]:
        pytest.skip("no distinct alternative fix defined for this task")
    for i, alt in enumerate(spec["alternatives"]):
        ws = _ws(task, tmp_path, f"alt{i}")
        alt(ws)
        assert _verify(task, ws).solved is True


def test_plausible_naive_fixes_fail(task, spec, tmp_path):
    for i, naive in enumerate(spec["naive"]):
        ws = _ws(task, tmp_path, f"naive{i}")
        naive(ws)
        assert _verify(task, ws).solved is False, naive.__name__


# --------------------------------------------------------------------------
# Leakage detection for this task's material
# --------------------------------------------------------------------------


def _raw(ws_path, calls):
    lines = [json.dumps({"type": "system", "subtype": "init", "session_id": "s", "apiKeySource": "none"})]
    for i, (name, inp, result) in enumerate(calls):
        lines.append(json.dumps({"type": "assistant", "message": {"id": f"m{i}", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": name, "input": inp}]}}))
        lines.append(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": result}]}}))
    return {"metadata": {"workspace": {"path": str(ws_path)}},
            "sessions": [SimpleNamespace(session_key="01_solo", parsed=telemetry.parse_stream(lines))]}


def _repo_texts(task):
    return {p.relative_to(task.source_tree).as_posix(): p.read_text(encoding="utf-8")
            for p in task.source_tree.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def test_path_and_marker_detector_flags_this_tasks_held_out_material(task, request):
    """Frozen isolation check v1 (paths and markers)."""
    root = request.config.rootpath
    ws_path = root / "runs" / "X_r1" / "workspace"
    cases = [
        [("Read", {"file_path": str(task.verifier_path)}, "x")],
        [("Bash", {"command": f"cat ../../../tasks/holdout/{task.verifier_source}"}, "x")],
        [("Read", {"file_path": str(root / "tests" / "test_fixture_stage05.py")}, "x")],
        [("Read", {"file_path": str(ws_path.parent / "metadata.json")}, "x")],
        [("Bash", {"command": "ls"}, f"{task.verifier_dest_name}\n")],
    ]
    for calls in cases:
        assert isolation.check_run(_raw(ws_path, calls))["breach_suspected"] is True, calls
    clean = [("Read", {"file_path": str(ws_path / task.expected_edit_paths[0])}, "x")]
    assert isolation.check_run(_raw(ws_path, clean))["breach_suspected"] is False


def test_content_detector_flags_this_tasks_held_out_content(task):
    """Stage-0.5 content check: held-out text that reaches an agent with no path
    or marker attached (the case the frozen v1 check cannot see)."""
    from analysis import leakage

    repo = _repo_texts(task)
    held_out = task.verifier_path.read_text(encoding="utf-8")
    protected = leakage.protected_lines(held_out, repo, task.statement)
    assert len(protected) >= 10
    body = "\n".join(l for l in held_out.splitlines() if "holdout" not in l.lower())
    assert leakage.check_texts([("s", "t", body)], protected)
    # the repository, the visible tests and the statement are never protected
    for rel, text in repo.items():
        assert not leakage.check_texts([("s", rel, text)], protected), rel
    assert not leakage.check_texts([("s", "stmt", task.statement)], protected)


# --------------------------------------------------------------------------
# Arm parity and SOLVED, through the real start_run / finish_run
# --------------------------------------------------------------------------


def test_arm_a_and_arm_b_start_from_identical_trees(task, tmp_path, clean_env):
    sessions = [start_run(task=task, arm=arm, repeat_id=1, cfg=config.RunConfig(), cli=CLI,
                          capability_report={}, runs_dir=tmp_path / "runs") for arm in ("A", "B")]
    try:
        a, b = sessions
        assert a.workspace.base_commit == b.workspace.base_commit
        assert a.workspace.tree_hash() == b.workspace.tree_hash()
        assert a.workspace.files() == b.workspace.files()
        assert a.metadata["config_hash"] == b.metadata["config_hash"]
        for s in sessions:
            assert not (s.workspace.path / task.verifier_dest_name).exists()
    finally:
        for s in sessions:
            s.ctx.log.close()


def test_solved_depends_only_on_the_held_out_verifier(task, spec, tmp_path, clean_env):
    s1 = start_run(task=task, arm="A", repeat_id=1, cfg=config.RunConfig(), cli=CLI,
                   capability_report={}, runs_dir=tmp_path / "r1")
    out1 = finish_run(s1)
    assert out1["visible_tests"]["passed"] is True
    assert out1["solved"] is False

    s2 = start_run(task=task, arm="B", repeat_id=1, cfg=config.RunConfig(), cli=CLI,
                   capability_report={}, runs_dir=tmp_path / "r2")
    spec["reference"](s2.workspace)
    target = s2.workspace.path / "tests" / "test_basic.py"
    os.chmod(target, 0o644)
    target.write_text("def test_forced_failure():\n    assert False\n", encoding="utf-8")
    out2 = finish_run(s2)
    assert out2["visible_tests"]["passed"] is False
    assert out2["solved"] is True
    assert [c["path"] for c in out2["workspace_integrity"]["protected_paths_changed"]] == [
        "tests/test_basic.py"]
