"""Per-run isolated Git workspace.

Task repositories are stored as plain file trees under tasks/repos/. For each run
the tree is copied into a fresh workspace and committed with a fixed identity and
fixed timestamps, so the base commit SHA is deterministic and identical across
runs and arms. Runs never share a working tree.

Held-out verifier tests are injected only AFTER the agent phase ends, so agents
cannot read or edit the verifier.

A task may declare `read_only_paths` (e.g. its visible benchmark tests). The
harness makes those files read-only after the base commit and before any agent
session, identically for every arm, and checks them for changes afterwards.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Sequence

# Fixed identity/time so the same tree always yields the same commit SHA.
DETERMINISTIC_GIT_ENV = {
    "GIT_AUTHOR_NAME": "stage0",
    "GIT_AUTHOR_EMAIL": "stage0@localhost",
    "GIT_COMMITTER_NAME": "stage0",
    "GIT_COMMITTER_EMAIL": "stage0@localhost",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_CONFIG_NOSYSTEM": "1",
    "HOME": "",  # set per call; keeps user git config out of the run
}

BASE_COMMIT_MESSAGE = "stage0 base"

IGNORED_NAMES = {"__pycache__", ".pytest_cache", ".git", ".venv", "node_modules"}

# Created as a side effect of running code; never counted as an agent's change.
_SIDE_EFFECT_PARTS = ("__pycache__", ".pytest_cache")
_SIDE_EFFECT_SUFFIXES = (".pyc", ".pyo")

READ_ONLY_MECHANISM = (
    "filesystem read-only attribute set by the harness on every file matching the "
    "task's read_only_paths, after the base commit and before any agent session; "
    "identical for every arm. On Windows this blocks in-place writes and "
    "replace-by-rename. On POSIX a rename over a read-only file is still possible "
    "and new files can still be added, which is why every run also reports any "
    "change under a protected path."
)


class WorkspaceError(RuntimeError):
    pass


@dataclass
class CommandResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    wall_seconds: float

    def as_dict(self) -> dict:
        return asdict(self)


def matches_any(rel: str, patterns: Sequence[str]) -> bool:
    rel = rel.replace("\\", "/")
    return any(fnmatch.fnmatchcase(rel, p) for p in patterns)


def _is_side_effect(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    return any(p in _SIDE_EFFECT_PARTS for p in parts) or rel.endswith(_SIDE_EFFECT_SUFFIXES)


@dataclass
class Workspace:
    path: Path
    repo_name: str
    base_commit: str
    source_tree: Path
    read_only_patterns: list[str] = field(default_factory=list)
    read_only_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "repo_name": self.repo_name,
            "base_commit": self.base_commit,
            "source_tree": str(self.source_tree),
            "read_only_patterns": list(self.read_only_patterns),
            "read_only_files": list(self.read_only_files),
            "read_only_mechanism": READ_ONLY_MECHANISM if self.read_only_patterns else None,
        }

    # -- git helpers ----------------------------------------------------

    def git(self, *args: str, check: bool = True) -> CommandResult:
        return _run(["git", *args], cwd=self.path, check=check, git_env=True)

    def tree_hash(self) -> str:
        return self.git("rev-parse", "HEAD^{tree}").stdout.strip()

    def working_tree_hash(self) -> str:
        """Hash of the current working tree including uncommitted changes."""
        self.git("add", "-A")
        return self.git("write-tree").stdout.strip()

    def blob_sha(self, rel_path: str, rev: str = "HEAD") -> Optional[str]:
        r = self.git("rev-parse", f"{rev}:{rel_path}", check=False)
        return r.stdout.strip() if r.exit_code == 0 else None

    def diff_vs_base(self) -> str:
        self.git("add", "-A")
        return self.git("diff", "--cached", self.base_commit).stdout

    def is_dirty(self) -> bool:
        return bool(self.git("status", "--porcelain").stdout.strip())

    def files(self) -> list[str]:
        out = self.git("ls-files").stdout
        return [l.strip() for l in out.splitlines() if l.strip()]

    # -- integrity ------------------------------------------------------

    def is_protected(self, rel: str) -> bool:
        return matches_any(rel, self.read_only_patterns)

    def changed_paths(self) -> list[dict]:
        """Every path added, modified or deleted since the base commit, excluding
        side effects of running code (__pycache__, *.pyc, .pytest_cache)."""
        self.git("add", "-A")
        out = self.git(
            "diff", "--cached", "--name-status", "--no-renames", self.base_commit
        ).stdout
        names = {"A": "added", "M": "modified", "D": "deleted"}
        changes = []
        for line in out.splitlines():
            if not line.strip():
                continue
            status, _, rel = line.partition("\t")
            rel = rel.strip().replace("\\", "/")
            if not rel or _is_side_effect(rel):
                continue
            changes.append({"path": rel, "change": names.get(status[:1], status)})
        return sorted(changes, key=lambda c: c["path"])


def _run(
    argv: Sequence[str],
    cwd: str | os.PathLike,
    *,
    check: bool = True,
    timeout: int = 300,
    git_env: bool = False,
    extra_env: Optional[dict] = None,
) -> CommandResult:
    import time

    env = dict(os.environ)
    if git_env:
        env.update(DETERMINISTIC_GIT_ENV)
        # Point HOME at the workspace so no user-level git config leaks in.
        env["HOME"] = str(cwd)
        env["USERPROFILE"] = str(cwd)
    if extra_env:
        env.update(extra_env)

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        res = CommandResult(list(argv), 124, exc.stdout or "", "timeout", time.monotonic() - t0)
        if check:
            raise WorkspaceError(f"command timed out: {argv}")
        return res

    res = CommandResult(
        list(argv), proc.returncode, proc.stdout or "", proc.stderr or "", time.monotonic() - t0
    )
    if check and proc.returncode != 0:
        raise WorkspaceError(f"command failed ({proc.returncode}): {argv}\n{res.stderr[:2000]}")
    return res


def _copy_tree(src: Path, dst: Path) -> None:
    def _ignore(_dir, names):
        return [n for n in names if n in IGNORED_NAMES]

    shutil.copytree(src, dst, ignore=_ignore)


def _make_read_only(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def prepare_workspace(
    source_tree: str | os.PathLike,
    dest: str | os.PathLike,
    *,
    repo_name: Optional[str] = None,
    exclude: Sequence[str] = (),
    read_only_paths: Sequence[str] = (),
) -> Workspace:
    """Copy a task repo into `dest` and commit it at a deterministic base commit.

    `exclude` names top-level entries to withhold (used for held-out verifier
    tests, which must not exist in the workspace during the agent phase).
    `read_only_paths` are glob patterns made read-only AFTER the base commit, so
    the base commit SHA does not depend on them.
    """
    src = Path(source_tree).resolve()
    if not src.is_dir():
        raise WorkspaceError(f"task repo tree not found: {src}")
    dst = Path(dest).resolve()
    if dst.exists():
        raise WorkspaceError(f"workspace destination already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    _copy_tree(src, dst)
    for name in exclude:
        target = dst / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()

    _run(["git", "init", "--quiet", "-b", "main"], cwd=dst, git_env=True)
    _run(["git", "add", "-A"], cwd=dst, git_env=True)
    _run(
        ["git", "commit", "--quiet", "--no-gpg-sign", "-m", BASE_COMMIT_MESSAGE],
        cwd=dst,
        git_env=True,
    )
    base = _run(["git", "rev-parse", "HEAD"], cwd=dst, git_env=True).stdout.strip()

    read_only_files: list[str] = []
    if read_only_paths:
        listing = _run(["git", "ls-files"], cwd=dst, git_env=True).stdout
        for rel in listing.splitlines():
            rel = rel.strip()
            if rel and matches_any(rel, read_only_paths):
                _make_read_only(dst / rel)
                read_only_files.append(rel)
        if not read_only_files:
            raise WorkspaceError(f"read_only_paths {list(read_only_paths)} matched no files")

    return Workspace(
        path=dst,
        repo_name=repo_name or src.name,
        base_commit=base,
        source_tree=src,
        read_only_patterns=list(read_only_paths),
        read_only_files=sorted(read_only_files),
    )


# --------------------------------------------------------------------------
# Verification — identical for every arm
# --------------------------------------------------------------------------


@dataclass
class VerificationResult:
    solved: bool
    exit_code: int
    argv: list[str]
    stdout: str
    stderr: str
    wall_seconds: float
    verifier_paths_injected: list[str]

    def as_dict(self) -> dict:
        return asdict(self)


def verify(
    ws: Workspace,
    *,
    verifier_source: str | os.PathLike,
    verifier_dest_name: str,
    command: Sequence[str],
    timeout: int = 600,
) -> VerificationResult:
    """Inject held-out tests, run the deterministic verifier, report the outcome.

    Called identically for Arm A and Arm B. The verifier is copied in after the
    agent phase so agents could not have edited it.
    """
    src = Path(verifier_source).resolve()
    if not src.exists():
        raise WorkspaceError(f"verifier source not found: {src}")

    dest = ws.path / verifier_dest_name
    if dest.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    if src.is_dir():
        _copy_tree(src, dest)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    res = _run(list(command), cwd=ws.path, check=False, timeout=timeout)
    return VerificationResult(
        solved=res.exit_code == 0,
        exit_code=res.exit_code,
        argv=res.argv,
        stdout=res.stdout,
        stderr=res.stderr,
        wall_seconds=res.wall_seconds,
        verifier_paths_injected=[verifier_dest_name],
    )


def run_command(ws: Workspace, command: Sequence[str], timeout: int = 600) -> CommandResult:
    """Run a command in the workspace without raising on failure (visible tests)."""
    return _run(list(command), cwd=ws.path, check=False, timeout=timeout)


def _force_writable_and_retry(func, path, _exc) -> None:
    # Read-only files (protected paths, and git's own object files) would make a
    # plain rmtree fail silently and leave the workspace behind.
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError:
        pass


def cleanup_workspace(ws: Workspace) -> None:
    """Remove a workspace. Raw run artifacts live elsewhere and are unaffected."""
    if not Path(ws.path).exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(ws.path, onexc=_force_writable_and_retry)
    else:
        shutil.rmtree(ws.path, onerror=_force_writable_and_retry)
