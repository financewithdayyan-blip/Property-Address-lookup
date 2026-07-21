"""
logger.py

Sets up run logging: every action, HTTP retry, and error goes to a
`run.log` file written alongside the output CSV, plus a concise stream to
the console so you can watch progress live.
"""

from __future__ import annotations

import logging
import os


def setup_logger(output_csv_path: str, verbose: bool = False) -> logging.Logger:
    """Create/reset the 'bluebird' logger and point its FileHandler at
    run.log in the same directory as the output CSV.
    """
    output_dir = os.path.dirname(os.path.abspath(output_csv_path)) or "."
    os.makedirs(output_dir, exist_ok=True)
    log_path = os.path.join(output_dir, "run.log")

    logger = logging.getLogger("bluebird")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()  # avoid duplicate handlers if called twice (e.g. tests)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    logger.info("Logging to %s", log_path)
    return logger


def setup_worker_logger(verbose: bool = False) -> logging.Logger:
    """Like setup_logger(), but stdout-only - for worker.py, which runs on
    a host (Railway) that captures stdout natively rather than a local
    filesystem worth writing a run.log to.
    """
    logger = logging.getLogger("bluebird")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)
    return logger


def get_logger() -> logging.Logger:
    """Fetch the already-configured 'bluebird' logger from other modules."""
    return logging.getLogger("bluebird")
