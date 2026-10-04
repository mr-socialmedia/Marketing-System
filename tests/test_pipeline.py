"""
The behavioural tests: debounce, families, classification, grouping, retention.

These cover the claims the reports rest on. If debounce breaks, the monthly
report says thirty tasks for one afternoon. If the family key breaks, five
logos become five deliverables. Both produce a confident, wrong document, which
is the only output worse than no output.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import Config, ProjectRule, load_config, save_config
from app.database.db import ActivityRepository, AuditRepository, Database
from app.database.models import Activity, Category, EventType
from app.intelligence.classifier import Classifier
from app.intelligence.deduplicator import Deduplicator, merge_across_days
from app.reporting.daily_report import DailyReportGenerator
from app.security.retention import RetentionManager
from app.tracking.filters import Debouncer, PathFilter, family_key


# ======================================================================
# Debounce
# ======================================================================
class TestDebounce:
    def test_repeated_saves_record_once(self) -> None:
        """
        The brief's example: thirty saves of one PSD are one piece of work.

        Time is injected rather than slept through, so this runs instantly and
        tests the window rather than the scheduler.
        """
        d = Debouncer(window_seconds=90)
        path = r"D:\Work\Platinum\logo.psd"

        recorded, count = d.should_record(path, "modified", now=0.0)
        assert recorded is True and count == 1

        for i in range(1, 30):
            recorded, count = d.should_record(path, "modified", now=float(i))
            assert recorded is False, f"save {i} should have been debounced"

        assert count == 30, "every repeat should still be counted as evidence"

    def test_a_new_sitting_after_the_window_records_again(self) -> None:
        d = Debouncer(window_seconds=90)
        path = r"D:\Work\Platinum\logo.psd"

        assert d.should_record(path, "modified", now=0.0)[0] is True
        assert d.should_record(path, "modified", now=200.0)[0] is True

    def test_continuous_work_extends_the_window(self) -> None:
        """
        Steady saving is one sitting, not one event every ninety seconds.

        Each touch pushes the window out, so an hour of continuous work on one
        file produces a single item.
        """
        d = Debouncer(window_seconds=90)
        path = r"D:\Work\Platinum\logo.psd"

        assert d.should_record(path, "modified", now=0.0)[0] is True
        for t in range(60, 3600, 60):
            assert d.should_record(path, "modified", now=float(t))[0] is False

    def test_different_event_types_are_separate_facts(self) -> None:
        """Creating a file and later editing it are two things worth keeping."""
        d = Debouncer(window_seconds=90)
        path = r"D:\Work\Platinum\logo.psd"
        assert d.should_record(path, "created", now=0.0)[0] is True
        assert d.should_record(path, "modified", now=1.0)[0] is True

    def test_case_insensitive_on_windows_paths(self) -> None:
        d = Debouncer(window_seconds=90)
        assert d.should_record(r"D:\Work\Logo.psd", "modified", now=0.0)[0] is True
        assert d.should_record(r"d:\work\logo.psd", "modified", now=1.0)[0] is False


# ======================================================================
# File families
# ======================================================================
class TestFamilyKey:
    @pytest.mark.parametrize("name", [
        "logo-v1.psd", "logo-v2.psd", "logo-v3.psd",
        "logo-final.psd", "logo-final-final.psd", "logo (2).psd",
        "logo copy.psd", "logo_v1.3.psd", "logo-draft.psd",
    ])
    def test_version_noise_collapses_to_one_family(self, name: str) -> None:
        """The brief's five logos, plus the variants people actually produce."""
        assert family_key(name) == "logo"

    def test_genuinely_different_files_stay_apart(self) -> None:
        """
        The failure that matters more than over-splitting.

        Merging the logo with the banner loses a real deliverable from the
        report; the deduplicator is deliberately conservative for this reason.
        """
        assert family_key("logo-v1.psd") != family_key("banner-v1.psd")
        assert family_key("hero-final.psd") != family_key("footer-final.psd")

    def test_a_four_digit_suffix_is_not_a_version(self) -> None:
        """"report-2026" is a year, not revision three."""
        assert family_key("report-2026.docx") == "report-2026"


