"""
SQLite access. One connection per thread, WAL, and the repository.

WHY WAL. The tracker writes from watchdog's observer thread while the UI reads
on the main one. In the default rollback journal those block each other, and a
blocked writer in a filesystem callback means watchdog's queue backs up behind
a UI repaint. WAL lets readers and the writer proceed together, which is the
difference between a tracker that keeps up with a Premiere export and one that
drops events.

WHY A CONNECTION PER THREAD. SQLite connections are not safe to share across
threads, and the usual workaround — check_same_thread=False plus a lock — turns
every read into a queue behind every write. Thread-local connections cost a few
file handles and remove the class of bug entirely.

NEVER BLOCK THE FILESYSTEM. `busy_timeout` is deliberately short. If the
database is locked, dropping one activity is strictly better than holding up a
watchdog callback; the person's file save must never wait on this program.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .migrations import migrate
from .models import Activity

log = logging.getLogger(__name__)


class Database:
    """Owns the file, the connections and the schema."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._initialised = False

    # ------------------------------------------------------------------
    def _configure(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL rather than FULL: with WAL this is durable across application
        # crashes and only risks the last transactions on a power cut. For
        # activity metadata that trade is obviously right — FULL would fsync on
        # every file save the person makes.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=3000")
        conn.execute("PRAGMA temp_store=MEMORY")

    def connection(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            return conn

        conn = sqlite3.connect(str(self.path), timeout=3.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        self._configure(conn)
        self._local.conn = conn

        with self._init_lock:
            if not self._initialised:
                applied = migrate(conn)
                if applied:
                    log.info("Applied %d migration(s) to %s", applied, self.path)
                self._initialised = True
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connection()
        conn.execute("BEGIN")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def checkpoint(self) -> None:
        """
        Fold the WAL back into the main file.

        Worth doing on a clean shutdown: a WAL that grows for weeks makes the
        data directory look alarming and slows the first read after a restart.
        """
        try:
            self.connection().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as exc:
            log.debug("Checkpoint skipped: %s", exc)


# ======================================================================
class ActivityRepository:
    """
    Reads and writes activities. The only module that touches those tables.

    It is handed a cipher rather than building one, so a test can pass a fake
    and so there is exactly one decision, at startup, about how paths are
    protected.
    """

    def __init__(self, db: Database, cipher: Any | None = None) -> None:
        self._db = db
        self._cipher = cipher

    # ------------------------------------------------------------------
    def add(self, activity: Activity) -> int | None:
        """
        Store one activity. Returns its id, or None if it was dropped.

        IF THE PATH CANNOT BE ENCRYPTED, THE PATH IS DROPPED — the activity is
        still stored, without it. The alternative, writing the plaintext
        "just this once", is how a database ends up with a readable path nobody
        remembers putting there. The derived fields are enough for every report;
        only "open this file" stops working.
        """
        encrypted: bytes | None = None
        if activity.full_path and self._cipher is not None:
            try:
                encrypted = self._cipher.encrypt(activity.full_path)
            except Exception:
                log.exception("Could not encrypt a path; storing the activity without it.")
                encrypted = None

        row = activity.to_row(encrypted)
        columns = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)

        try:
            cur = self._db.connection().execute(
                f"INSERT INTO activities ({columns}) VALUES ({placeholders})", row
            )
            return int(cur.lastrowid) if cur.lastrowid else None
        except sqlite3.Error as exc:
            # Never raise into a watchdog callback: an exception there kills
            # the observer thread and tracking silently stops for the rest of
            # the session.
            log.error("Could not store activity: %s", exc)
            return None

    def decrypt_path(self, activity_id: int) -> str | None:
        """The plaintext path for one activity, for an explicit 'open file'."""
        if self._cipher is None:
            return None
        row = self._db.connection().execute(
            "SELECT path_encrypted FROM activities WHERE id = ?", (activity_id,)
        ).fetchone()
        if row is None or row["path_encrypted"] is None:
            return None
        try:
            return self._cipher.decrypt(row["path_encrypted"])
        except Exception:
            log.warning("Stored path for activity %s could not be decrypted.", activity_id)
            return None

    # ------------------------------------------------------------------
    def for_date(self, local_date: str) -> list[sqlite3.Row]:
        return list(self._db.connection().execute(
            "SELECT * FROM activities WHERE local_date = ? ORDER BY occurred_at",
            (local_date,),
        ).fetchall())

    def for_range(self, start_date: str, end_date: str) -> list[sqlite3.Row]:
        """Inclusive of both ends — a week report must contain its last day."""
        return list(self._db.connection().execute(
            "SELECT * FROM activities WHERE local_date BETWEEN ? AND ? "
            "ORDER BY occurred_at",
            (start_date, end_date),
        ).fetchall())

    def count_for_date(self, local_date: str) -> int:
        row = self._db.connection().execute(
            "SELECT COUNT(*) AS n FROM activities WHERE local_date = ?", (local_date,)
        ).fetchone()
        return int(row["n"]) if row else 0

    def delete_for_date(self, local_date: str) -> int:
        """'Delete today's data' from the privacy screen."""
        cur = self._db.connection().execute(
            "DELETE FROM activities WHERE local_date = ?", (local_date,)
        )
        return cur.rowcount or 0

    def delete_by_ids(self, ids: list[int]) -> int:
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        cur = self._db.connection().execute(
            f"DELETE FROM activities WHERE id IN ({marks})", ids
        )
        return cur.rowcount or 0

    def delete_older_than(self, cutoff_iso: str) -> int:
        cur = self._db.connection().execute(
            "DELETE FROM activities WHERE occurred_at < ?", (cutoff_iso,)
        )
        return cur.rowcount or 0


# ======================================================================
class AuditRepository:
    """
    Append-only in practice: nothing in this program deletes a single entry.

    Retention trims by AGE, as a whole sweep. There is no "delete this audit
    line" operation, because an audit you can edit one row at a time is not
    one.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, action: str, detail: str | None = None) -> None:
        try:
            self._db.connection().execute(
                "INSERT INTO audit_logs (action, detail) VALUES (?, ?)",
                (action, detail),
            )
        except sqlite3.Error as exc:
            log.error("Could not write audit entry %r: %s", action, exc)

    def recent(self, limit: int = 100) -> list[sqlite3.Row]:
        return list(self._db.connection().execute(
            "SELECT * FROM audit_logs ORDER BY occurred_at DESC LIMIT ?", (limit,)
        ).fetchall())

    def delete_older_than(self, cutoff_iso: str) -> int:
        cur = self._db.connection().execute(
            "DELETE FROM audit_logs WHERE occurred_at < ?", (cutoff_iso,)
        )
        return cur.rowcount or 0


class ReportRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(
        self, kind: str, period_start: str, period_end: str,
        content: str, file_path: str | None = None, fmt: str = "markdown",
    ) -> int | None:
        cur = self._db.connection().execute(
            "INSERT INTO reports (kind, period_start, period_end, format, content, file_path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, period_start, period_end, fmt, content, file_path),
        )
        return int(cur.lastrowid) if cur.lastrowid else None

    def recent(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(self._db.connection().execute(
            "SELECT id, kind, period_start, period_end, file_path, generated_at "
            "FROM reports ORDER BY generated_at DESC LIMIT ?", (limit,)
        ).fetchall())
