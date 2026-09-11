# Scheduling

## Order

A job is *ready* when all of its dependencies succeeded (or, after a failed
attempt, when its backoff has elapsed). Ready jobs start in this order:

1. higher `priority` first;
2. among equal priority, first come first served: the job that became ready
   earliest starts first. A job's ready time is when it became ready for its
   current attempt, so a retried job queues behind jobs that were already
   waiting;
3. then by normalized name.

`Workflow.plan()` lists a valid order without running anything: among jobs
whose dependencies come earlier, higher priority first, then by normalized
name.

## Parallelism

At most `max_parallel` jobs (default 2) run at once. Jobs start in scheduling
order while slots are free.

## Failure

A failed attempt is retried while attempts remain; when they are exhausted the
job fails and every job depending on it, directly or not, is skipped.

## Crash and resume

`persistence.snapshot(engine)` can be taken at any time and
`persistence.restore(workflow, snapshot, handlers)` continues from it.
Resuming must not repeat finished work and must not change the order in which
waiting jobs start. An interrupted (running) job is ready again at the
snapshot time, and its interrupted attempt is not counted.
