import pytest

from dbclient import Client, Pool, PoolExhausted, config_from_args, load_config


def test_defaults():
    cfg = load_config({})
    assert (cfg.host, cfg.port, cfg.timeout_s) == ("localhost", 5432, 30.0)


def test_unknown_key_is_rejected():
    with pytest.raises(ValueError):
        load_config({"hots": "db"})


def test_pool_limits_connections():
    pool = Pool(2)
    first = pool.acquire()
    pool.acquire()
    with pytest.raises(PoolExhausted):
        pool.acquire()
    pool.release(first)
    pool.acquire()
    assert pool.in_use == 2


def test_cli_host_and_port():
    cfg = config_from_args(["--host", "db", "--port", "6000"])
    assert (cfg.host, cfg.port) == ("db", 6000)


def test_client_query():
    client = Client(load_config({"host": "db"}))
    assert client.query("SELECT 1") == "[db:5432#1] SELECT 1"
