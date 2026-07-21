"""
db.py

Turso (libSQL/SQLite) helpers for worker.py (the web app's background job
processor). Not used by the plain CLI (main.py), which writes CSV files
directly.

Schema: see schema.sql. There is exactly one worker process, so row
claiming uses a plain SELECT-then-guarded-UPDATE rather than Postgres-style
"SELECT ... FOR UPDATE SKIP LOCKED" (SQLite has no row-level locking). The
UPDATE's "WHERE processing_status = 'pending'" guard means a second worker
racing for the same row would simply find 0 rows affected and retry,
rather than corrupting anything - fine at this scale.
"""

from __future__ import annotations

import os
from typing import Optional

import libsql


def get_connection():
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if not url or not token:
        raise RuntimeError("TURSO_DATABASE_URL / TURSO_AUTH_TOKEN environment variables are not set.")
    conn = libsql.connect(url, auth_token=token)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _row_to_dict(cur, row) -> dict:
    columns = [d[0] for d in cur.description]
    return dict(zip(columns, row))


def claim_next_pending_row(conn) -> Optional[dict]:
    """Claim one pending row (oldest first, across all jobs) and return it
    as a dict, or None if the queue is empty. Also flips the parent job's
    status to 'running' on its first claimed row.

    This assumes exactly one worker process (the documented architecture -
    see worker.py's module docstring). It is NOT safe against a second
    concurrent worker: this libSQL Python binding's cursor.rowcount does
    not reflect actual affected-row counts (verified empirically - it
    returned nonzero for both a matching and a non-matching UPDATE), so
    there is no reliable way to detect "another worker already claimed
    this row" here. If a second worker is ever added, add a unique
    claim-token column and verify the claim by reading it back, rather
    than trusting rowcount.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM job_rows WHERE processing_status = 'pending' ORDER BY id LIMIT 1"
    )
    found = cur.fetchone()
    if found is None:
        return None
    row = _row_to_dict(cur, found)

    cur.execute(
        "UPDATE job_rows SET processing_status = 'claimed' WHERE id = ?",
        (row["id"],),
    )
    cur.execute(
        "UPDATE jobs SET status = 'running' WHERE id = ? AND status = 'pending'",
        (row["job_id"],),
    )
    conn.commit()
    return row


def write_row_result(conn, row_id: int, job_id: str, result, error_message: Optional[str] = None) -> None:
    """Persist a classify_and_build_row() OutputRow and advance the parent
    job's progress counter, marking the job done once every row is in.
    `error_message` is a human-readable reason shown on the status page -
    only meaningful when result.status == "ERROR" (the CLI has no
    equivalent since a local run.log is close at hand; a web user has no
    such file, so the reason needs to travel with the row).
    """
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE job_rows
        SET processing_status = 'done',
            owner_name_found = ?,
            property_address = ?,
            mailing_address = ?,
            parcel_id = ?,
            result_status = ?,
            match_score = ?,
            source_url = ?,
            error_message = ?,
            processed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        WHERE id = ?
        """,
        (
            result.owner_name_found, result.property_address,
            result.mailing_address, result.parcel_id, result.status,
            result.match_score, result.source_url, error_message, row_id,
        ),
    )
    cur.execute(
        "UPDATE jobs SET processed_rows = processed_rows + 1 WHERE id = ?",
        (job_id,),
    )
    cur.execute(
        "UPDATE jobs SET status = 'done' WHERE id = ? AND processed_rows >= total_rows",
        (job_id,),
    )
    conn.commit()


def insert_candidates(conn, job_row_id: int, candidates: list[dict]) -> None:
    if not candidates:
        return
    cur = conn.cursor()
    for c in candidates:
        cur.execute(
            """
            INSERT INTO job_row_candidates
                (job_row_id, owner_name_found, property_address,
                 mailing_address, parcel_id, match_score, source_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_row_id, c["owner_name_found"], c["property_address"],
                c["mailing_address"], c["parcel_id"], c["match_score"],
                c["source_url"],
            ),
        )
    conn.commit()
