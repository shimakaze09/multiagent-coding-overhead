"""Human-readable pool status for logs and the admin page."""


def describe(pool):
    return f"pool: {pool.in_use}/{pool.max_conn} in use (max_conn={pool.max_conn})"
