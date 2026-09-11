"""The client object applications use."""

from .pool import Pool


class Client:
    def __init__(self, config):
        self.config = config
        self.pool = Pool(max_conn=config.max_conn)

    def query(self, sql):
        conn = self.pool.acquire()
        try:
            return f"[{self.config.host}:{self.config.port}#{conn}] {sql}"
        finally:
            self.pool.release(conn)
