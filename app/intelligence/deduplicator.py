"""
Turn a list of file events into a list of things a person did.

THE EXAMPLE FROM THE BRIEF IS THE SPECIFICATION:

    logo-v1.psd modified
    logo-v2.psd modified
    logo-v3.psd modified
    logo-final.psd modified
    logo-final-final.psd modified
                    ↓
    "Worked on logo development and creative revisions."

Five rows, one afternoon, one sentence. Getting this wrong in either direction
is costly: too little merging produces a report that reads like a disk audit;
too much merging loses a real second deliverable.

NO AI HERE, DELIBERATELY. Grouping is decided by project, file family, category
and time proximity — all of which are facts already in the row. An AI asked to
group these would sometimes be cleverer and sometimes merge the Platinum logo
with the Miss Platinum logo because the names rhyme. Determinism is worth more
than cleverness for the step that decides what the report is ABOUT; the AI's
job starts afterwards, at wording.

NOTHING HERE DECIDES THAT WORK IS FINISHED. There is no "completed" output,
because nothing observable in a file event means completion. A report that
claims something was delivered because a file stopped changing is a report that
will be wrong in front of a director.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..database.models import Category, WorkItem

log = logging.getLogger(__name__)


# Two touches of the same family further apart than this are two sittings.
DEFAULT_GAP_MINUTES = 90


@dataclass
class _Group:
    project: str
    brand: str | None
    category: Category
    family: str
    first_at: datetime
    last_at: datetime
    filenames: set[str] = field(default_factory=set)
    extensions: set[str] = field(default_factory=set)
    activity_ids: list[int] = field(default_factory=list)
    event_types: set[str] = field(default_factory=set)
    confidence_sum: float = 0.0
    confidence_n: int = 0

    @property
    def confidence(self) -> float:
        return round(self.confidence_sum / self.confidence_n, 2) if self.confidence_n else 0.0


class Deduplicator:
    """
    Group rows into work items.

    Rows are whatever `ActivityRepository.for_range` returns — sqlite3.Row or
    dicts. Accessed through `_get` so a test can pass plain dicts without a
    database.
    """

    def __init__(self, gap_minutes: int = DEFAULT_GAP_MINUTES) -> None:
        self._gap = timedelta(minutes=max(1, int(gap_minutes)))

    # ------------------------------------------------------------------
    @staticmethod
    def _get(row: Any, key: str, default: Any = None) -> Any:
        try:
            value = row[key]
        except (KeyError, IndexError, TypeError):
            return getattr(row, key, default)
        return default if value is None else value

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    # ------------------------------------------------------------------
    def group(self, rows: list[Any]) -> list[WorkItem]:
        """
        Rows → work items, newest activity last.

        The key is (project, category, family). Project and category keep
        different deliverables apart; family is what collapses the five logos.
        Time then splits a key that recurs weeks later into separate items,
        because "worked on the logo" in March and again in June is two pieces
        of work, not one long one.
        """
        buckets: dict[tuple[str, str, str], list[_Group]] = defaultdict(list)

        ordered = sorted(rows, key=lambda r: str(self._get(r, "occurred_at", "")))

        for row in ordered:
            occurred = self._parse_time(self._get(row, "occurred_at"))
            if occurred is None:
                continue

            project = str(self._get(row, "project") or "Unclassified")
            category_raw = str(self._get(row, "category") or Category.UNCLASSIFIED.value)
            try:
                category = Category(category_raw)
            except ValueError:
                category = Category.UNCLASSIFIED

            family = str(self._get(row, "family_key") or self._get(row, "filename") or "")
            key = (project, category.value, family)

            existing = buckets[key]
            # Only the most recent group for this key can still be open; the
            # rows arrive in time order, so earlier ones are already closed.
            if existing and (occurred - existing[-1].last_at) <= self._gap:
                group = existing[-1]
                group.last_at = occurred
            else:
                group = _Group(
                    project=project,
                    brand=self._get(row, "brand"),
                    category=category,
                    family=family,
                    first_at=occurred,
                    last_at=occurred,
                )
                existing.append(group)

            filename = self._get(row, "filename")
            if filename:
                group.filenames.add(str(filename))
            ext = self._get(row, "extension")
            if ext:
                group.extensions.add(str(ext).lower())
            activity_id = self._get(row, "id")
            if isinstance(activity_id, int):
                group.activity_ids.append(activity_id)
            event_type = self._get(row, "event_type")
            if event_type:
                group.event_types.add(str(event_type))

            # repeat_count is the debouncer's evidence of sustained work. An
            # item touched 40 times is more certain than one touched once.
            repeats = int(self._get(row, "repeat_count", 1) or 1)
            group.confidence_sum += min(1.0, 0.4 + (repeats / 25.0))
            group.confidence_n += 1

        items: list[WorkItem] = []
        for groups in buckets.values():
            for group in groups:
                items.append(self._to_item(group))

        items.sort(key=lambda i: i.last_at or datetime.min)
        return items

    # ------------------------------------------------------------------
    def _to_item(self, group: _Group) -> WorkItem:
        return WorkItem(
            project=group.project,
            brand=group.brand,
            category=group.category,
            action=self._describe(group),
            file_count=len(group.filenames),
            session_count=1,
            first_at=group.first_at,
            last_at=group.last_at,
            evidence_ids=group.activity_ids,
            confidence=group.confidence,
            file_types=sorted(group.extensions),
        )


    @staticmethod
    def _describe(group: _Group) -> str:
        """
        A plain sentence about what happened, from facts only.

        Written here rather than by an AI so that a report is possible with the
        AI layer switched off — which is the default. The wording is dull on
        purpose: "Worked on" claims activity, which is observed, rather than
        "Completed", which is not.
        """
        name = group.family or "files"
        n = len(group.filenames)

        verb = {
            Category.GRAPHIC_DESIGN: "Designed and revised",
            Category.VIDEO_PRODUCTION: "Edited and exported",
            Category.DEVELOPMENT: "Developed",
            Category.DOCUMENTATION: "Prepared and updated",
            Category.PRESENTATION: "Built and revised",
            Category.WEBSITE: "Updated",
            Category.SOCIAL_MEDIA: "Produced",
            Category.AI_CREATIVE: "Generated and refined",
        }.get(group.category, "Worked on")

        if n > 1:
            return f"{verb} {name} ({n} files, {len(group.activity_ids)} revisions)"
        if len(group.activity_ids) > 1:
            return f"{verb} {name} ({len(group.activity_ids)} revisions)"
        return f"{verb} {name}"


def merge_across_days(items: list[WorkItem]) -> list[WorkItem]:
    """
    Collapse the same work appearing on several days into one monthly item.

    For a monthly report only. A daily report should say what happened that
    day; a monthly one saying "worked on the logo" nineteen times is the raw
    log the brief explicitly rules out.
    """
    merged: dict[tuple[str, str, str], WorkItem] = {}

    for item in items:
        key = (item.project, item.category.value, item.action.split("(")[0].strip())
        existing = merged.get(key)
        if existing is None:
            merged[key] = item.model_copy(deep=True)
            continue

        existing.file_count = max(existing.file_count, item.file_count)
        existing.session_count += item.session_count
        existing.evidence_ids.extend(item.evidence_ids)
        if item.first_at and (existing.first_at is None or item.first_at < existing.first_at):
            existing.first_at = item.first_at
        if item.last_at and (existing.last_at is None or item.last_at > existing.last_at):
            existing.last_at = item.last_at
        # The higher of the two: work seen across several days is better
        # evidenced than work seen once.
        existing.confidence = max(existing.confidence, item.confidence)

    out = list(merged.values())
    out.sort(key=lambda i: (i.project, i.category.value))
    return out
