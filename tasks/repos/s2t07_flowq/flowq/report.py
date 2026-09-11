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
        "peak": dict(engine.peak),
        "capacity": dict(engine.capacity),
    }


def render(summary):
    lines = [f"{'job':<20}{'state':<12}{'attempts':>8}"]
    for row in summary["jobs"]:
        lines.append(f"{row['job']:<20}{row['state']:<12}{row['attempts']:>8}")
    usage = ", ".join(f"{r} {summary['peak'][r]}/{summary['capacity'][r]}" for r in sorted(summary["peak"]))
    lines.append(f"finished at t={summary['t']:g}; peak usage: {usage}")
    return "\n".join(lines)