# ======================================================================
# Filters
# ======================================================================
class TestFilters:
    def test_temporary_files_are_excluded(self, tmp_path) -> None:
        root = tmp_path / "Work"
        root.mkdir()
        f = PathFilter([root.resolve()], [".tmp", ".crdownload"], [])

        assert f.accepts(root / "brief.docx").accepted is True
        assert f.accepts(root / "~$brief.docx").accepted is False
        assert f.accepts(root / "x.tmp").accepted is False
        assert f.accepts(root / "big.zip.crdownload").accepted is False

    def test_the_whole_parent_chain_is_checked(self, tmp_path) -> None:
        """
        A file deep inside node_modules has an innocent immediate parent.

        Checking only that parent lets an entire dependency tree through, which
        on a single `npm install` is tens of thousands of events.
        """
        root = tmp_path / "Work"
        deep = root / "site" / "node_modules" / "pkg" / "dist"
        deep.mkdir(parents=True)

        f = PathFilter([root.resolve()], [], ["node_modules"])
        assert f.accepts(deep / "index.js").accepted is False
        assert f.accepts(root / "site" / "index.js").accepted is True


# ======================================================================
# Classification
# ======================================================================
class TestClassifier:
    def test_extension_decides_the_category(self) -> None:
        c = Classifier([])
        assert c.category_for("a.psd")[0] is Category.GRAPHIC_DESIGN
        assert c.category_for("a.aep")[0] is Category.VIDEO_PRODUCTION
        assert c.category_for("a.tsx")[0] is Category.DEVELOPMENT
        assert c.category_for("a.xlsx")[0] is Category.DOCUMENTATION
        assert c.category_for("a.pptx")[0] is Category.PRESENTATION

    def test_the_most_specific_rule_wins(self) -> None:
        """
        "Platinum Fit" and "Platinum" both match a Platinum Fit path.

        Specificity is decided by rule length, not by the order somebody typed
        them into config.json — an ordering nobody maintains correctly.
        """
        c = Classifier([
            ProjectRule(match="Platinum", project="Platinum", brand="Platinum"),
            ProjectRule(match="Platinum Fit", project="Platinum Fit", brand="Platinum Fit"),
        ])
        project, brand, _, _ = c.project_for(r"D:\Work\Platinum Fit\Campaign\hero.psd")
        assert project == "Platinum Fit"
        assert brand == "Platinum Fit"

    def test_an_unmatched_path_is_unclassified_not_guessed(self) -> None:
        """
        A confident wrong project is a correction in a meeting.

        Unclassified is a prompt to add a rule; inventing one is a report filed
        under the wrong brand.
        """
        c = Classifier([ProjectRule(match="platinum", project="Platinum")])
        project, _, _, confidence = c.project_for(r"D:\Elsewhere\thing.psd")
        assert project == "Unclassified"
        assert confidence == 0.0

    def test_confidence_takes_the_weaker_half(self) -> None:
        """
        An item is only as trustworthy as its least certain part.

        Averaging would let a confident category (.psd) hide a project that was
        never identified, which is the half that actually matters.
        """
        c = Classifier([])
        result = c.classify(r"D:\Nowhere\thing.psd")
        assert result.project == "Unclassified"
        assert result.confidence == 0.0


