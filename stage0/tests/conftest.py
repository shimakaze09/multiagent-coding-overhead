import os
import subprocess
import sys
from pathlib import Path

import pytest

# --------------------------------------------------------------------------
# Local tests must never launch the real Claude Code CLI: that could consume
# subscription quota. Any subprocess whose executable basename is `claude` or
# `claude.exe` is refused before it starts. The mock CLI (tools/mock_claude.py,
# run through the Python interpreter) is unaffected.
# --------------------------------------------------------------------------

_ORIGINAL_POPEN_INIT = subprocess.Popen.__init__
REAL_CLAUDE_LAUNCH_ATTEMPTS: list = []


def _argv0(args) -> str:
    if isinstance(args, (list, tuple)) and args:
        return str(args[0])
    if isinstance(args, str) and args.strip():
        return args.split()[0]
    return ""


def _guarded_popen_init(self, args, *a, **k):
    base = _argv0(args).replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base in ("claude", "claude.exe"):
        REAL_CLAUDE_LAUNCH_ATTEMPTS.append(list(args) if isinstance(args, (list, tuple)) else [args])
        raise RuntimeError("real Claude Code CLI launch refused during local tests")
    return _ORIGINAL_POPEN_INIT(self, args, *a, **k)


subprocess.Popen.__init__ = _guarded_popen_init

STAGE0_ROOT = Path(__file__).resolve().parent.parent
if str(STAGE0_ROOT) not in sys.path:
    sys.path.insert(0, str(STAGE0_ROOT))

FIXTURES = STAGE0_ROOT / "tests" / "fixtures"
MOCK_CLI = STAGE0_ROOT / "tools" / "mock_claude.py"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def mock_cli_path() -> str:
    return str(MOCK_CLI)


@pytest.fixture
def clean_env(monkeypatch):
    """An environment with no API-billing variables, so the guard passes."""
    import config

    for var in config.BILLING_GUARD_VARS:
        monkeypatch.delenv(var, raising=False)
    return os.environ


@pytest.fixture(scope="session")
def mock_runs(tmp_path_factory):
    """Arm A and Arm B run once through the mock CLI, shared across test modules.

    Instrumentation validation only: the mock is not a model, so these are not
    Arm A / Arm B results.
    """
    import config
    from arms import multi_nl, single
    from harness import claude_cli
    from tasks import registry

    os.environ["STAGE0_CLAUDE_CLI"] = str(MOCK_CLI)
    try:
        cli = config.find_claude_cli()
        caps = claude_cli.detect_capabilities(cli)
        assert caps.ok, caps.missing_required
        task = registry.get_task("palindrome_punctuation")
        runs_dir = tmp_path_factory.mktemp("runs")
        cfg = config.RunConfig(model="mock-sonnet", limits=config.SMOKE_LIMITS)

        out = {}
        for arm, mod in (("A", single), ("B", multi_nl)):
            summary = mod.run(
                task=task,
                repeat_id=1,
                cfg=cfg,
                cli=cli,
                capability_report=caps.as_dict(),
                runs_dir=runs_dir,
            )
            out[arm] = (runs_dir / summary["run_id"], summary)
        out["runs_dir"] = runs_dir
        return out
    finally:
        os.environ.pop("STAGE0_CLAUDE_CLI", None)


@pytest.fixture(scope="session")
def mock_c1_run(tmp_path_factory):
    """Stage-1 C1_shared_worker_context once through the mock CLI, in its OWN runs
    directory, so the A/B mock fixture above is unchanged. Instrumentation only."""
    import config
    from arms import c1_shared_worker
    from harness import claude_cli
    from tasks import registry

    os.environ["STAGE0_CLAUDE_CLI"] = str(MOCK_CLI)
    try:
        cli = config.find_claude_cli()
        caps = claude_cli.detect_capabilities(cli)
        assert caps.ok, caps.missing_required
        runs_dir = tmp_path_factory.mktemp("runs_c1")
        summary = c1_shared_worker.run(
            task=registry.get_task("palindrome_punctuation"),
            repeat_id=1,
            cfg=config.RunConfig(model="mock-sonnet", limits=config.SMOKE_LIMITS),
            cli=cli,
            capability_report=caps.as_dict(),
            runs_dir=runs_dir,
        )
        return runs_dir / summary["run_id"], summary
    finally:
        os.environ.pop("STAGE0_CLAUDE_CLI", None)


@pytest.fixture(scope="session")
def all_mock_runs(mock_runs, mock_c1_run):
    return {"A": mock_runs["A"], "B": mock_runs["B"], "C1": mock_c1_run}
