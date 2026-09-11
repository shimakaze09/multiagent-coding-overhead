"""A minimal connection pool (connections are opaque integer handles here)."""


class PoolExhausted(RuntimeError):
    pass


class Pool:
    def __init__(self, max_conn=10):
        if max_conn < 1:
            raise ValueError("max_conn must be at least 1")
        self.max_conn = max_conn
        self._in_use = set()
        self._next = 0

    @property
    def in_use(self):
        return len(self._in_use)

    def acquire(self):
        if len(self._in_use) >= self.max_conn:
            raise PoolExhausted(
                f"all {self.max_conn} connections in use (max_conn={self.max_conn})"
            )
        self._next += 1
        self._in_use.add(self._next)
        return self._next

    def release(self, conn):
        self._in_use.discard(conn)
