"""Client configuration, loaded from a mapping (e.g. a parsed TOML file)."""

from dataclasses import dataclass

DEFAULTS = {"host": "localhost", "port": 5432, "max_conn": 10, "timeout_s": 30.0}


@dataclass(frozen=True)
class ClientConfig:
    host: str
    port: int
    max_conn: int
    timeout_s: float


def load_config(data):
    unknown = set(data) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    merged = {**DEFAULTS, **data}
    if int(merged["max_conn"]) < 1:
        raise ValueError("max_conn must be at least 1")
    return ClientConfig(
        host=str(merged["host"]),
        port=int(merged["port"]),
        max_conn=int(merged["max_conn"]),
        timeout_s=float(merged["timeout_s"]),
    )
