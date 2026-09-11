"""Run summaries."""

from .state import FAILED, SKIPPED, SUCCEEDED


def summarize(engine):
    runs = list(engine.runs.values())
    return {
        "t": engine.clock.now,
        "jobs": [{"job": r.spec.name, "state": r.state, "attempts": r.attempts} for r in runs],
        "succeeded": [r.spec.name for r in runs if r.state == SUCCEEDED],
        "failed": [r.spec.name for r in runs if r.state == FAILED],
        "skipped": [r.spec.name for r in runs if r.state == SKIPPED],
        "max_parallel": engine.max_parallel,
    }


def render(summary):
    lines = [f"{'job':<20}{'state':<12}{'attempts':>8}"]
    for row in summary["jobs"]:
        lines.append(f"{row['job']:<20}{row['state']:<12}{row['attempts']:>8}")
    lines.append(f"finished at t={summary['t']:g} with at most {summary['max_parallel']} jobs in parallel")
    return "\n".join(lines)
