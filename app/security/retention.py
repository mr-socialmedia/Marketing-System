"""
Deleting old data, on a schedule, without being asked.

RETENTION IS A SECURITY CONTROL, NOT HOUSEKEEPING. Data that no longer exists
cannot be stolen, subpoenaed, or read by whoever gets the laptop next. The
default — 180 days of raw activity — is chosen so a year-end review is still
possible while a two-year-old afternoon is simply gone.

REPORTS ARE EXEMPT BY DEFAULT. A raw event is scaffolding the program created;
a report is something the person wrote and may need years later. Deleting the
first on a timer is good hygiene, deleting the second is destroying their work,
so `reports_days = 0` (keep forever) is the default and has to be changed
deliberately.

THE SWEEP IS AUDITED BY COUNT. "deleted 4,120 activities older than 180 days"
proves the control ran without recording anything about what was deleted.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..config import RetentionConfig
from ..database.db import ActivityRepository, AuditRepository, Database

log = logging.getLogger(__name__)


class RetentionManager:
    def __init__(
        self,
        db: Database,
        config: RetentionConfig,
        activities: ActivityRepository,
        audit: AuditRepository,
    ) -> None:
        self._db = db
        self._config = config
        self._activities = activities
        self._audit = audit

    # ------------------------------------------------------------------
    @staticmethod
    def _cutoff(days: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def run(self) -> dict[str, int]:
        """
        One sweep. Returns what was removed, for the UI and the audit.

        Each category is its own transaction. A failure deleting reports must
        not roll back the activity deletion that already succeeded — the
        retention promise is per-category, and a half-applied sweep that is
        then retried is worse than two independent ones.
        """
        removed = {"activities": 0, "audit_logs": 0, "reports": 0}

        try:
            with self._db.transaction():
                removed["activities"] = self._activities.delete_older_than(
                    self._cutoff(self._config.raw_activity_days)
                )
        except Exception:
            log.exception("Retention sweep failed for activities.")

        try:
            with self._db.transaction():
                removed["audit_logs"] = self._audit.delete_older_than(
                    self._cutoff(self._config.audit_log_days)
                )
        except Exception:
            log.exception("Retention sweep failed for audit logs.")

        if self._config.reports_days > 0:
            try:
                with self._db.transaction():
                    cur = self._db.connection().execute(
                        "DELETE FROM reports WHERE generated_at < ?",
                        (self._cutoff(self._config.reports_days),),
                    )
                    removed["reports"] = cur.rowcount or 0
            except Exception:
                log.exception("Retention sweep failed for reports.")

        total = sum(removed.values())
        if total:
            # A count, never a description of what was in the rows.
            self._audit.record(
                "retention_sweep",
                f"activities={removed['activities']} "
                f"audit={removed['audit_logs']} reports={removed['reports']}",
            )
            log.info("Retention removed %d row(s): %s", total, removed)

        # Space is only returned to the filesystem on VACUUM. Without it a
        # database that held a year of events stays that size forever, which
        # makes "we deleted it" look untrue to anyone checking the file.
        if removed["activities"] > 1000:
            try:
                self._db.connection().execute("VACUUM")
                log.info("Database vacuumed after a large retention sweep.")
            except Exception as exc:
                log.debug("VACUUM skipped: %s", exc)

        return removed

    def preview(self) -> dict[str, int]:
        """What a sweep WOULD remove. For the settings screen, before saving."""
        conn = self._db.connection()
        counts = {}
        counts["activities"] = int(conn.execute(
            "SELECT COUNT(*) FROM activities WHERE occurred_at < ?",
            (self._cutoff(self._config.raw_activity_days),),
        ).fetchone()[0])
        counts["audit_logs"] = int(conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE occurred_at < ?",
            (self._cutoff(self._config.audit_log_days),),
        ).fetchone()[0])
        counts["reports"] = 0
        if self._config.reports_days > 0:
            counts["reports"] = int(conn.execute(
                "SELECT COUNT(*) FROM reports WHERE generated_at < ?",
                (self._cutoff(self._config.reports_days),),
            ).fetchone()[0])
        return counts
