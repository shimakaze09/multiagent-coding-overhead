"""Controlled fixture 2: `cart_invoice_rounding` (PREREGISTRATION amendment 5).

Prepared, NOT run against Claude. These tests prove the fixture does what it is
designed to do, without any Claude session:

  * the symptom and the only correct production change are in shopcart/cart.py;
  * a naive fix that does not discover the per-unit rounding contract fails;
  * editing the shared modules fails, so B-E are genuinely constraints;
  * visible tests are read-only, identically for both arms;
  * the held-out verifier is withheld during the agent phase, runs alone, and
    cannot be subverted by an agent-written conftest.py.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

import config
from arms import start_run
from harness import workspace as ws_mod
from tasks import registry

TASK_ID = "cart_invoice_rounding"


@pytest.fixture
def task():
    return registry.get_task(TASK_ID)


def _ws(task, tmp_path, name="w"):
    return ws_mod.prepare_workspace(
        task.source_tree,
        tmp_path / name,
        exclude=(task.verifier_dest_name,),
        read_only_paths=task.read_only_paths,
    )


def _verify(task, ws):
    return ws_mod.verify(
        ws,
        verifier_source=task.verifier_path,
        verifier_dest_name=task.verifier_dest_name,
        command=task.verifier_command,
    )


def _edit(ws, rel, old, new):
    p = ws.path / rel
    text = p.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {rel}"
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


BUGGY_LINE_TOTAL = (
    "        gross = line.unit_price.amount_minor * line.qty\n"
    "        discount = gross * line.discount_percent // 100\n"
    "        return Money(gross - discount, self.currency)"
)
BUGGY_TOTAL_MAJOR = "        return Decimal(self.total().amount_minor) / 100"


def _reference_fix(ws):
    _edit(ws, "shopcart/cart.py", "from .money import Money",
          "from .discounts import net_unit_price\nfrom .money import Money")
    _edit(ws, "shopcart/cart.py", BUGGY_LINE_TOTAL,
          "        return net_unit_price(line.unit_price, line.discount_percent).times(line.qty)")
    _edit(ws, "shopcart/cart.py", BUGGY_TOTAL_MAJOR, "        return self.total().major()")


# --------------------------------------------------------------------------
# Definition
# --------------------------------------------------------------------------


def test_task_is_registered_with_protection_and_scope(task):
    assert task.category == "cross_file_bug"
    assert task.repo == "shopcart"
    assert task.read_only_paths == ("tests/*",)
    assert task.expected_modified_paths == ("shopcart/cart.py",)


def test_held_out_verifier_runs_alone_and_ignores_conftest(task):
    cmd = list(task.verifier_command)
    assert cmd[-1] == task.verifier_dest_name
    assert "--noconftest" in cmd
    assert "tests" not in cmd, "SOLVED must not count the visible tests"
    assert list(task.visible_test_command)[-1] == "tests"


def test_statement_names_no_supporting_file(task):
    """Any priming of the Implementer toward the supporting files can then only
    come from the handoff, not from the task statement both arms receive."""
    for name in ("money.py", "discounts.py", "currency.py", "invoice.py", "cart.py", "shopcart/"):
        assert name not in task.statement


def test_supporting_files_are_small_and_realistic(task):
    for rel in ("shopcart/money.py", "shopcart/discounts.py", "shopcart/currency.py",
                "shopcart/invoice.py", "shopcart/cart.py"):
        n = len((task.source_tree / rel).read_text(encoding="utf-8").splitlines())
        assert 20 <= n <= 80, f"{rel}: {n} lines"


# --------------------------------------------------------------------------
# Workspace behaviour
# --------------------------------------------------------------------------


def test_verifier_is_withheld_during_the_agent_phase(task, tmp_path):
    ws = _ws(task, tmp_path)
    assert not (ws.path / task.verifier_dest_name).exists()
    assert task.verifier_dest_name not in ws.files()


def test_visible_tests_are_read_only_in_the_workspace(task, tmp_path):
    ws = _ws(task, tmp_path)
    assert ws.read_only_files == ["tests/test_basic.py"]
    target = ws.path / "tests" / "test_basic.py"
    with pytest.raises(PermissionError):
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\n# tampered\n")
    # production files stay writable
    with open(ws.path / "shopcart" / "cart.py", "a", encoding="utf-8") as fh:
        fh.write("")


@pytest.mark.skipif(os.name != "nt", reason="rename over a read-only file is allowed on POSIX")
def test_replace_by_rename_is_also_blocked_on_windows(task, tmp_path):
    ws = _ws(task, tmp_path)
    tmp = ws.path / "tests" / "replacement.py"
    tmp.write_text("def test_x(): pass\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        os.replace(tmp, ws.path / "tests" / "test_basic.py")


def test_base_commit_is_independent_of_protection(task, tmp_path):
    protected = _ws(task, tmp_path, "p")
    plain = ws_mod.prepare_workspace(task.source_tree, tmp_path / "q",
                                     exclude=(task.verifier_dest_name,))
    assert protected.base_commit == plain.base_commit


def test_visible_tests_pass_on_the_unfixed_code(task, tmp_path):
    ws = _ws(task, tmp_path)
    res = ws_mod.run_command(ws, task.visible_test_command)
    assert res.exit_code == 0, res.stdout + res.stderr


def test_the_statement_examples_are_true_on_the_unfixed_code(task, tmp_path):
    ws = _ws(task, tmp_path)
    code = (
        "from shopcart import Cart, Money, Invoice\n"
        "c = Cart('USD').add('SKU-1', Money.of('1.05', 'USD'), qty=3, discount_percent=15)\n"
        "print(repr(c.total())); print(repr(Invoice.from_cart(c).total()))\n"
        "j = Cart('JPY').add('SKU-2', Money.of(1500, 'JPY'), qty=2)\n"
        "print(repr(j.total_major()))\n"
    )
    out = ws_mod.run_command(ws, [sys.executable, "-c", code]).stdout.splitlines()
    assert out == [
        "Money(amount_minor=268, currency='USD')",
        "Money(amount_minor=267, currency='USD')",
        "Decimal('30')",
    ]


def test_unfixed_code_fails_the_held_out_verifier(task, tmp_path):
    ws = _ws(task, tmp_path)
    res = _verify(task, ws)
    assert res.solved is False
    assert "failed" in res.stdout


def test_reference_fix_in_cart_py_only_passes(task, tmp_path):
    ws = _ws(task, tmp_path)
    _reference_fix(ws)
    assert ws.changed_paths() == [{"path": "shopcart/cart.py", "change": "modified"}]
    res = _verify(task, ws)
    assert res.solved is True, res.stdout[-3000:]
    assert ws_mod.run_command(ws, task.visible_test_command).exit_code == 0


def test_naive_per_line_rounding_fails(task, tmp_path):
    """Rounding the whole line half up instead of per unit gives 268 for the
    task's own example, so the per-unit contract has to be discovered."""
    ws = _ws(task, tmp_path)
    _edit(ws, "shopcart/cart.py", "        discount = gross * line.discount_percent // 100",
          "        discount = (gross * line.discount_percent + 50) // 100")
    _edit(ws, "shopcart/cart.py", BUGGY_TOTAL_MAJOR, "        return self.total().major()")
    assert _verify(task, ws).solved is False


