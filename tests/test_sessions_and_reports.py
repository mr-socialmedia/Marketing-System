"""
Work sessions, and the weekly and monthly reports built on them.

The assertions worth having here are about what a report must NOT say. A
report that is merely incomplete gets ignored; a report that is confident and
wrong gets forwarded, quoted in a review, and remembered.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.database.db import ActivityRepository, Database
from app.database.models import Activity, Category, EventType
from app.intelligence.sanitizer import Sanitizer
from app.reporting.period_report import (
    PeriodReportGenerator,
    month_bounds,
    week_bounds,
)
from app.tracking.session_manager import SessionManager


def _add(repo: ActivityRepository, day: str, hour: int, minute: int = 0, *,
         project: str = "Platinum Fit", category: Category = Category.GRAPHIC_DESIGN,
         filename: str = "logo-v1.psd", family: str = "logo") -> None:
    repo.add(Activity(
        occurred_at=datetime.fromisoformat(f"{day}T{hour:02d}:{minute:02d}:00+00:00"),
        local_date=day,
        event_type=EventType.MODIFIED,
        filename=filename,
        extension="." + filename.rsplit(".", 1)[-1],
        family_key=family,
        project=project,
        brand=project,
        category=category,
    ))


# ======================================================================
class TestSessionManager:
    def test_a_gap_longer_than_the_timeout_starts_a_new_sitting(self, tmp_path) -> None:
        db = Database(tmp_path / "t.db")
        repo = ActivityRepository(db, None)

        _add(repo, "2026-09-09", 9, 0)
        _add(repo, "2026-09-09", 9, 10)    # within 15 minutes — same sitting
        _add(repo, "2026-09-09", 14, 0)    # hours later — a new one

        manager = SessionManager(db, timeout_minutes=15)
        assert manager.rebuild_day("2026-09-09") == 2

        sessions = manager.for_date("2026-09-09")
        assert [int(s["activity_count"]) for s in sessions] == [2, 1]
        assert int(sessions[0]["duration_minutes"]) == 10
        db.close()

    def test_two_projects_in_one_afternoon_are_two_sittings(self, tmp_path) -> None:
        db = Database(tmp_path / "t.db")
        repo = ActivityRepository(db, None)

        _add(repo, "2026-09-09", 9, 0, project="Platinum Fit")
        _add(repo, "2026-09-09", 9, 5, project="Miss Platinum")

        manager = SessionManager(db, timeout_minutes=15)
        assert manager.rebuild_day("2026-09-09") == 2
        assert {s["project"] for s in manager.for_date("2026-09-09")} == {
            "Platinum Fit", "Miss Platinum"
        }
        db.close()

    def test_two_disciplines_on_one_project_stay_ONE_sitting(self, tmp_path) -> None:
        """
        Designing a key visual then cutting the video for the same campaign is
        one continuous stretch of work. Splitting it by discipline would report
        two half-length sittings for an unbroken afternoon.

        The session records the discipline it spent most of its events on; the
        per-discipline breakdown belongs in the report.
        """
        db = Database(tmp_path / "t.db")
        repo = ActivityRepository(db, None)

        _add(repo, "2026-09-09", 9, 0, category=Category.GRAPHIC_DESIGN)
        _add(repo, "2026-09-09", 9, 5, category=Category.GRAPHIC_DESIGN)
        _add(repo, "2026-09-09", 9, 9, category=Category.VIDEO_PRODUCTION,
             filename="promo.aep", family="promo")

        manager = SessionManager(db, timeout_minutes=15)
        assert manager.rebuild_day("2026-09-09") == 1
        session = manager.for_date("2026-09-09")[0]
        assert session["category"] == Category.GRAPHIC_DESIGN.value
        assert int(session["file_count"]) == 2
        db.close()

    def test_rebuilding_is_idempotent(self, tmp_path) -> None:
        """
        It runs after every report and can run on a schedule, so running it
        twice must produce the same rows rather than doubling them.
        """
        db = Database(tmp_path / "t.db")
        repo = ActivityRepository(db, None)
        _add(repo, "2026-09-09", 9, 0)
        _add(repo, "2026-09-09", 9, 5)

        manager = SessionManager(db, timeout_minutes=15)
        first = manager.rebuild_day("2026-09-09")
        second = manager.rebuild_day("2026-09-09")

        assert first == second == 1
        assert len(manager.for_date("2026-09-09")) == 1
        db.close()

    def test_activities_are_linked_to_their_session(self, tmp_path) -> None:
        db = Database(tmp_path / "t.db")
        repo = ActivityRepository(db, None)
        _add(repo, "2026-09-09", 9, 0)
        _add(repo, "2026-09-09", 9, 5)

        SessionManager(db, timeout_minutes=15).rebuild_day("2026-09-09")

        unlinked = db.connection().execute(
            "SELECT COUNT(*) FROM activities WHERE session_id IS NULL"
        ).fetchone()[0]
        assert unlinked == 0
        db.close()

    def test_a_single_event_session_claims_no_duration(self, tmp_path) -> None:
        """
        A lone file save has an observed span of zero, and zero is what gets
        stored. The tempting fix — "assume fifteen minutes" — would put
        invented minutes into a timesheet.
        """
        db = Database(tmp_path / "t.db")
        _add(ActivityRepository(db, None), "2026-09-09", 9, 0)

        manager = SessionManager(db, timeout_minutes=15)
        manager.rebuild_day("2026-09-09")
        assert int(manager.for_date("2026-09-09")[0]["duration_minutes"]) == 0
        db.close()


# ======================================================================
class TestPeriodBounds:
    def test_the_week_starts_on_sunday(self) -> None:
        """
        Kuwait works Sunday to Thursday. A Monday-start week splits the working
        week across two reports and puts the weekend in the middle of one — and
        the Thursday review the brief asks for only makes sense on a week that
        begins on Sunday.
        """
        # 2026-09-09 is a Wednesday.
        start, end = week_bounds("2026-09-09")
        assert date.fromisoformat(start).strftime("%A") == "Sunday"
        assert date.fromisoformat(end).strftime("%A") == "Saturday"
        assert start == "2026-09-06"

    def test_a_sunday_is_its_own_week_start(self) -> None:
        start, _ = week_bounds("2026-09-06")
        assert start == "2026-09-06"

    def test_month_bounds_cover_the_whole_month(self) -> None:
        assert month_bounds("2026-09-15") == ("2026-09-01", "2026-09-30")
        assert month_bounds("2026-02-10") == ("2026-02-01", "2026-02-28")
        # A leap February, which off-by-one month arithmetic gets wrong.
        assert month_bounds("2028-02-10") == ("2028-02-01", "2028-02-29")
        assert month_bounds("2026-12-31") == ("2026-12-01", "2026-12-31")


# ======================================================================
class TestMonthlyReport:
    @staticmethod
    def _rows(repo: ActivityRepository) -> list:
        for day in (1, 2, 3, 15, 16):
            _add(repo, f"2026-09-{day:02d}", 9, 0,
                 filename="spring-campaign-v2.psd", family="spring-campaign")
        _add(repo, "2026-09-04", 11, 0, category=Category.VIDEO_PRODUCTION,
             filename="promo-final.aep", family="promo")
        return repo.for_range("2026-09-01", "2026-09-30")

    def test_a_monthly_report_never_contains_a_filename(self, tmp_path) -> None:
        """
        THE REGRESSION. The first version reused the deduplicator's sentence,
        which is built from the file family, so the output read
        "Produced spring for Miss Platinum" — a filename stem presented as a
        deliverable, which is the raw log the brief rules out, and a filename
        leaked into a document that gets forwarded.
        """
        db = Database(tmp_path / "t.db")
        rows = self._rows(ActivityRepository(db, None))

        report = PeriodReportGenerator().monthly(rows, "2026-09-01", "2026-09-30")

        for leak in ["spring-campaign", "promo-final", ".psd", ".aep", "-v2"]:
            assert leak not in report, f"{leak!r} reached the monthly report"
        # And it still says what happened.
        assert "design assets" in report
        assert "video content" in report
        assert "Platinum Fit" in report
        db.close()

    def test_a_monthly_report_carries_no_paths(self, tmp_path) -> None:
        db = Database(tmp_path / "t.db")
        rows = self._rows(ActivityRepository(db, None))
        report = PeriodReportGenerator().monthly(rows, "2026-09-01", "2026-09-30")
        assert Sanitizer().contains_path(report) is False
        db.close()

    def test_repetition_across_days_collapses_to_one_line(self, tmp_path) -> None:
        """Five days of the same work is one line, not five."""
        db = Database(tmp_path / "t.db")
        rows = self._rows(ActivityRepository(db, None))

        report = PeriodReportGenerator().monthly(rows, "2026-09-01", "2026-09-30")
        design_lines = [
            line for line in report.splitlines()
            if line.startswith("- ") and "design assets" in line
        ]
        assert len(design_lines) == 1, design_lines
        db.close()

    def test_an_empty_month_says_so(self) -> None:
        report = PeriodReportGenerator().monthly([], "2026-09-01", "2026-09-30")
        assert "No tracked activity" in report
        assert "September 2026" in report

    def test_no_unverifiable_adjectives(self, tmp_path) -> None:
        """
        "successfully", "comprehensive", "significant" cannot be checked
        against anything recorded, and a report full of them is one a reader
        learns to discount.
        """
        db = Database(tmp_path / "t.db")
        rows = self._rows(ActivityRepository(db, None))
        report = PeriodReportGenerator().monthly(rows, "2026-09-01", "2026-09-30").lower()

        for word in ["successfully", "comprehensive", "significant", "extensive",
                     "strategic", "seamless", "world-class"]:
            assert word not in report, f"unverifiable adjective {word!r} in the report"
        db.close()


# ======================================================================
class TestWeeklyReport:
    def test_nothing_is_ever_reported_as_completed(self, tmp_path) -> None:
        """
        A file stops changing when the work is done, when it is abandoned, and
        when somebody goes on leave. Those are indistinguishable from the
        outside, so `completed` stays at zero until an evidence source exists.

        This is the single most damaging thing the weekly report could get
        wrong — it is read by somebody deciding what to chase.
        """
        db = Database(tmp_path / "t.db")
        repo = ActivityRepository(db, None)
        _add(repo, "2026-09-09", 9, 0)
        rows = repo.for_range("2026-09-06", "2026-09-12")

        report = PeriodReportGenerator().weekly(rows, "2026-09-06", "2026-09-12")
        assert "Completed: **0**" in report
        assert "never inferred" in report
        db.close()

    def test_it_counts_projects_and_items(self, tmp_path) -> None:
        db = Database(tmp_path / "t.db")
        repo = ActivityRepository(db, None)
        _add(repo, "2026-09-09", 9, 0, project="Platinum Fit")
        _add(repo, "2026-09-10", 9, 0, project="Miss Platinum",
             filename="lookbook.ai", family="lookbook")
        rows = repo.for_range("2026-09-06", "2026-09-12")

        report = PeriodReportGenerator().weekly(rows, "2026-09-06", "2026-09-12")
        assert "Projects: **2**" in report
        assert "Platinum Fit: 1" in report
        db.close()

    def test_an_empty_week_says_so(self) -> None:
        report = PeriodReportGenerator().weekly([], "2026-09-06", "2026-09-12")
        assert "No tracked activity this week" in report


# ======================================================================
class TestApplicationTracker:
    """
    Driven with an injected process list rather than real applications.

    A test that launches Notepad and waits for it tests the operating system's
    shutdown timing as much as this code — the first live run of this feature
    reported a session as still open purely because Notepad had not finished
    exiting after one second.
    """

    @staticmethod
    def _tracker(tmp_path, running: list[str], gate=None):
        from app.database.db import Database
        from app.security.privacy import PrivacyGate
        from app.tracking.app_tracker import ApplicationTracker

        db = Database(tmp_path / "a.db")
        tracker = ApplicationTracker(
            db=db, gate=gate or PrivacyGate(), approved=["photoshop.exe", "afterfx.exe"]
        )
        # Replace the one method that touches the operating system.
        tracker._running_approved = lambda: set(running)  # type: ignore[method-assign]
        return db, tracker

    def test_a_session_opens_and_closes(self, tmp_path) -> None:
        running = ["photoshop.exe"]
        db, tracker = self._tracker(tmp_path, running)
        t0 = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)

        assert tracker.poll_once(t0)["opened"] == 1

        running.clear()
        # Past the resume grace period.
        assert tracker.poll_once(t0.replace(hour=10))["closed"] == 1

        row = db.connection().execute(
            "SELECT process_name, ended_at, duration_minutes FROM application_sessions"
        ).fetchone()
        assert row["process_name"] == "photoshop.exe"
        assert row["ended_at"] is not None
        db.close()

    def test_a_quick_restart_stays_one_sitting(self, tmp_path) -> None:
        """
        Applications restart. Premiere crashes; Photoshop is closed to free
        memory and reopened two minutes later. Recording that as two sittings
        turns one afternoon into a fragmented list.
        """
        running = ["photoshop.exe"]
        db, tracker = self._tracker(tmp_path, running)
        t0 = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)

        tracker.poll_once(t0)
        running.clear()
        # Inside the grace period — not closed.
        assert tracker.poll_once(t0 + timedelta(seconds=60))["closed"] == 0
        running.append("photoshop.exe")
        assert tracker.poll_once(t0 + timedelta(seconds=120))["opened"] == 0

        count = db.connection().execute(
            "SELECT COUNT(*) FROM application_sessions"
        ).fetchone()[0]
        assert count == 1
        db.close()

    def test_private_mode_closes_sessions_and_records_nothing(self, tmp_path) -> None:
        from app.security.privacy import PrivacyGate

        gate = PrivacyGate()
        running = ["photoshop.exe"]
        db, tracker = self._tracker(tmp_path, running, gate=gate)
        t0 = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)

        tracker.poll_once(t0)
        gate.enable_private_mode()

        # Nothing opens while private, and the open session is closed rather
        # than left running across a gap the user asked not to be recorded.
        assert tracker.poll_once(t0 + timedelta(minutes=5)) == {"opened": 0, "closed": 0}
        open_rows = db.connection().execute(
            "SELECT COUNT(*) FROM application_sessions WHERE ended_at IS NULL"
        ).fetchone()[0]
        assert open_rows == 0
        db.close()

    def test_an_unapproved_application_is_never_recorded(self, tmp_path) -> None:
        """The whitelist, again: approving Photoshop is not approving Chrome."""
        from app.database.db import Database
        from app.security.privacy import PrivacyGate
        from app.tracking.app_tracker import ApplicationTracker

        db = Database(tmp_path / "a.db")
        tracker = ApplicationTracker(
            db=db, gate=PrivacyGate(), approved=["photoshop.exe"]
        )
        tracker._running_approved = lambda: set()  # type: ignore[method-assign]

        tracker.poll_once(datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc))
        assert db.connection().execute(
            "SELECT COUNT(*) FROM application_sessions"
        ).fetchone()[0] == 0
        db.close()
