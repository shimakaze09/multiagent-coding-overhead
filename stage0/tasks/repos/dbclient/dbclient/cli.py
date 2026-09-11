"""Command-line entry point: `dbclient --host db --port 6000 ...`."""

import argparse

from .config import load_config


def build_parser():
    parser = argparse.ArgumentParser(prog="dbclient")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--max-conn", type=int, dest="max_conn",
                        help="maximum number of pooled connections")
    parser.add_argument("--timeout-s", type=float, dest="timeout_s")
    return parser


def config_from_args(argv):
    args = build_parser().parse_args(argv)
    data = {k: v for k, v in vars(args).items() if v is not None}
    return load_config(data)
