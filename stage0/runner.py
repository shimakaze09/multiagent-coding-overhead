"""Stage 0 entry point.

    python runner.py diagnose            environment + CLI capability gate
    python runner.py smoke               1 task, Arm A + Arm B (never more)
    python runner.py ingest              rebuild the derived SQLite index
    python runner.py report              per-run report / arm comparison
    python runner.py trace               human-readable run trace
    python runner.py pilot --confirm-pilot   the full 12x2x3 pilot (gated)

Subscription usage protection: every command except `pilot` is capped by
config.SMOKE_LIMITS, and `pilot` refuses to start without --confirm-pilot.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from analysis import ingest as ingest_mod, report as report_mod
from arms import multi_nl, single
from harness import claude_cli
from tasks import registry

DEFAULT_SMOKE_TASK = "palindrome_punctuation"
MIN_PYTHON = (3, 11)


# --------------------------------------------------------------------------
# Milestone 0 - diagnostics
# --------------------------------------------------------------------------


def cmd_diagnose(args) -> int:
    out: dict = {"checks": {}, "generated_at": None}
    ok = True

    print("=== Stage 0 diagnostics ===\n")

    # -- python ---------------------------------------------------------
    py = sys.version_info[:3]
    py_ok = py >= MIN_PYTHON
    ok &= py_ok
    out["checks"]["python"] = {
        "version": ".".join(map(str, py)),
        "required": ">=3.11",
        "ok": py_ok,
    }
    print(f"[{_m(py_ok)}] Python {'.'.join(map(str, py))} (need >= 3.11)")

    # -- git ------------------------------------------------------------
    git_path = shutil.which("git")
    git_ver = ""
    if git_path:
        try:
            git_ver = (
                subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=30).stdout
                or ""
            ).strip()
        except (OSError, subprocess.SubprocessError):
            git_ver = ""
    git_ok = bool(git_ver)
    ok &= git_ok
    out["checks"]["git"] = {"path": git_path, "version": git_ver, "ok": git_ok}
    print(f"[{_m(git_ok)}] git: {git_ver or 'NOT FOUND'}")

    # -- billing guard --------------------------------------------------
    guard = config.check_billing_guard()
    out["checks"]["billing_guard"] = {
        "ok": guard.ok,
        "vars_present": list(guard.present_vars),
        "vars_tested": list(config.BILLING_GUARD_VARS),
        "note": "presence-only; no credential value was read, printed or logged",
    }
    print(f"[{_m(guard.ok)}] ANTHROPIC_API_KEY guard")
    if not guard.ok:
        print()
        print(guard.explain())
        print()
        out["verdict"] = "STOP - API billing possible"
        out["ok"] = False
        _write_diagnostics(out, args)
        return 2
    print("        no API-key / Bedrock / Vertex variables present")

    # -- claude cli -----------------------------------------------------
    cli = config.find_claude_cli()
    caps = claude_cli.detect_capabilities(cli)
    out["checks"]["claude_cli"] = caps.as_dict()
    if cli is None:
        ok = False
        print("[FAIL] claude CLI: NOT FOUND")
        for n in caps.notes:
            print(f"        {n}")
    else:
        print(f"[{_m(caps.ok)}] claude CLI {cli.version or '(version unknown)'}")
        print(f"        path: {cli.path}")
        print(f"        found via: {cli.discovered_via}")
        print("        required flags:")
        for flag in claude_cli.REQUIRED_FLAGS:
            print(f"          [{_m(caps.flags.get(flag))}] {flag}")
        print("        output formats:")
        for fmt, present in caps.output_formats.items():
            print(f"          [{_m(present)}] --output-format {fmt}")
        print("        optional / probed flags:")
        for flag in claude_cli.OPTIONAL_FLAGS:
            print(f"          [{'yes' if caps.flags.get(flag) else ' no'}] {flag}")
        if caps.missing_required:
            print(f"        MISSING REQUIRED: {caps.missing_required}")
        for n in caps.notes:
            print(f"        note: {n}")
        ok &= caps.ok

    # -- claude authentication ------------------------------------------
    auth = _check_cli_auth(cli)
    out["checks"]["claude_auth"] = auth
    print(f"[{_m(auth['ok'])}] claude CLI authentication")
    print(f"        logged in: {auth.get('logged_in')}   method: {auth.get('auth_method')}")
    print(f"        api provider: {auth.get('api_provider')}")
    if not auth["ok"]:
        ok = False
        for line in auth.get("remediation", []):
            print(f"        {line}")

    # -- tasks ----------------------------------------------------------
    try:
        tasks = registry.all_tasks()
        tasks_ok = bool(tasks)
        out["checks"]["tasks"] = {
            "count": len(tasks),
            "ids": sorted(tasks),
            "categories": sorted({t.category for t in tasks.values()}),
            "ok": tasks_ok,
        }
        print(f"[{_m(tasks_ok)}] tasks registered: {len(tasks)} ({', '.join(sorted(tasks))})")
    except registry.TaskError as exc:
        ok = False
        out["checks"]["tasks"] = {"ok": False, "error": str(exc)}
        print(f"[FAIL] tasks: {exc}")

    # -- limits ---------------------------------------------------------
    out["checks"]["limits"] = {
        "smoke": config.SMOKE_LIMITS.as_dict(),
        "pilot": config.PILOT_LIMITS.as_dict(),
        "pilot_requires_flag": "--confirm-pilot",
    }
    print(f"[ ok ] usage limits: smoke={config.SMOKE_LIMITS.max_sessions_per_invocation} sessions max, "
          f"pilot requires --confirm-pilot")

    out["checks"]["platform"] = {
        "platform": platform.platform(),
        "arm_c_implemented": False,
        "anthropic_sdk_used": False,
    }

    out["ok"] = bool(ok)
    out["verdict"] = "READY" if ok else "NOT READY - see failing checks above"
    print()
    print(f"=== verdict: {out['verdict']} ===")
    _write_diagnostics(out, args)
    return 0 if ok else 1


def _check_cli_auth(cli: Optional[config.ClaudeCli]) -> dict:
    """Ask the CLI about its own auth. Runs no model workload and costs nothing."""
    if cli is None:
        return {"ok": False, "logged_in": None, "reason": "no CLI found"}
    env, _manifest = claude_cli.build_child_env()
    try:
        proc = subprocess.run(
            [cli.path, "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "logged_in": None, "reason": f"{type(exc).__name__}"}

    data: dict = {}
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        pass

    logged_in = bool(data.get("loggedIn"))
    provider = data.get("apiProvider")
    method = data.get("authMethod")
    api_billing = method in config.API_KEY_SOURCES_MEANING_API_BILLING

    result = {
        "ok": logged_in and not api_billing,
        "logged_in": logged_in,
        "auth_method": method,
        "api_provider": provider,
        "exit_code": proc.returncode,
        "raw": data,
    }
    if not logged_in:
        result["remediation"] = [
            "The `claude` CLI has no usable credentials of its own.",
            "The Claude desktop application keeps its OAuth token in a separate",
            "store that the CLI does not read, so a harness-spawned session fails",
            "with authentication_failed and no experiment data can be collected.",
            "",
            "Sign the CLI in yourself, once, in an interactive terminal:",
            f'    "{cli.path}" auth login',
            "(or `claude setup-token` for a long-lived subscription token).",
            "",
            "This harness will not sign in for you and will never create an API key.",
        ]
    elif api_billing:
        result["remediation"] = [
            f"authMethod={method!r} indicates API-key billing, not subscription.",
            "Stage 0 refuses to run in that mode.",
        ]
    return result


def _write_diagnostics(out: dict, args) -> None:
    from harness import events as ev

    out["generated_at"] = ev.utc_now_iso()
    path = Path(getattr(args, "out", None) or (config.STAGE0_ROOT / "runs" / "diagnostics.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\ndiagnostics written to {path}")


def _m(v) -> str:
    return " ok " if v else "FAIL"


# --------------------------------------------------------------------------
# Running arms
# --------------------------------------------------------------------------


def _require_ready(strict_auth: bool = True) -> tuple[config.ClaudeCli, dict]:
    """Fail clearly, before spawning anything, if capabilities are unavailable."""
    config.preflight_billing_guard()

    cli = config.find_claude_cli()
    if cli is None:
        raise SystemExit(
            "No `claude` executable found. Run `python runner.py diagnose`.\n"
            "Set STAGE0_CLAUDE_CLI to the full path if it is installed elsewhere."
        )
    caps = claude_cli.detect_capabilities(cli)
    if not caps.ok:
        raise SystemExit(
            "Installed Claude Code is missing capabilities Stage 0 requires: "
            f"{caps.missing_required}\nRun `python runner.py diagnose` for detail."
        )
    if strict_auth:
        auth = _check_cli_auth(cli)
        if not auth["ok"]:
            lines = "\n  ".join(auth.get("remediation", ["(no detail)"]))
            raise SystemExit(
                "Claude Code CLI is not authenticated for subscription use, so no "
                "experiment can run.\n  " + lines
            )
    return cli, caps.as_dict()


def cmd_smoke(args) -> int:
    cli, caps = _require_ready()
    task = registry.get_task(args.task)
    cfg = config.RunConfig(model=args.model, limits=config.SMOKE_LIMITS)

    arms = ["A", "B"] if args.arm == "both" else [args.arm]
    if len(arms) > cfg.limits.max_tasks_per_invocation * 2:
        raise SystemExit("smoke would exceed the configured smoke limits")

    summaries = []
    for arm in arms:
        mod = single if arm == "A" else multi_nl
        print(f"\n--- running Arm {arm} on task {task.task_id} ---")
        summary = mod.run(
            task=task,
            repeat_id=args.repeat_id,
            cfg=cfg,
            cli=cli,
            capability_report=caps,
        )
        summaries.append(summary)
        print(f"    run_id : {summary['run_id']}")
        print(f"    solved : {summary['solved']}")
        print(f"    sessions: {summary['session_count']}")

    print("\n--- reports ---")
    reports = []
    for s in summaries:
        rd = config.RUNS_DIR / s["run_id"]
        r = report_mod.run_report(rd)
        reports.append(r)
        print()
        print(report_mod.render_run_report(r))

    if len(reports) == 2:
        print()
        print(report_mod.render_comparison(reports[0], reports[1]))
        print()
        print(report_mod.render_pair_summary(reports[0], reports[1]))
    return 0


def cmd_mock_smoke(args) -> int:
    """Run the full pipeline against tools/mock_claude.py.

    Instrumentation validation only: the mock is not a model and consumes no
    subscription quota. Results are NOT Arm A / Arm B findings.
    """
    config.preflight_billing_guard()
    mock = config.STAGE0_ROOT / "tools" / "mock_claude.py"
    os.environ["STAGE0_CLAUDE_CLI"] = str(mock)

    cli = config.find_claude_cli()
    caps = claude_cli.detect_capabilities(cli)
    if not caps.ok:
        raise SystemExit(f"mock CLI failed capability detection: {caps.missing_required}")

    task = registry.get_task(args.task)
    cfg = config.RunConfig(model="mock-sonnet", limits=config.SMOKE_LIMITS)
    runs_dir = Path(args.runs_dir) if args.runs_dir else config.RUNS_DIR / "mock"

    print("=" * 74)
    print("MOCK RUN - instrumentation validation only.")
    print("tools/mock_claude.py is not a model. These are NOT Arm A / Arm B results")
    print("and no subscription quota is consumed.")
    print("=" * 74)

    reports = []
    for arm, mod in (("A", single), ("B", multi_nl)):
        summary = mod.run(
            task=task,
            repeat_id=args.repeat_id,
            cfg=cfg,
            cli=cli,
            capability_report=caps.as_dict(),
            runs_dir=runs_dir,
        )
        r = report_mod.run_report(runs_dir / summary["run_id"])
        reports.append((runs_dir / summary["run_id"], r))
        print()
        print(report_mod.render_run_report(r))

    print()
    print(report_mod.render_comparison(reports[0][1], reports[1][1]))
    print()
    print(report_mod.human_trace(reports[1][0]))
    print()
    print(f"raw artifacts under: {runs_dir}")
    return 0


def cmd_pilot(args) -> int:
    if not args.confirm_pilot:
        raise SystemExit(
            "The full pilot launches many Claude Code sessions against your\n"
            "subscription (planned: 12 tasks x 2 arms x 3 repeats = 72 runs).\n"
            "It will not start without an explicit flag:\n"
            "    python runner.py pilot --confirm-pilot\n"
            "Use `python runner.py smoke` for development."
        )
    cli, caps = _require_ready()
    tasks = registry.all_tasks()
    limits = config.PILOT_LIMITS
    planned = len(tasks) * 2 * args.repeats
    if len(tasks) > limits.max_tasks_per_invocation:
        raise SystemExit(
            f"{len(tasks)} tasks exceeds max_tasks_per_invocation={limits.max_tasks_per_invocation}"
        )
    print(f"pilot: {len(tasks)} tasks x 2 arms x {args.repeats} repeats = {planned} runs")
    if len(tasks) < 12:
        print(
            f"NOTE: only {len(tasks)} task(s) are authored. The pilot design calls for 12 "
            "(4 cross-file feature, 4 cross-file bug, 2 repo-wide, 2 single-file control)."
        )
    if not args.i_really_mean_it:
        raise SystemExit(
            "Refusing to run the pilot: pass --i-really-mean-it as well.\n"
            "Stage 0 development is expected to use `smoke` only."
        )
    cfg = config.RunConfig(model=args.model, limits=limits)
    for task in tasks.values():
        for repeat_id in range(1, args.repeats + 1):
            for arm, mod in (("A", single), ("B", multi_nl)):
                s = mod.run(
                    task=task,
                    repeat_id=repeat_id,
                    cfg=cfg,
                    cli=cli,
                    capability_report=caps,
                )
                print(f"{s['run_id']}: solved={s['solved']}")
    return 0


# --------------------------------------------------------------------------
# Analysis commands
# --------------------------------------------------------------------------


def cmd_ingest(args) -> int:
    runs = ingest_mod.discover_runs(args.runs_dir)
    if not runs:
        print("no runs found")
        return 1
    db = Path(args.db or (config.STAGE0_ROOT / "analysis" / "stage0.sqlite3"))
    if db.exists():
        db.unlink()  # the DB is derived; always rebuilt from raw artifacts
    ids = ingest_mod.build(db, runs)
    print(f"ingested {len(ids)} run(s) into {db}")
    for i in ids:
        print(f"  {i}")
    return 0


def cmd_report(args) -> int:
    if args.run:
        rd = _resolve_run(args.run, args.runs_dir)
        print(report_mod.render_run_report(report_mod.run_report(rd)))
        return 0

    runs = ingest_mod.discover_runs(args.runs_dir)
    if not runs:
        print("no runs found")
        return 1
    reports = [report_mod.run_report(rd) for rd in runs]
    if args.task:
        reports = [r for r in reports if r["task_id"] == args.task]
    for r in reports:
        print(report_mod.render_run_report(r))
        print()
    by_arm: dict[str, list[dict]] = {}
    for r in reports:
        by_arm.setdefault(r["arm"], []).append(r)
    if "A" in by_arm and "B" in by_arm:
        print(report_mod.render_comparison(by_arm["A"][-1], by_arm["B"][-1]))
        print()
        # The pre-registered primary output for the most recent A/B pair.
        print(report_mod.render_pair_summary(by_arm["A"][-1], by_arm["B"][-1]))
    return 0


def cmd_trace(args) -> int:
    rd = _resolve_run(args.run, args.runs_dir)
    print(report_mod.human_trace(rd))
    return 0


def _resolve_run(run_id: str, runs_dir: Optional[str]) -> Path:
    base = Path(runs_dir or config.RUNS_DIR)
    p = base / run_id
    if p.is_dir():
        return p
    matches = [d for d in ingest_mod.discover_runs(base) if run_id in d.name]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"no run matching {run_id!r} in {base}")
    raise SystemExit(f"ambiguous run {run_id!r}: {[m.name for m in matches]}")


# --------------------------------------------------------------------------
# argparse
# --------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="runner.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diagnose", help="environment + Claude Code capability check")
    d.add_argument("--out", help="where to write diagnostics JSON")
    d.set_defaults(func=cmd_diagnose)

    s = sub.add_parser("smoke", help="one task, Arm A and/or Arm B")
    s.add_argument("--task", default=DEFAULT_SMOKE_TASK)
    s.add_argument("--arm", choices=("A", "B", "both"), default="both")
    s.add_argument("--model", default="sonnet")
    s.add_argument("--repeat-id", type=int, default=1, dest="repeat_id")
    s.set_defaults(func=cmd_smoke)

    m = sub.add_parser(
        "mock-smoke",
        help="run the pipeline against the mock CLI (no model, no quota used)",
    )
    m.add_argument("--task", default=DEFAULT_SMOKE_TASK)
    m.add_argument("--repeat-id", type=int, default=1, dest="repeat_id")
    m.add_argument("--runs-dir", dest="runs_dir")
    m.set_defaults(func=cmd_mock_smoke)

    p = sub.add_parser("pilot", help="full pilot (explicitly gated)")
    p.add_argument("--confirm-pilot", action="store_true", dest="confirm_pilot")
    p.add_argument("--i-really-mean-it", action="store_true", dest="i_really_mean_it")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--model", default="sonnet")
    p.set_defaults(func=cmd_pilot)

    i = sub.add_parser("ingest", help="rebuild the derived SQLite index")
    i.add_argument("--db")
    i.add_argument("--runs-dir", dest="runs_dir")
    i.set_defaults(func=cmd_ingest)

    r = sub.add_parser("report", help="per-run report and arm comparison")
    r.add_argument("--run")
    r.add_argument("--task")
    r.add_argument("--runs-dir", dest="runs_dir")
    r.set_defaults(func=cmd_report)

    t = sub.add_parser("trace", help="human-readable trace of one run")
    t.add_argument("run")
    t.add_argument("--runs-dir", dest="runs_dir")
    t.set_defaults(func=cmd_trace)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
