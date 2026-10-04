"""
The audit trail for privacy-relevant actions.

WHAT IS AUDITED: that a control was used. Tracking started, paused, resumed;
private mode on and off; a folder added or removed; an integration enabled;
data deleted; a report generated.

WHAT IS NOT AUDITED, EVER: what was being worked on, which file, which folder
path, what the deleted rows contained. An audit line reading "private mode
enabled while editing salary-review.xlsx" would leak precisely the thing
private mode exists to protect, and an audit of deletions that names what was
deleted defeats the deletion.

The constrained vocabulary below is the enforcement. `record()` refuses an
unknown action rather than accepting free text, because free text is how a
caller eventually writes a path into the one table that is meant to be safe to
hand to anyone.
"""

from __future__ import annotations

import logging
import re
from typing import Final

from ..database.db import AuditRepository

log = logging.getLogger(__name__)


AUDITABLE: Final[frozenset[str]] = frozenset({
    "tracker_started",
    "tracker_stopped",
    "tracking_paused",
    "tracking_resumed",
    "private_mode_enabled",
    "private_mode_disabled",
    "folder_added",
    "folder_removed",
    "application_tracking_enabled",
    "application_tracking_disabled",
    "integration_enabled",
    "integration_disabled",
    "ai_enabled",
    "ai_disabled",
    "data_deleted",
    "data_exported",
    "report_generated",
    "retention_sweep",
    "config_changed",
})

# A detail may only be counts and short labels — never a path, never a
# filename, never an address.
_SAFE_DETAIL = re.compile(r"^[A-Za-z0-9 _=,:;.+\-/]{0,200}$")
_PATHLIKE = re.compile(r"[a-zA-Z]:[\\/]|\\\\|/[^\s]+/")


class AuditLog:
    """A thin, opinionated wrapper over the repository."""

    def __init__(self, repository: AuditRepository, strict: bool = True) -> None:
        self._repo = repository
        self._strict = strict

    def record(self, action: str, detail: str | None = None) -> None:
        if action not in AUDITABLE:
            message = f"{action!r} is not an auditable action"
            if self._strict:
                raise ValueError(message)
            log.warning("%s; recording anyway because strict=False", message)

        safe_detail = self._sanitize_detail(detail)
        self._repo.record(action, safe_detail)

    @staticmethod
    def _sanitize_detail(detail: str | None) -> str | None:
        """
        Drop anything that could be a path or does not look like a count.

        Dropping rather than redacting: a redacted detail still says "there was
        something here", and the audit's value comes from being boring enough
        that nobody has to review it.
        """
        if detail is None:
            return None
        text = str(detail).strip()
        if not text:
            return None
        if _PATHLIKE.search(text):
            log.warning("Audit detail looked path-like and was dropped.")
            return None
        if not _SAFE_DETAIL.match(text):
            log.warning("Audit detail contained unexpected characters and was dropped.")
            return None
        return text

    def recent(self, limit: int = 100):
        return self._repo.recent(limit)
