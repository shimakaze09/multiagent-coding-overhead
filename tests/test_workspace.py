"""Milestone 5 tests: isolated per-run Git workspaces."""

from __future__ import annotations

import pytest

from harness import workspace as ws_mod
from tasks import registry


@pytest.fixture
def task():
    return registry.get_task("palindrome_punctuation")


def test_base_commit_is_deterministic_across_runs(task, tmp_path):
    a = ws_mod.prepare_workspace(task.source_tree, tmp_path / "a")
    b = ws_mod.prepare_workspace(task.source_tree, tmp_path / "b")
    assert a.base_commit == b.base_commit, (
        "the same pinned tree must produce the same base commit in every run"
    )
    assert len(a.base_commit) == 40
    assert a.tree_hash() == b.tree_hash()


def test_workspace_starts_clean(task, tmp_path):
    ws = ws_mod.prepare_workspace(task.source_tree, tmp_path / "w")
    assert not ws.is_dirty()
    assert ws.diff_vs_base() == ""


def test_held_out_verifier_is_absent_during_the_agent_phase(task, tmp_path):
    ws = ws_mod.prepare_workspace(
        task.source_tree, tmp_path / "w", exclude=(task.verifier_dest_name,)
    )
    assert not (ws.path / task.verifier_dest_name).exists()
    assert task.verifier_dest_name not in ws.files()


def test_visible_tests_are_present(task, tmp_path):
    ws = ws_mod.prepare_workspace(task.source_tree, tmp_path / "w")
    assert "tests/test_basic.py" in ws.files()
    assert "tinylib/text.py" in ws.files()


def test_runs_cannot_contaminate_one_another(task, tmp_path):
    a = ws_mod.prepare_workspace(task.source_tree, tmp_path / "a")
    b = ws_mod.prepare_workspace(task.source_tree, tmp_path / "b")

    (a.path / "tinylib" / "text.py").write_text("CONTAMINATED", encoding="utf-8")

    assert a.is_dirty()
    assert not b.is_dirty(), "a change in one run must not appear in another"
    assert "CONTAMINATED" not in (b.path / "tinylib" / "text.py").read_text(encoding="utf-8")
    src = task.source_tree / "tinylib" / "text.py"
    assert "CONTAMINATED" not in src.read_text(encoding="utf-8"), (
        "the pinned source tree must never be modified by a run"
    )


def test_diff_and_tree_hash_track_changes(task, tmp_path):
    ws = ws_mod.prepare_workspace(task.source_tree, tmp_path / "w")
    before = ws.working_tree_hash()
    p = ws.path / "tinylib" / "text.py"
    p.write_text(p.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    after = ws.working_tree_hash()

    assert before != after
    diff = ws.diff_vs_base()
    assert "# changed" in diff
    assert "tinylib/text.py" in diff


def test_blob_sha_identifies_a_file_version(task, tmp_path):
    ws = ws_mod.prepare_workspace(task.source_tree, tmp_path / "w")
    sha = ws.blob_sha("tinylib/text.py")
    assert sha and len(sha) == 40
    assert ws.blob_sha("does/not/exist.py") is None


def test_user_git_config_cannot_affect_the_base_commit(task, tmp_path, monkeypatch):
    """HOME is redirected into the workspace, so no user identity or hooks leak."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Someone Else")
    monkeypatch.setenv("EMAIL", "someone@example.com")
    a = ws_mod.prepare_workspace(task.source_tree, tmp_path / "a")
    monkeypatch.delenv("GIT_AUTHOR_NAME", raising=False)
    b = ws_mod.prepare_workspace(task.source_tree, tmp_path / "b")
    assert a.base_commit == b.base_commit


def test_preparing_over_an_existing_directory_is_refused(task, tmp_path):
    dest = tmp_path / "w"
    ws_mod.prepare_workspace(task.source_tree, dest)
    with pytest.raises(ws_mod.WorkspaceError):
        ws_mod.prepare_workspace(task.source_tree, dest)


def test_missing_source_tree_is_refused(tmp_path):
    with pytest.raises(ws_mod.WorkspaceError):
        ws_mod.prepare_workspace(tmp_path / "nope", tmp_path / "w")


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def test_verifier_fails_on_the_unfixed_repository(task, tmp_path):
    ws = ws_mod.prepare_workspace(
        task.source_tree, tmp_path / "w", exclude=(task.verifier_dest_name,)
    )
    res = ws_mod.verify(
        ws,
        verifier_source=task.verifier_path,
        verifier_dest_name=task.verifier_dest_name,
        command=task.verifier_command,
    )
    assert res.solved is False
    assert res.exit_code != 0
    assert "failed" in res.stdout.lower()


def test_verifier_passes_on_a_correctly_fixed_repository(task, tmp_path):
    ws = ws_mod.prepare_workspace(
        task.source_tree, tmp_path / "w", exclude=(task.verifier_dest_name,)
    )
    text = ws.path / "tinylib" / "text.py"
    text.write_text(
        text.read_text(encoding="utf-8").replace(
            "def slugify(text):",
            '_PUNCT = re.compile(r"[^a-z0-9]+")\n\n\n'
            "def normalize_for_comparison(text):\n"
            '    """Comparison-only normalizer."""\n'
            '    return _PUNCT.sub("", normalize(text))\n\n\n'
            "def slugify(text):",
            1,
        ),
        encoding="utf-8",
    )
    pal = ws.path / "tinylib" / "palindrome.py"
    pal.write_text(
        pal.read_text(encoding="utf-8")
        .replace("from .text import normalize", "from .text import normalize_for_comparison", 1)
        .replace('cleaned = normalize(text).replace(" ", "")',
                 "cleaned = normalize_for_comparison(text)", 1),
        encoding="utf-8",
    )

    res = ws_mod.verify(
        ws,
        verifier_source=task.verifier_path,
        verifier_dest_name=task.verifier_dest_name,
        command=task.verifier_command,
    )
    assert res.solved is True, res.stdout
    assert res.exit_code == 0


def test_verification_records_that_no_llm_judge_was_used(task, tmp_path):
    ws = ws_mod.prepare_workspace(
        task.source_tree, tmp_path / "w", exclude=(task.verifier_dest_name,)
    )
    res = ws_mod.verify(
        ws,
        verifier_source=task.verifier_path,
        verifier_dest_name=task.verifier_dest_name,
        command=task.verifier_command,
    )
    assert res.verifier_paths_injected == [task.verifier_dest_name]
    assert res.argv == list(task.verifier_command)


def test_task_repo_and_verifier_exist_for_every_registered_task():
    for t in registry.all_tasks().values():
        assert t.source_tree.is_dir()
        assert t.verifier_path.exists()
        assert t.category in registry.CATEGORIES
        assert t.statement.strip()