def test_fixing_only_total_major_fails(task, tmp_path):
    ws = _ws(task, tmp_path)
    _edit(ws, "shopcart/cart.py", BUGGY_TOTAL_MAJOR, "        return self.total().major()")
    assert _verify(task, ws).solved is False


def test_editing_a_shared_module_fails_even_with_the_correct_cart_fix(task, tmp_path):
    ws = _ws(task, tmp_path)
    _reference_fix(ws)
    _edit(ws, "shopcart/discounts.py", "rounding=ROUND_HALF_UP", "rounding=ROUND_HALF_EVEN")
    assert _verify(task, ws).solved is False


def test_an_agent_written_conftest_cannot_subvert_the_verifier(task, tmp_path):
    ws = _ws(task, tmp_path)
    (ws.path / "conftest.py").write_text(
        "import pytest\n\n"
        "@pytest.hookimpl(hookwrapper=True)\n"
        "def pytest_runtest_makereport(item, call):\n"
        "    outcome = yield\n"
        "    outcome.get_result().outcome = 'passed'\n",
        encoding="utf-8",
    )
    # the conftest really would mask failures without --noconftest ...
    (ws.path / task.verifier_dest_name).write_bytes(task.verifier_path.read_bytes())
    masked = ws_mod.run_command(
        ws, [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", task.verifier_dest_name]
    )
    assert masked.exit_code == 0
    # ... but the verifier ignores it
    assert _verify(task, ws).solved is False


def test_changes_to_protected_paths_are_detected(task, tmp_path):
    """Covers what filesystem protection cannot prevent on every platform (POSIX
    rename, newly added files)."""
    ws = _ws(task, tmp_path)
    target = ws.path / "tests" / "test_basic.py"
    os.chmod(target, 0o644)
    target.write_text(target.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    (ws.path / "tests" / "test_extra.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    changed = ws.changed_paths()
    protected = [c for c in changed if ws.is_protected(c["path"])]
    assert {c["path"] for c in protected} == {"tests/test_basic.py", "tests/test_extra.py"}


def test_running_tests_creates_no_reported_change(task, tmp_path):
    ws = _ws(task, tmp_path)
    ws_mod.run_command(ws, [sys.executable, "-m", "pytest", "-q", "tests"])
    assert ws.changed_paths() == []


def test_cleanup_removes_read_only_files(task, tmp_path):
    ws = _ws(task, tmp_path)
    ws_mod.cleanup_workspace(ws)
    assert not ws.path.exists()


# --------------------------------------------------------------------------
# Arm parity, through the real start_run (no Claude session is opened)
# --------------------------------------------------------------------------


def test_both_arms_get_identical_protection_and_configuration(task, tmp_path, clean_env):
    cli = config.ClaudeCli(path="claude", version="2.1.260 (Claude Code)", discovered_via="test")
    cfg = config.RunConfig()
    sessions = [
        start_run(task=task, arm=arm, repeat_id=1, cfg=cfg, cli=cli,
                  capability_report={}, runs_dir=tmp_path / "runs")
        for arm in ("A", "B")
    ]
    try:
        a, b = sessions
        assert a.workspace.read_only_files == b.workspace.read_only_files == ["tests/test_basic.py"]
        assert a.workspace.base_commit == b.workspace.base_commit
        assert a.metadata["config_hash"] == b.metadata["config_hash"]
        assert (a.metadata["workspace"]["read_only_files"]
                == b.metadata["workspace"]["read_only_files"])
        assert a.metadata["task"]["read_only_paths"] == ["tests/*"]
        for s in sessions:
            assert not (s.workspace.path / task.verifier_dest_name).exists()
    finally:
        for s in sessions:
            s.ctx.log.close()


def test_visible_tests_do_not_reveal_the_held_out_answer(task):
    visible = (task.source_tree / "tests" / "test_basic.py").read_text(encoding="utf-8")
    held_out = task.verifier_path.read_text(encoding="utf-8")
    # expected values and entry points that only the held-out verifier uses
    for leak in ("Money(267", "2547", 'Decimal("3000")', "from_cart(", "net_unit_price(",
                 "unit_discount(", '"KWD"', '"BHD"', '"KRW"', '"1.05"'):
        assert leak in held_out, leak
        # identifier boundary: `test_single_unit_discount(` is not a call of unit_discount
        assert not re.search(r"(?<!\w)" + re.escape(leak), visible), leak
    # no visible case combines a quantity above 1 with a discount: that is the
    # only shape that distinguishes per-unit from per-line rounding
    for call in re.findall(r"\.add\(([^)]*)\)", visible):
        if "discount_percent" in call:
            assert re.search(r"qty\s*=\s*1\b", call), call


def test_making_the_invoice_agree_with_the_cart_fails(task, tmp_path):
    """Wrong fix: change the shared invoice to round per line like the cart."""
    ws = _ws(task, tmp_path)
    _edit(ws, "shopcart/cart.py", BUGGY_TOTAL_MAJOR, "        return self.total().major()")
    _edit(ws, "shopcart/invoice.py",
          "        net_unit = net_unit_price(unit_price, discount_percent)\n"
          "        self.lines.append((sku, qty, net_unit, net_unit.times(qty)))",
          "        gross = unit_price.amount_minor * qty\n"
          "        line_total = Money(gross - gross * discount_percent // 100, self.currency)\n"
          "        self.lines.append((sku, qty, unit_price, line_total))")
    assert _verify(task, ws).solved is False


def test_arm_a_and_arm_b_start_from_identical_trees(task, tmp_path, clean_env):
    cli = config.ClaudeCli(path="claude", version="2.1.260 (Claude Code)", discovered_via="test")
    sessions = [start_run(task=task, arm=arm, repeat_id=1, cfg=config.RunConfig(), cli=cli,
                          capability_report={}, runs_dir=tmp_path / "runs") for arm in ("A", "B")]
    try:
        a, b = sessions
        assert a.workspace.tree_hash() == b.workspace.tree_hash()
        assert a.workspace.files() == b.workspace.files()
    finally:
        for s in sessions:
            s.ctx.log.close()


def test_solved_depends_only_on_the_held_out_verifier(task, tmp_path, clean_env):
    """Through the real finish_run, with no agent session: visible tests and
    SOLVED are independent, and tampering with visible tests cannot fake SOLVED."""
    from arms import finish_run

    cli = config.ClaudeCli(path="claude", version="2.1.260 (Claude Code)", discovered_via="test")

    # 1. unfixed: visible tests PASS, SOLVED is still False
    s1 = start_run(task=task, arm="A", repeat_id=1, cfg=config.RunConfig(), cli=cli,
                   capability_report={}, runs_dir=tmp_path / "r1")
    out1 = finish_run(s1)
    assert out1["visible_tests"]["passed"] is True
    assert out1["solved"] is False
    assert out1["solved_basis"] == "held-out verifier exit code only"

    # 2. correct fix, but the visible tests replaced by a failing one:
    #    visible FAIL, SOLVED True, and the protected change is reported
    s2 = start_run(task=task, arm="B", repeat_id=1, cfg=config.RunConfig(), cli=cli,
                   capability_report={}, runs_dir=tmp_path / "r2")
    _reference_fix(s2.workspace)
    target = s2.workspace.path / "tests" / "test_basic.py"
    os.chmod(target, 0o644)
    target.write_text("def test_forced_failure():\n    assert False\n", encoding="utf-8")
    out2 = finish_run(s2)
    assert out2["visible_tests"]["passed"] is False
    assert out2["solved"] is True
    assert [c["path"] for c in out2["workspace_integrity"]["protected_paths_changed"]] == [
        "tests/test_basic.py"]


def test_the_first_fixture_is_unaffected(tmp_path):
    first = registry.get_task("palindrome_punctuation")
    assert first.read_only_paths == ()
    ws = ws_mod.prepare_workspace(first.source_tree, tmp_path / "p1",
                                  exclude=(first.verifier_dest_name,),
                                  read_only_paths=first.read_only_paths)
    assert ws.read_only_files == []
    assert ws.base_commit == "2cafd4c5e663dcb194eac740dbce710a972bb92e"
