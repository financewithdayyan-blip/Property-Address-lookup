"""
worker.py

Long-running background processor for the web version of the lookup
tool. Polls Postgres for pending job_rows (uploaded via the Next.js app
in web/), runs the exact same scraper.search() + classify_and_build_row()
logic the CLI (main.py) uses, and writes results back to Postgres.

This exists because Vercel functions have strict execution time limits
(and can't launch a real browser at all) - a batch of 50-500 rows at a
polite 2-4s/request pace can take many minutes, so all scraping happens
here instead, on a host with no such limit. Run continuously with:

    python worker.py

Requires DATABASE_URL (see schema.sql / DEPLOY.md).
"""

from __future__ import annotations

import time
import traceback
from typing import Dict, Optional

import db
from county_configs import get_county_config
from logger import setup_worker_logger
from matching import OutputRow, classify_and_build_row
import scraper
from scraper import RateLimiter, ScraperError, make_delay_bounds, new_session

POLL_INTERVAL_SECONDS = 2.0
DELAY_CENTER_SECONDS = 3.0  # same default as the CLI's --delay
DB_RECONNECT_WAIT_SECONDS = 5.0

# Only search_types that don't need a real browser can run here - Duval
# (and anything else needing Selenium) isn't supported in the web version
# yet: no Chrome/chromedriver on this host, and it's not worth adding
# until a county actually needs it. See DEPLOY.md.
WEB_SUPPORTED_SEARCH_TYPES = {"arcgis_query"}


class DBMultiMatchWriter:
    """Adapter so classify_and_build_row() (which expects a
    csv.DictWriter-like object exposing `.writerow(dict)`) can write
    MULTIPLE MATCHES candidates into Postgres instead of a CSV file.
    Buffers rows in memory and is flushed in one batch insert after
    classification finishes (see process_row()).
    """

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def writerow(self, row: dict) -> None:
        self.rows.append(row)


class SessionBundle:
    __slots__ = ("session", "rate_limiter")

    def __init__(self, session, rate_limiter) -> None:
        self.session = session
        self.rate_limiter = rate_limiter


def _write_error(conn, row: dict, message: str) -> None:
    error_row = OutputRow(row["owner_name_input"], row["county"], row["state"], status="ERROR")
    db.write_row_result(conn, row["id"], row["job_id"], error_row, error_message=message)


def process_row(conn, logger, sessions: Dict[str, SessionBundle], row: dict) -> None:
    owner_name_input = row["owner_name_input"]
    county = row["county"]
    state = row["state"]

    config = get_county_config(county, state)

    if config is None:
        logger.error('No county config for "%s, %s" (owner=%r). Marking ERROR.',
                     county, state, owner_name_input)
        _write_error(conn, row, f'No county config for "{county}, {state}".')
        return

    if config.get("search_type") not in WEB_SUPPORTED_SEARCH_TYPES:
        logger.info(
            '%s (search_type=%s) is not supported in the web version yet - '
            "marking ERROR for owner=%r.",
            config.get("display_name", county), config.get("search_type"), owner_name_input,
        )
        _write_error(
            conn, row,
            f"{config.get('display_name', county)} isn't supported in the web "
            "version yet (it needs browser automation) - use the CLI instead.",
        )
        return

    bundle = sessions.get(config["display_name"])
    if bundle is None:
        bundle = SessionBundle(new_session(), RateLimiter(*make_delay_bounds(DELAY_CENTER_SECONDS)))
        sessions[config["display_name"]] = bundle

    logger.info('Searching %s for owner=%r (county=%s, state=%s)',
                config["display_name"], owner_name_input, county, state)
    try:
        matches = scraper.search(config, owner_name_input, bundle.session, bundle.rate_limiter)
    except ScraperError as exc:
        logger.error("Scrape error for %r in %s: %s", owner_name_input, config["display_name"], exc)
        _write_error(conn, row, str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - never let one bad row kill the worker
        logger.error(
            "Unexpected error for %r in %s: %s\n%s",
            owner_name_input, config["display_name"], exc, traceback.format_exc(),
        )
        _write_error(conn, row, f"Unexpected error: {exc}")
        return

    multi_writer = DBMultiMatchWriter()
    result = classify_and_build_row(owner_name_input, county, state, matches, logger, multi_writer)
    db.write_row_result(conn, row["id"], row["job_id"], result)
    if multi_writer.rows:
        db.insert_candidates(conn, row["id"], multi_writer.rows)


def _connect_with_retry(logger):
    """db.get_connection() itself can fail (transient DNS/network hiccups
    are normal for a long-running client of a remote HTTP-based DB) - this
    must never propagate, or a reconnect attempt failing once would take
    the whole worker down along with it (exactly what happened during
    testing: an idle connection got dropped, and the very next connect
    attempt hit a transient DNS error and crashed the process).
    """
    while True:
        try:
            return db.get_connection()
        except Exception:
            logger.error(
                "Failed to connect to the database, retrying in %.0fs:\n%s",
                DB_RECONNECT_WAIT_SECONDS, traceback.format_exc(),
            )
            time.sleep(DB_RECONNECT_WAIT_SECONDS)


def main() -> None:
    logger = setup_worker_logger()
    logger.info("Worker starting. Supported search_types: %s", WEB_SUPPORTED_SEARCH_TYPES)

    conn = _connect_with_retry(logger)
    sessions: Dict[str, SessionBundle] = {}

    while True:
        try:
            row = db.claim_next_pending_row(conn)
        except Exception:
            logger.error("DB error while claiming a row, reconnecting:\n%s", traceback.format_exc())
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - already broken, nothing to do
                pass
            conn = _connect_with_retry(logger)
            continue

        if row is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        try:
            process_row(conn, logger, sessions, row)
        except Exception:
            # process_row() catches its own scrape/DB errors, so this
            # should be unreachable - but the worker must never die
            # outright just because one row misbehaved unexpectedly.
            logger.error("Unhandled error processing row id=%s:\n%s", row.get("id"), traceback.format_exc())
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001 - connection may already be broken; next
                pass           # loop iteration's claim attempt will reconnect if so


if __name__ == "__main__":
    main()
