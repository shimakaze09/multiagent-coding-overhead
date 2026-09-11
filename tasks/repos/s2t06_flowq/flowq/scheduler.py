"""Which ready jobs start now (docs/scheduling.md).

Ready jobs are considered in scheduling order: higher priority first; among
equal priority, the job that became ready earliest first; then by key. A job's
ready time is when it became ready for its current attempt. At most ``slots``
jobs start.
"""


def order_key(run):
    return (-run.spec.priority, run.ready_since, run.spec.key)


def pick(ready_runs, slots):
    return sorted(ready_runs, key=order_key)[:max(0, slots)]
