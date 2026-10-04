"""
The shapes that move between layers.

`Activity` is what the tracker produces and the database stores. Note what it
does not have: no content, no window title, no URL, no keystroke, no clipboard.
A field that does not exist cannot be populated by a later change that seemed
harmless at the time, which is why the absences are the point.

`full_path` is held in memory only long enough to be classified and encrypted.
It is excluded from `to_row()` in plaintext — the row carries `path_encrypted`
instead — so there is no path through this module that writes a readable path
to disk.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class EventType(str, Enum):
    CREATED = "created"
    MODIFIED = "modified"
    MOVED = "moved"
    DELETED = "deleted"


class Category(str, Enum):
    GRAPHIC_DESIGN = "Graphic Design"
    VIDEO_PRODUCTION = "Video Production"
    AI_CREATIVE = "AI Creative Production"
    DEVELOPMENT = "Development"
    DOCUMENTATION = "Documentation / Reporting"
    PRESENTATION = "Presentation"
    DIGITAL_MARKETING = "Digital Marketing"
    SOCIAL_MEDIA = "Social Media"
    WEBSITE = "Website"
    RESEARCH = "Research"
    MANAGEMENT = "Management"
    UNCLASSIFIED = "Unclassified"


class Activity(BaseModel):
    """One recorded file event, after the whitelist, exclusions and debounce."""

    id: int | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    local_date: str
    event_type: EventType
    source: str = "filesystem"

    # In memory only. Never serialised to a row in the clear — see to_row().
    full_path: str | None = Field(default=None, exclude=True)

    filename: str | None = None
    extension: str | None = None
    family_key: str | None = None
    parent_folder: str | None = None
    root_label: str | None = None

    project: str | None = None
    brand: str | None = None
    category: Category = Category.UNCLASSIFIED

    repeat_count: int = 1
    size_bytes: int | None = None
    session_id: int | None = None

    def to_row(self, path_encrypted: bytes | None) -> dict:
        """
        The database row.

        The encrypted path is passed IN rather than produced here, so this
        model never imports the cipher and can never be tempted to fall back to
        writing the plaintext when encryption fails. A caller that cannot
        encrypt must decide what to do; the default (see repository) is to drop
        the path and keep the derived fields.
        """
        return {
            "occurred_at": self.occurred_at.astimezone(timezone.utc).isoformat(),
            "local_date": self.local_date,
            "event_type": self.event_type.value,
            "source": self.source,
            "path_encrypted": path_encrypted,
            "filename": self.filename,
            "extension": self.extension,
            "family_key": self.family_key,
            "parent_folder": self.parent_folder,
            "root_label": self.root_label,
            "project": self.project,
            "brand": self.brand,
            "category": self.category.value,
            "repeat_count": self.repeat_count,
            "size_bytes": self.size_bytes,
            "session_id": self.session_id,
        }


class WorkSession(BaseModel):
    """A sitting: related activity on one project, unbroken by a long gap."""

    id: int | None = None
    started_at: datetime
    ended_at: datetime | None = None
    local_date: str
    project: str | None = None
    brand: str | None = None
    category: Category = Category.UNCLASSIFIED
    activity_count: int = 0
    file_count: int = 0
    duration_minutes: int | None = None


class WorkItem(BaseModel):
    """
    A deduplicated piece of work, which is what a report talks about.

    `confidence` is carried so a report can be honest. The pipeline guesses the
    project from a folder name; when it guesses badly, the item should say so
    rather than present a guess as a fact.
    """

    project: str = "Unclassified"
    brand: str | None = None
    category: Category = Category.UNCLASSIFIED
    action: str = ""
    file_count: int = 0
    session_count: int = 0
    first_at: datetime | None = None
    last_at: datetime | None = None
    evidence_ids: list[int] = Field(default_factory=list)
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    # Extensions only, never names. The sanitizer passes these through because
    # ".psd" says "design work" and discloses nothing.
    file_types: list[str] = Field(default_factory=list)


class AuditEntry(BaseModel):
    """
    A privacy-relevant event.

    `detail` is constrained by convention to counts and non-sensitive labels.
    The audit exists to prove the controls were used; putting the protected
    content into it would defeat the control it is recording.
    """

    id: int | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action: str
    detail: str | None = None
