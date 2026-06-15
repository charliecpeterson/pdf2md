"""Logging setup. The library attaches only a NullHandler; the CLI is the single
place that installs a real handler."""

from __future__ import annotations

import logging

_ROOT = "pdf2md"

logging.getLogger(_ROOT).addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{_ROOT}.{name}")


def configure_cli_logging(verbose: bool = False) -> None:
    """Install a stderr handler on the pdf2md logger. Called once, from the CLI."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger(_ROOT)
    root.handlers = [h for h in root.handlers if not isinstance(h, logging.NullHandler)]
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
