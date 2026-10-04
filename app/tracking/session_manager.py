"""
Work sessions — the sittings a day is actually made of.

THE BRIEF'S EXAMPLE IS THE SPECIFICATION:

    Photoshop activity and file changes in one project folder between
    09:15 and 10:45
                        ↓
    Project: Platinum App · Design · ~1h 30m · 6 files touched

A session is a RUN of activity on one project, unbroken by a gap longer than
the configured timeout. It is the layer between "a file changed" and "this is
what I did", and it is what makes a duration reportable at all.

BUILT AFTER THE FACT, NOT LIVE. A live session manager has to decide "is this
session over yet?" with a timer, and then answer that question again correctly
across a restart, a sleep, a paused tracker and a disconnected drive. Building
from stored activities instead makes the whole thing a pure function of the
data: it is idempotent, it can be rebuilt when the timeout changes, and there
is no in-memory state to lose.

KEYED ON PROJECT, NOT ON PROJECT AND DISCIPLINE. An afternoon spent designing a
key visual and then cutting the video for the same campaign is ONE sitting, and
splitting it in two would report two half-length sessions for a continuous
stretch of work. The session records the discipline it spent most of its events
on; the per-discipline breakdown lives in the report, which is where it is
actually read.

DURATION IS THE OBSERVED SPAN AND NOTHING MORE. A session with a single event
has a span of zero, and that is what gets stored. The tempting fix — "assume
fifteen minutes for an isolated save" — would put invented minutes into a
timesheet, and the brief is explicit that nothing may be invented. A zero-span
session still reports the work; it just does not claim a duration.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..database.db import Database
from ..database.models import Category

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MINUTES = 15


@dataclass
class _Run:
    """One session under construction."""

    project: str
    brand: str | None
    started_at: datetime
    ended_at: datetime
    activity_ids: list[int] = field(default_factory=list)
    filenames: set[str] = field(default_factory=set)
    categories: Counter[str] = field(default_factory=Counter)

    @property
    def dominant_category(self) -> str:
        """
        The discipline this sitting spent most of its events on.

        Ties break towards the FIRST category seen, because the thing you
        started with is usually what the sitting was about — the rest is
        follow-on work.
        """
        if not self.categories:
            return Category.UNCLASSIFIED.value
        return self.categories.most_common(1)[0][0]

    @property
    def duration_minutes(self) -> int:
        return max(0, int((self.ended_at - self.started_at).total_seconds() // 60))


class SessionManager:
    """
    Builds work_sessions from activities, and links the two.

    Nothing else writes that table. Rebuilding a day is the only operation, and
    it is destructive for that day on purpose: a partial rebuild would leave
    sessions from two different timeout settings side by side, and no report
    could say which.
    """

    def __init__(self, db: Database, timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES) -> None:
        self._db = db
        self._timeout = timedelta(minutes=max(1, int(timeout_minutes)))

    # ------------------------------------------------------------------
    @staticmethod
    def _parse(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _runs_for(self, rows: list[sqlite3.Row]) -> list[_Run]:
        """
        Group ordered activities into runs.

        One pass. Rows arrive sorted by time, so only the most recent run for a
        project can still be open — anything earlier has already been closed by
        a later event on that project.
        """
        open_runs: dict[str, _Run] = {}
        closed: list[_Run] = []

        for row in rows:
            occurred = self._parse(row["occurred_at"])
            if occurred is None:
                continue

            project = str(row["project"] or "Unclassified")
            run = open_runs.get(project)

            if run is not None and (occurred - run.ended_at) > self._timeout:
                closed.append(run)
                run = None

            if run is None:
                run = _Run(
                    project=project,
                    brand=row["brand"],
                    started_at=occurred,
                    ended_at=occurred,
                )
                open_runs[project] = run

            run.ended_at = occurred
            if row["id"] is not None:
                run.activity_ids.append(int(row["id"]))
            if row["filename"]:
                run.filenames.add(str(row["filename"]))
            if row["category"]:
                run.categories[str(row["category"])] += 1

        closed.extend(open_runs.values())
        closed.sort(key=lambda r: r.started_at)
        return closed

    # ------------------------------------------------------------------
    def rebuild_day(self, local_date: str) -> int:
        """
        Rebuild every session for one local day. Returns how many were written.

        Idempotent: running it twice produces the same rows. That is what lets
        it be called after every report, after a timeout change, or on a
        schedule, without anyone having to reason about whether it is safe.

        The whole day is one transaction. A half-rebuilt day — sessions deleted
        but not recreated — would read as a day with no work.
        """
        conn = self._db.connection()
        rows = list(conn.execute(
            "SELECT id, occurred_at, project, brand, category, filename "
            "  FROM activities WHERE local_date = ? ORDER BY occurred_at, id",
            (local_date,),
        ).fetchall())

        runs = self._runs_for(rows)

        try:
            conn.execute("BEGIN")

            # Unlink first. The FK is ON DELETE SET NULL, so the delete alone
            # would do it — but being explicit means a future FK change cannot
            # silently leave activities pointing at sessions that no longer
            # exist.
            conn.execute(
                "UPDATE activities SET session_id = NULL WHERE local_date = ?",
                (local_date,),
            )
            conn.execute("DELETE FROM work_sessions WHERE local_date = ?", (local_date,))

            for run in runs:
                cur = conn.execute(
                    "INSERT INTO work_sessions "
                    "  (started_at, ended_at, local_date, project, brand, category,"
                    "   activity_count, file_count, duration_minutes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run.started_at.isoformat(),
                        run.ended_at.isoformat(),
                        local_date,
                        run.project,
                        run.brand,
                        run.dominant_category,
                        len(run.activity_ids),
                        len(run.filenames),
                        run.duration_minutes,
                    ),
                )
                session_id = cur.lastrowid
                if session_id and run.activity_ids:
                    marks = ",".join("?" * len(run.activity_ids))
                    conn.execute(
                        f"UPDATE activities SET session_id = ? WHERE id IN ({marks})",
                        [session_id, *run.activity_ids],
                    )

            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

        log.info("Rebuilt %d session(s) for %s", len(runs), local_date)
        return len(runs)

    def rebuild_range(self, start_date: str, end_date: str) -> int:
        """
        Rebuild every day that has activity in a range, inclusive.

        Day by day rather than in one pass: a session does not span midnight in
        this model — the local date is how every report groups — and rebuilding
        per day keeps each transaction small enough that a failure costs one
        day rather than a month.
        """
        days = [
            str(row["local_date"]) for row in self._db.connection().execute(
                "SELECT DISTINCT local_date FROM activities "
                " WHERE local_date BETWEEN ? AND ? ORDER BY local_date",
                (start_date, end_date),
            ).fetchall()
        ]
        return sum(self.rebuild_day(day) for day in days)

    # ------------------------------------------------------------------
    def for_date(self, local_date: str) -> list[sqlite3.Row]:
        return list(self._db.connection().execute(
            "SELECT * FROM work_sessions WHERE local_date = ? ORDER BY started_at",
            (local_date,),
        ).fetchall())

    def for_range(self, start_date: str, end_date: str) -> list[sqlite3.Row]:
        return list(self._db.connection().execute(
            "SELECT * FROM work_sessions WHERE local_date BETWEEN ? AND ? "
            "ORDER BY started_at",
            (start_date, end_date),
        ).fetchall())

    def stats_for_range(self, start_date: str, end_date: str) -> dict[str, int]:
        """
        The counts a report's statistics block needs, in one query.

        `tracked_minutes` is the sum of the session SPANS. It is deliberately
        not called "hours worked": a span contains whatever happened between
        the first and last file of a sitting, and presenting it as worked time
        produces a number somebody repeats.
        """
        row = self._db.connection().execute(
            "SELECT COUNT(*)                     AS sessions,"
            "       COALESCE(SUM(duration_minutes), 0) AS tracked_minutes,"
            "       COALESCE(SUM(activity_count), 0)   AS activities,"
            "       COUNT(DISTINCT project)      AS projects"
            "  FROM work_sessions WHERE local_date BETWEEN ? AND ?",
            (start_date, end_date),
        ).fetchone()
        if row is None:
            return {"sessions": 0, "tracked_minutes": 0, "activities": 0, "projects": 0}
        return {k: int(row[k]) for k in ("sessions", "tracked_minutes", "activities", "projects")}

    def sessions_by_category(self, start_date: str, end_date: str) -> dict[str, int]:
        """How many sittings each discipline accounted for."""
        return {
            str(row["category"]): int(row["n"]) for row in self._db.connection().execute(
                "SELECT category, COUNT(*) AS n FROM work_sessions "
                " WHERE local_date BETWEEN ? AND ? GROUP BY category ORDER BY n DESC",
                (start_date, end_date),
            ).fetchall()
        }
