import pytest

from confkit import DictSource, EnvSource, Field, Schema, SettingsError, load


def test_first_source_wins():
    schema = Schema(Field("db.port", int, 5432))
    sources = [DictSource({"db.port": "6000"}), DictSource({"db.port": "7000"})]
    assert load(schema, sources) == {"db.port": 6000}


def test_default_is_used_when_no_source_defines_the_key():
    assert load(Schema(Field("db.port", int, 5432)), [DictSource({})]) == {"db.port": 5432}


def test_required_setting_missing():
    with pytest.raises(SettingsError, match=r"^db\.host: "):
        load(Schema(Field("db.host", str, required=True)), [DictSource({})])


def test_boolean_values():
    schema = Schema(Field("debug", bool))
    assert load(schema, [DictSource({"debug": "yes"})]) == {"debug": True}
    assert load(schema, [DictSource({"debug": " Off "})]) == {"debug": False}


def test_environment_source():
    env = EnvSource({"APP_DB_PORT": "6543"})
    assert env.name_for("db.port") == "APP_DB_PORT"
    assert load(Schema(Field("db.port", int)), [env]) == {"db.port": 6543}


def test_invalid_value_names_the_key():
    with pytest.raises(SettingsError, match=r"^db\.port: not an integer"):
        load(Schema(Field("db.port", int)), [DictSource({"db.port": "abc"})])


def test_unsupported_type_is_rejected_by_the_schema():
    with pytest.raises(ValueError):
        Schema(Field("ratio", float))