# ======================================================================
# Deduplication
# ======================================================================
class TestDeduplicator:
    @staticmethod
    def _row(name: str, minute: int, project: str = "Platinum",
             category: str = "Graphic Design", rid: int = 1) -> dict:
        base = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)
        return {
            "id": rid,
            "occurred_at": (base + timedelta(minutes=minute)).isoformat(),
            "project": project,
            "brand": project,
            "category": category,
            "family_key": family_key(name),
            "filename": name,
            "extension": ".psd",
            "event_type": "modified",
            "repeat_count": 1,
        }

    def test_the_briefs_example(self) -> None:
        """
        Five logo files in one sitting become one work item.

        This is the specification from the brief, written as a test.
        """
        rows = [
            self._row("logo-v1.psd", 0, rid=1),
            self._row("logo-v2.psd", 5, rid=2),
            self._row("logo-v3.psd", 12, rid=3),
            self._row("logo-final.psd", 20, rid=4),
            self._row("logo-final-final.psd", 35, rid=5),
        ]
        items = Deduplicator(gap_minutes=90).group(rows)

        assert len(items) == 1, f"expected one item, got {[i.action for i in items]}"
        item = items[0]
        assert item.project == "Platinum"
        assert item.file_count == 5
        assert len(item.evidence_ids) == 5
        assert "logo" in item.action.lower()

    def test_a_long_gap_splits_the_sitting(self) -> None:
        rows = [self._row("logo-v1.psd", 0, rid=1), self._row("logo-v2.psd", 300, rid=2)]
        assert len(Deduplicator(gap_minutes=90).group(rows)) == 2

    def test_different_projects_never_merge(self) -> None:
        """The same filename under two brands is two pieces of work."""
        rows = [
            self._row("logo-v1.psd", 0, project="Platinum", rid=1),
            self._row("logo-v2.psd", 5, project="Miss Platinum", rid=2),
        ]
        items = Deduplicator().group(rows)
        assert len(items) == 2
        assert {i.project for i in items} == {"Platinum", "Miss Platinum"}

    def test_nothing_is_ever_marked_complete(self) -> None:
        """
        No code path emits a completion claim.

        A file stops changing when it is done, when it is abandoned, and when
        the person goes on holiday. Reporting the first is a guess presented as
        a fact.
        """
        items = Deduplicator().group([self._row("logo-final.psd", 0)])
        text = " ".join(i.action.lower() for i in items)
        for word in ["completed", "finished", "delivered", "done"]:
            assert word not in text

    def test_merge_across_days_collapses_a_month(self) -> None:
        rows = []
        for day in range(1, 6):
            rows.append({
                "id": day,
                "occurred_at": datetime(2026, 3, day, 10, 0, tzinfo=timezone.utc).isoformat(),
                "project": "Platinum", "brand": "Platinum",
                "category": "Graphic Design", "family_key": "logo",
                "filename": "logo.psd", "extension": ".psd",
                "event_type": "modified", "repeat_count": 1,
            })
        daily = Deduplicator(gap_minutes=90).group(rows)
        assert len(daily) == 5

        monthly = merge_across_days(daily)
        assert len(monthly) == 1
        assert monthly[0].session_count == 5


# ======================================================================
# Reports
# ======================================================================
class TestDailyReport:
    def test_an_empty_day_says_so(self) -> None:
        out = DailyReportGenerator().generate([], "2026-03-02")
        assert "No tracked activity" in out
        # Not an empty file that looks like a crash.
        assert "2026-03-02" in out

    def test_a_report_groups_by_category(self) -> None:
        rows = [
            TestDeduplicator._row("hero.psd", 0, rid=1),
            {**TestDeduplicator._row("promo.aep", 30, rid=2),
             "category": "Video Production", "extension": ".aep"},
        ]
        out = DailyReportGenerator().generate(rows, "2026-03-02")
        assert "### Graphic Design" in out
        assert "### Video Production" in out
        assert "Platinum" in out

    def test_a_report_contains_no_paths(self) -> None:
        """A report is forwarded. It must carry no filesystem detail."""
        from app.intelligence.sanitizer import Sanitizer

        rows = [TestDeduplicator._row("hero.psd", 0, rid=1)]
        out = DailyReportGenerator().generate(rows, "2026-03-02")
        assert Sanitizer().contains_path(out) is False


