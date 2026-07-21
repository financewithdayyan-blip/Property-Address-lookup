-- schema.sql
--
-- Shared SQLite/libSQL schema (Turso) for the web version of the lookup
-- tool. The Next.js app (Vercel) and worker.py (Railway) both read/write
-- these tables - the database is the hand-off point between the two.
--
-- Apply with: turso db shell <db-name> < schema.sql
-- (or paste into the Turso dashboard's SQL console)
--
-- Note on IDs: unlike Postgres, SQLite has no server-side UUID generator
-- worth depending on, so job ids are generated in application code
-- (crypto.randomUUID() in the Next.js API route) and inserted as plain
-- TEXT rather than relying on a DB default.
--
-- Note on concurrency: there is exactly one worker process, so the
-- claim-next-row logic (see db.py / worker.py) is a plain
-- SELECT-then-UPDATE, not Postgres's "SELECT ... FOR UPDATE SKIP LOCKED"
-- (SQLite has no row-level locking, and this libSQL Python binding's
-- cursor.rowcount isn't reliable enough to build a claim guard on top
-- of - verified empirically). This is NOT safe against a second
-- concurrent worker as-is; see the docstring on claim_next_pending_row()
-- in db.py before ever running more than one worker.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | running | done
    total_rows      INTEGER NOT NULL,
    processed_rows  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job_rows (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id               TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    row_index            INTEGER NOT NULL,        -- preserves input CSV order for download
    owner_name_input      TEXT NOT NULL,
    county                TEXT NOT NULL,
    state                 TEXT NOT NULL,
    property_description  TEXT NOT NULL DEFAULT '', -- optional user-supplied legal description; not used for searching, but cross-checked against each candidate's own legal description to auto-resolve MULTIPLE MATCHES (see matching.py)
    processing_status     TEXT NOT NULL DEFAULT 'pending', -- pending | claimed | done
    owner_name_found      TEXT NOT NULL DEFAULT '',
    property_address      TEXT NOT NULL DEFAULT '',
    mailing_address        TEXT NOT NULL DEFAULT '',
    parcel_id               TEXT NOT NULL DEFAULT '',
    result_status           TEXT NOT NULL DEFAULT '', -- FOUND | LOW CONFIDENCE | MULTIPLE MATCHES | NOT FOUND | ERROR
    match_score              TEXT NOT NULL DEFAULT '',
    source_url                TEXT NOT NULL DEFAULT '',
    error_message              TEXT,                  -- human-readable reason, only set when result_status = 'ERROR'
    processed_at                TEXT
);

-- Supports "claim the next pending row across all jobs".
CREATE INDEX IF NOT EXISTS idx_job_rows_claim
    ON job_rows (processing_status, id);

CREATE INDEX IF NOT EXISTS idx_job_rows_job_id ON job_rows (job_id, row_index);

-- DB equivalent of the CLI's "*_multiple_matches.csv" companion file - every
-- raw candidate considered for a MULTIPLE MATCHES row, for manual review.
CREATE TABLE IF NOT EXISTS job_row_candidates (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_row_id            INTEGER NOT NULL REFERENCES job_rows(id) ON DELETE CASCADE,
    owner_name_found       TEXT NOT NULL DEFAULT '',
    property_address        TEXT NOT NULL DEFAULT '',
    mailing_address           TEXT NOT NULL DEFAULT '',
    parcel_id                  TEXT NOT NULL DEFAULT '',
    match_score                 TEXT NOT NULL DEFAULT '',
    source_url                    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_job_row_candidates_job_row_id
    ON job_row_candidates (job_row_id);
