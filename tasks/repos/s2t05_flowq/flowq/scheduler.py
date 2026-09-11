"""Which ready jobs start now (docs/scheduling.md).

Ready jobs are considered in scheduling order: higher priority first; among
equal priority, the job that became ready earliest first; then by key. A job's
ready time is when it became ready for its current attempt. Jobs start in that
order while their resources fit the free capacity; the first job that does not
fit blocks every job after it (no backfilling).
"""


def order_key(run):
    return (-run.spec.priority, run.ready_since, run.spec.key)


def pick(ready_runs, capacity, in_use):
    free = {r: capacity[r] - in_use.get(r, 0) for r in capacity}
    chosen = []
    for run in sorted(ready_runs, key=order_key):
        need = run.spec.resource_map()
        if any(n > free.get(r, 0) for r, n in need.items()):
            break
        for r, n in need.items():
            free[r] -= n
        chosen.append(run)
    return chosen
