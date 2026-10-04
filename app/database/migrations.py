"""
Schema, as an ordered list of migrations.

Versioned rather than "CREATE TABLE IF NOT EXISTS" so an installed agent can be
upgraded without the user losing six months of history. Each migration runs
exactly once, inside a transaction, and the applied version is recorded.

NOTE ON WHAT IS AND IS NOT STORED.

`path_encrypted` is a BLOB. Everything else about a file is the derived,
non-sensitive part: the extension, the family key, the project, the category.
Those are what every query filters on, so keeping the sensitive field opaque
costs nothing at read time.

There is no column for file contents, no column for a window title, no column
for a URL, and no column for anything typed. That absence is the design. A
schema with a nullable `window_title` is one feature request away from being
populated.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

Migration = tuple[int, str, Callable[[sqlite3.Connection], None] | None, str]


_V1_SCHEMA = """
-- Raw file events, after whitelist, exclusion and debounce.
CREATE TABLE IF NOT EXISTS activities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at     TEXT    NOT NULL,          -- ISO 8601, UTC
    local_date      TEXT    NOT NULL,          -- YYYY-MM-DD, LOCAL day, for grouping
    event_type      TEXT    NOT NULL,          -- created | modified | moved | deleted
    source          TEXT    NOT NULL DEFAULT 'filesystem',

    -- The only sensitive column, and the only encrypted one.
    path_encrypted  BLOB,

    -- Derived, non-sensitive. These are what reports actually read.
    filename        TEXT,
    extension       TEXT,
    family_key      TEXT,                      -- version-stripped stem
    parent_folder   TEXT,                      -- leaf folder name only, never the chain
    root_label      TEXT,                      -- which approved root it came from

    project         TEXT,
    brand           TEXT,
    category        TEXT,

    repeat_count    INTEGER NOT NULL DEFAULT 1,
    size_bytes      INTEGER,

    session_id      INTEGER,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (session_id) REFERENCES work_sessions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_activities_date    ON activities(local_date);
CREATE INDEX IF NOT EXISTS idx_activities_project ON activities(project, local_date);
CREATE INDEX IF NOT EXISTS idx_activities_family  ON activities(family_key, local_date);
CREATE INDEX IF NOT EXISTS idx_activities_session ON activities(session_id);
-- Retention sweeps scan by age; without this they table-scan a year of rows.
CREATE INDEX IF NOT EXISTS idx_activities_occurred ON activities(occurred_at);


-- Logical sittings, built from activities by the session manager.
CREATE TABLE IF NOT EXISTS work_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL,
    ended_at        TEXT,
    local_date      TEXT    NOT NULL,
    project         TEXT,
    brand           TEXT,
    category        TEXT,
    activity_count  INTEGER NOT NULL DEFAULT 0,
    file_count      INTEGER NOT NULL DEFAULT 0,
    duration_minutes INTEGER,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_date ON work_sessions(local_date);


-- Generated reports. Kept indefinitely by default: a report is written work.
CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,                -- daily | weekly | monthly
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    format       TEXT NOT NULL DEFAULT 'markdown',
    content      TEXT NOT NULL,
    file_path    TEXT,
    generated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_reports_period ON reports(kind, period_start);


-- Privacy-relevant events ONLY. Never content, never paths, never filenames.
CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
    action     TEXT NOT NULL,
    detail     TEXT                              -- non-sensitive, e.g. a count
);

CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(occurred_at);


-- Small key/value store for runtime state that is not user configuration.
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


MIGRATIONS: list[Migration] = [
    (1, "initial schema", None, _V1_SCHEMA),
]


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def migrate(conn: sqlite3.Connection) -> int:
    """
    Apply everything newer than the recorded version.

    Each migration is its own transaction, so a failure half way through a
    sequence leaves the database at the last version that fully succeeded
    rather than in a state no migration describes.
    """
    applied = 0
    version = current_version(conn)

    for number, name, fn, sql in MIGRATIONS:
        if number <= version:
            continue
        try:
            conn.execute("BEGIN")
            if sql:
                conn.executescript(sql)
            if fn is not None:
                fn(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (number, name),
            )
            conn.execute("COMMIT")
            applied += 1
        except Exception:
            conn.execute("ROLLBACK")
            raise

    return applied