# ======================================================================
# Storage and retention
# ======================================================================
class TestStorage:
    def test_an_activity_round_trips(self, tmp_path) -> None:
        db = Database(tmp_path / "t.db")
        repo = ActivityRepository(db, cipher=None)

        repo.add(Activity(
            local_date="2026-03-02", event_type=EventType.MODIFIED,
            filename="hero.psd", extension=".psd", family_key="hero",
            project="Platinum", category=Category.GRAPHIC_DESIGN,
        ))

        rows = repo.for_date("2026-03-02")
        assert len(rows) == 1
        assert rows[0]["project"] == "Platinum"
        db.close()

    def test_the_plaintext_path_is_never_stored(self, tmp_path) -> None:
        """
        With no cipher the path is DROPPED, not written in the clear.

        "Just this once" is how a readable path ends up in a database nobody
        remembers putting it in.
        """
        db = Database(tmp_path / "t.db")
        repo = ActivityRepository(db, cipher=None)
        secret = r"D:\Work\Platinum\Confidential\salary-review.xlsx"

        repo.add(Activity(
            local_date="2026-03-02", event_type=EventType.MODIFIED,
            full_path=secret, filename="salary-review.xlsx",
            project="Platinum", category=Category.DOCUMENTATION,
        ))

        blob = (tmp_path / "t.db").read_bytes()
        assert b"Confidential" not in blob
        assert b"D:\\Work" not in blob
        db.close()

    def test_an_encrypted_path_round_trips(self, tmp_path) -> None:
        from app.security.encryption import FernetCipher

        db = Database(tmp_path / "t.db")
        cipher = FernetCipher(tmp_path / "path.key")
        repo = ActivityRepository(db, cipher)
        secret = r"D:\Work\Platinum\brief.docx"

        repo.add(Activity(
            local_date="2026-03-02", event_type=EventType.MODIFIED,
            full_path=secret, filename="brief.docx", project="Platinum",
        ))

        row_id = repo.for_date("2026-03-02")[0]["id"]
        assert repo.decrypt_path(row_id) == secret

        # The PATH is unreadable in the file. The FILENAME deliberately is not:
        # family grouping needs it, and "brief.docx" discloses nothing on its
        # own. Asserting the filename were absent would be asserting against
        # the design rather than testing it.
        blob = (tmp_path / "t.db").read_bytes()
        assert rb"D:\Work\Platinum" not in blob
        assert b"brief.docx" in blob
        db.close()


class TestRetention:
    def test_old_activity_is_removed_and_recent_is_kept(self, tmp_path) -> None:
        from app.config import RetentionConfig

        db = Database(tmp_path / "t.db")
        activities = ActivityRepository(db, cipher=None)
        audit = AuditRepository(db)

        old = datetime.now(timezone.utc) - timedelta(days=200)
        recent = datetime.now(timezone.utc) - timedelta(days=5)

        for when, day in ((old, "2025-08-15"), (recent, "2026-02-25")):
            activities.add(Activity(
                occurred_at=when, local_date=day, event_type=EventType.MODIFIED,
                filename="x.psd", project="Platinum",
            ))

        manager = RetentionManager(
            db, RetentionConfig(raw_activity_days=180), activities, audit
        )
        removed = manager.run()

        assert removed["activities"] == 1
        assert activities.count_for_date("2026-02-25") == 1
        assert activities.count_for_date("2025-08-15") == 0
        db.close()


# ======================================================================
# Config
# ======================================================================
class TestConfig:
    def test_a_corrupt_config_falls_back_to_safe_defaults(self, tmp_path) -> None:
        """
        Failing CLOSED matters here.

        The alternative is a program that crashes on startup after a bad edit
        and gets "fixed" by somebody loosening the parser.
        """
        bad = tmp_path / "config.json"
        bad.write_text("{ not json at all", encoding="utf-8")

        cfg = load_config(bad)
        assert cfg.tracking.folders == []
        assert cfg.privacy.local_only is True

    def test_config_survives_a_round_trip(self, tmp_path) -> None:
        path = tmp_path / "config.json"
        cfg = Config()
        cfg.tracking.folders = [str(tmp_path / "Work")]
        cfg.project_rules = [ProjectRule(match="platinum", project="Platinum")]
        save_config(cfg, path)

        loaded = load_config(path)
        assert loaded.tracking.folders == cfg.tracking.folders
        assert loaded.project_rules[0].project == "Platinum"
