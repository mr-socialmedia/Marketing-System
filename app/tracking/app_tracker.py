"""
Application tracking. Opt-in, whitelisted, and deliberately almost blind.

WHY THIS IS OFF BY DEFAULT. Knowing which application was open is a step closer
to watching a person than knowing which file changed. A file event is evidence
of work on a specific deliverable; "Photoshop was running from 09:00 to 17:00"
is evidence about somebody's day. It makes the reports better — the brief's own
session example is "Photoshop activity AND file modifications" — which is
exactly why it has to be the user's decision rather than a default.

=============================================================================
PSUTIL HANDS YOU SURVEILLANCE BY DEFAULT. EXACTLY ONE METHOD IS USED.
=============================================================================

A psutil.Process exposes all of this, and every one of them is refused here:

    cmdline()      `Photoshop.exe "D:\\Work\\Confidential\\salary-review.psd"`
                   — a full path, carried straight past the folder whitelist.
    open_files()   every file the process has open, ACROSS THE WHOLE DISK.
                   This single call would defeat the entire security model.
    cwd()          a path.
    environ()      environment variables, which routinely hold tokens.
    exe()          the install path.
    connections()  what the machine is talking to.

Only `name()` is read: `"Photoshop.exe"`. That is a string from a whitelist the
user wrote, and it is the whole of what this module learns.

`tests/test_security_guarantees.py` asserts that none of the other names appear
in this file. The discipline is not a habit; it is a build rule.

POLLING, NOT HOOKING. A hook into process creation needs privileges this app
deliberately does not ask for, and it is shaped like monitoring software. A
poll every few seconds is enough to time a session to the nearest minute, which
is all any report shows.

NO WINDOW TITLES. psutil does not offer them, and nothing here reaches for
win32gui to get them. A window title is the document name — often the most
sensitive string on the screen.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..database.db import Database
    from ..security.privacy import PrivacyGate

log = logging.getLogger(__name__)

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover - declared in requirements
    PSUTIL_AVAILABLE = False
    psutil = None  # type: ignore[assignment]


DEFAULT_POLL_SECONDS = 20

# A session that ends and restarts within this is treated as continuous.
# Applications restart: Premiere crashes, Photoshop is closed to free memory
# and reopened two minutes later. Recording that as two sittings would turn one
# afternoon into a fragmented list.
RESUME_GRACE_SECONDS = 180


class ApplicationTracker:
    """
    Polls for approved applications and records how long each was running.

    Holds the privacy gate for the same reason the filesystem tracker does: a
    paused or private tracker must learn nothing, and the check happens before
    the process list is even read.
    """

    def __init__(
        self,
        *,
        db: Database,
        gate: PrivacyGate,
        approved: list[str],
        poll_seconds: int = DEFAULT_POLL_SECONDS,
    ) -> None:
        if not PSUTIL_AVAILABLE:
            raise RuntimeError("psutil is not installed; run `pip install -r requirements.txt`")

        # Matched case-insensitively: Windows reports "PHOTOSHOP.EXE" and
        # "Photoshop.exe" depending on how it was started.
        self._approved = {name.strip().lower() for name in approved if name.strip()}
        self._db = db
        self._gate = gate
        self._poll = max(5, int(poll_seconds))

        self._open: dict[str, int] = {}          # process name -> session row id
        self._last_seen: dict[str, datetime] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def _running_approved(self) -> set[str]:
        """
        The approved applications currently running.

        ONLY `name` is requested. process_iter takes an explicit attribute
        list, so this cannot accidentally touch cmdline or open_files — asking
        for the minimum is both the privacy rule and, usefully, the fast path.
        """
        found: set[str] = set()
        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info.get("name") or "").lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # A process that vanished between listing and reading, or one
                # owned by another user. Neither is an error worth logging per
                # poll — on a busy machine that would be constant noise.
                continue
            if name in self._approved:
                found.add(name)
        return found

    # ------------------------------------------------------------------
    def poll_once(self, now: datetime | None = None) -> dict[str, int]:
        """
        One pass. Returns {"opened": n, "closed": n}, for the tests and the UI.

        Separated from the loop so it can be driven directly with an injected
        clock, rather than a test having to sleep through real poll intervals.
        """
        ts = now or datetime.now(timezone.utc)

        # The gate first, before the process list is read at all. A paused or
        # private tracker must not learn which applications are open.
        if not self._gate.allows_collection():
            self._close_all(ts)
            return {"opened": 0, "closed": 0}

        running = self._running_approved()
        opened = closed = 0

        with self._lock:
            for name in running:
                self._last_seen[name] = ts
                if name not in self._open:
                    row_id = self._start_session(name, ts)
                    if row_id is not None:
                        self._open[name] = row_id
                        opened += 1

            for name in list(self._open):
                if name in running:
                    continue
                last = self._last_seen.get(name, ts)
                # Grace period: an application that restarts quickly is one
                # sitting, not two.
                if (ts - last).total_seconds() < RESUME_GRACE_SECONDS:
                    continue
                self._end_session(self._open.pop(name), last)
                closed += 1

        return {"opened": opened, "closed": closed}

    # ------------------------------------------------------------------
    def _start_session(self, process_name: str, at: datetime) -> int | None:
        try:
            cur = self._db.connection().execute(
                "INSERT INTO application_sessions (process_name, started_at, local_date) "
                "VALUES (?, ?, ?)",
                (process_name, at.isoformat(), datetime.now().strftime("%Y-%m-%d")),
            )
            return int(cur.lastrowid) if cur.lastrowid else None
        except Exception:
            # Never raise from the polling thread; a lost application session
            # is a worse report, not a broken tracker.
            log.exception("Could not open an application session.")
            return None

    def _end_session(self, row_id: int, at: datetime) -> None:
        try:
            self._db.connection().execute(
                "UPDATE application_sessions "
                "   SET ended_at = ?,"
                # Computed in SQL from the stored start, so a clock change
                # between open and close cannot produce a negative duration.
                "       duration_minutes = MAX(0, CAST((julianday(?) - julianday(started_at))"
                "                                      * 1440 AS INTEGER))"
                " WHERE id = ? AND ended_at IS NULL",
                (at.isoformat(), at.isoformat(), row_id),
            )
        except Exception:
            log.exception("Could not close an application session.")

    def _close_all(self, at: datetime) -> None:
        """Close every open session — on stop, or when the gate shuts."""
        with self._lock:
            for name, row_id in list(self._open.items()):
                self._end_session(row_id, self._last_seen.get(name, at))
            self._open.clear()

    # ------------------------------------------------------------------
    def start(self) -> None:
        if not self._approved:
            log.warning(
                "Application tracking is on but no applications are approved. "
                "Nothing will be recorded."
            )
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="app-tracker", daemon=True
        )
        self._thread.start()
        log.info("Application tracking: %s", ", ".join(sorted(self._approved)))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("Application poll failed; tracking continues.")
            # wait() rather than sleep(), so stopping is immediate rather than
            # taking up to a full poll interval.
            self._stop.wait(self._poll)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._close_all(datetime.now(timezone.utc))
        log.info("Application tracking stopped.")

    # ------------------------------------------------------------------
    def sessions_for_date(self, local_date: str) -> list:
        return list(self._db.connection().execute(
            "SELECT process_name, started_at, ended_at, duration_minutes "
            "  FROM application_sessions WHERE local_date = ? ORDER BY started_at",
            (local_date,),
        ).fetchall())

    def totals_for_range(self, start_date: str, end_date: str) -> dict[str, int]:
        """Minutes per application over a period, for the report's statistics."""
        return {
            str(row["process_name"]): int(row["minutes"] or 0)
            for row in self._db.connection().execute(
                "SELECT process_name, SUM(duration_minutes) AS minutes"
                "  FROM application_sessions"
                " WHERE local_date BETWEEN ? AND ? AND ended_at IS NOT NULL"
                " GROUP BY process_name ORDER BY minutes DESC",
                (start_date, end_date),
            ).fetchall()
        }
