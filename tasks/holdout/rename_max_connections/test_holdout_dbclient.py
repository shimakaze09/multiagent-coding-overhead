"""Held-out deterministic verifier for the `rename_max_connections` task.

Injected into the workspace only AFTER the agent phase ends, identically for
every arm, and run on its own (`--noconftest`, not together with the visible
tests), so SOLVED is decided by these tests alone.
"""

import warnings

import pytest

from dbclient import (Client, Pool, PoolExhausted, build_parser, config_from_args,
                      describe, load_config)


# The statement asks for messages that mention `max_connections`; a message
# naming the option spelling (`--max-connections`) mentions it too.
MENTIONS_NEW_NAME = r"max[_-]connections"


def _no_deprecation(fn, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        return fn(*args, **kwargs)


# --- the new name ---------------------------------------------------------


def test_config_key():
    cfg = _no_deprecation(load_config, {"max_connections": 5})
    assert cfg.max_connections == 5
    assert _no_deprecation(load_config, {}).max_connections == 10


def test_config_validation_uses_the_new_name():
    with pytest.raises(ValueError, match=MENTIONS_NEW_NAME):
        load_config({"max_connections": 0})


def test_pool_argument_attribute_and_message():
    pool = _no_deprecation(Pool, max_connections=2)
    assert pool.max_connections == 2
    pool.acquire()
    pool.acquire()
    with pytest.raises(PoolExhausted) as err:
        pool.acquire()
    assert "max_connections=2" in str(err.value)
    assert "max_conn=" not in str(err.value)
    with pytest.raises(ValueError, match=MENTIONS_NEW_NAME):
        Pool(max_connections=0)


def test_pool_positional_argument_still_works():
    assert _no_deprecation(Pool, 3).max_connections == 3


def test_describe():
    assert describe(Pool(max_connections=3)) == "pool: 0/3 in use (max_connections=3)"


def test_command_line_option():
    cfg = _no_deprecation(config_from_args, ["--max-connections", "4"])
    assert cfg.max_connections == 4
    assert "--max-connections" in build_parser().format_help()


def test_client_uses_the_configured_limit():
    client = Client(load_config({"max_connections": 7}))
    assert client.pool.max_connections == 7


# --- the old names keep working for one release -----------------------------


def test_old_config_key_warns():
    with pytest.warns(DeprecationWarning, match=MENTIONS_NEW_NAME):
        cfg = load_config({"max_conn": 7})
    assert cfg.max_connections == 7


def test_new_key_wins_when_both_are_given():
    with pytest.warns(DeprecationWarning, match=MENTIONS_NEW_NAME):
        cfg = load_config({"max_conn": 7, "max_connections": 9})
    assert cfg.max_connections == 9


def test_old_pool_keyword_warns():
    with pytest.warns(DeprecationWarning, match=MENTIONS_NEW_NAME):
        pool = Pool(max_conn=4)
    assert pool.max_connections == 4


def test_old_command_line_option_warns():
    with pytest.warns(DeprecationWarning, match=MENTIONS_NEW_NAME):
        cfg = config_from_args(["--max-conn", "6"])
    assert cfg.max_connections == 6


# --- behaviour other than the name must not change --------------------------


def test_other_behaviour_unchanged():
    cfg = load_config({"host": "db", "port": "6000", "timeout_s": "2.5"})
    assert (cfg.host, cfg.port, cfg.timeout_s) == ("db", 6000, 2.5)
    with pytest.raises(ValueError, match="unknown config keys"):
        load_config({"hots": "db"})
    client = Client(load_config({"host": "db"}))
    assert client.query("SELECT 1") == "[db:5432#1] SELECT 1"
    pool = Pool(1)
    handle = pool.acquire()
    pool.release(handle)
    assert pool.in_use == 0
