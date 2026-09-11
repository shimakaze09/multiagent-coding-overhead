"""Held-out deterministic verifier for the `settings_list_fields` task.

Injected into the workspace only AFTER the agent phase ends, identically for
every arm, and run on its own (`--noconftest`, not together with the visible
tests), so SOLVED is decided by these tests alone.
"""

import pytest

from confkit import DictSource, EnvSource, Field, Schema, SettingsError, load
from confkit import coerce


def _load_one(field, raw):
    return load(Schema(field), [DictSource({field.name: raw})])[field.name]


# --- the requested feature ------------------------------------------------


def test_task_example():
    schema = Schema(Field("cache.hosts", list, item_type=str),
                    Field("cache.ports", list, item_type=int))
    got = load(schema, [DictSource({"cache.hosts": "a.local, b.local", "cache.ports": "7000,7001"})])
    assert got == {"cache.hosts": ["a.local", "b.local"], "cache.ports": [7000, 7001]}


def test_items_use_the_scalar_rules_of_their_type():
    assert _load_one(Field("flags", list, item_type=bool), "yes, no ,1") == [True, False, True]
    assert _load_one(Field("ports", list, item_type=int), " 1 ,2,  3 ") == [1, 2, 3]
    assert _load_one(Field("names", list, item_type=str), "  x ,y  ") == ["x", "y"]


def test_single_item():
    assert _load_one(Field("ports", list, item_type=int), "8080") == [8080]


def test_empty_value_is_an_empty_list():
    assert _load_one(Field("names", list, item_type=str), "") == []
    assert _load_one(Field("ports", list, item_type=int), "") == []


def test_invalid_item_names_the_key():
    with pytest.raises(SettingsError) as err:
        _load_one(Field("cache.ports", list, item_type=int), "7000,x")
    assert str(err.value).startswith("cache.ports:")


def test_environment_variables_work_the_same_way():
    schema = Schema(Field("cache.ports", list, item_type=int))
    assert load(schema, [EnvSource({"APP_CACHE_PORTS": "1, 2"})]) == {"cache.ports": [1, 2]}


def test_first_source_wins_for_lists():
    schema = Schema(Field("names", list, item_type=str))
    got = load(schema, [EnvSource({"APP_NAMES": "env"}), DictSource({"names": "a,b"})])
    assert got == {"names": ["env"]}


def test_list_default_is_used_when_missing():
    schema = Schema(Field("ports", list, [1, 2], item_type=int))
    assert load(schema, [DictSource({})]) == {"ports": [1, 2]}


@pytest.mark.parametrize("bad", [
    lambda: Schema(Field("x", list)),
    lambda: Schema(Field("x", list, item_type=list)),
    lambda: Schema(Field("x", list, item_type=float)),
])
def test_list_without_a_valid_scalar_item_type_is_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        bad()


# --- existing behaviour that must NOT change --------------------------------


def test_scalar_fields_unchanged():
    schema = Schema(Field("a", int, 5), Field("b", bool), Field("c", str, required=True))
    assert load(schema, [DictSource({"b": "on", "c": "  hi "})]) == {"a": 5, "b": True, "c": "hi"}
    with pytest.raises(SettingsError, match=r"^c: required setting is missing$"):
        load(schema, [DictSource({})])


def test_converters_unchanged():
    assert coerce.to_bool(" TRUE ") is True
    with pytest.raises(ValueError, match="not a boolean"):
        coerce.to_bool("maybe")
    with pytest.raises(ValueError, match="not an integer"):
        coerce.to_int("1.5")
    assert coerce.to_str("  x ") == "x"


def test_sources_unchanged():
    assert DictSource({"n": 3}).get("n") == "3"
    assert DictSource({}).get("n") is None
    assert EnvSource({"X_A_B": "v"}, prefix="X_").get("a.b") == "v"


def test_schema_validation_unchanged():
    with pytest.raises(ValueError):
        Schema(Field("a"), Field("a"))
    with pytest.raises(ValueError):
        Schema(Field("ratio", float))
    assert Field("a", int, 5).has_default() and not Field("a").has_default()
