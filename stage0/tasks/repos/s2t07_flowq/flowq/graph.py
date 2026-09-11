"""Dependency-graph helpers."""

import heapq


def dependents(wf):
    """key -> keys of the jobs that depend on it."""
    out = {k: [] for k in wf.jobs}
    for job in wf:
        for dep in dict.fromkeys(job.dep_keys):
            out[dep].append(job.key)
    return out


def find_cycle(wf):
    """Display names along one dependency cycle, or None."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {k: WHITE for k in wf.jobs}
    stack = []

    def visit(k):
        colour[k] = GREY
        stack.append(k)
        for d in wf.jobs[k].dep_keys:
            if colour[d] == GREY:
                loop = stack[stack.index(d):] + [d]
                return [wf.jobs[x].name for x in loop]
            if colour[d] == WHITE:
                found = visit(d)
                if found:
                    return found
        stack.pop()
        colour[k] = BLACK
        return None

    for k in wf.jobs:
        if colour[k] == WHITE:
            found = visit(k)
            if found:
                return found
    return None


def toposort(wf):
    """A valid order; among jobs whose dependencies are done, higher priority
    first, then by key (docs/scheduling.md)."""
    remaining = {k: len(set(j.dep_keys)) for k, j in wf.jobs.items()}
    heap = [(-wf.jobs[k].priority, k) for k, n in remaining.items() if n == 0]
    heapq.heapify(heap)
    after = dependents(wf)
    order = []
    while heap:
        _prio, k = heapq.heappop(heap)
        order.append(wf.jobs[k].name)
        for d in after[k]:
            remaining[d] -= 1
            if remaining[d] == 0:
                heapq.heappush(heap, (-wf.jobs[d].priority, d))
    return order


def descendants(wf, key):
    after = dependents(wf)
    seen, todo = set(), [key]
    while todo:
        for d in after[todo.pop()]:
            if d not in seen:
                seen.add(d)
                todo.append(d)
    return seen
