"""dbclient - a small database client."""

from .cli import build_parser, config_from_args
from .client import Client
from .config import ClientConfig, load_config
from .pool import Pool, PoolExhausted
from .stats import describe

__all__ = [
    "Client", "ClientConfig", "load_config", "Pool", "PoolExhausted",
    "describe", "build_parser", "config_from_args",
]
