"""
Weekly and monthly reports — the output the whole pipeline exists for.

A MONTHLY REPORT IS NOT A RAW ACTIVITY LOG. That line is from the brief and it
is the hardest requirement in this file, because the raw log is what the data
naturally looks like and turning it into a report means throwing most of it
away. Nineteen days of logo revisions is one line.

NOTHING HERE INVENTS AN ACHIEVEMENT. Every line traces to work items, which
trace to recorded events. The vocabulary is deliberately dull — "Produced",
"Developed and revised", "Coordinated" — because the verifiable verb is the
whole point. There is no code path that emits "successfully", "comprehensive"
or "delivered".

NOTHING HERE CLAIMS COMPLETION. A file stops changing when it is done, when it
is abandoned, and when the person goes on holiday; those are indistinguishable
from the outside. The weekly report has a status breakdown, and `completed` is
only ever reached through explicit evidence that this phase does not yet
collect — so it reports zero, honestly, rather than guessing.

NO AI IS INVOLVED. The default configuration has AI switched off, so this has
to produce something sendable from the database alone. When the AI layer
arrives it improves the WORDING of what this already says; it does not supply
the content, and the report must still exist without it.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..database.models import Category, WorkItem
from ..intelligence.deduplicator import Deduplicator, merge_across_days

log = logging.getLogger(__name__)


# Disciplines collapsed into the sections a director reads. Several internal
# categories map to one heading: nobody outside this department distinguishes
# "Social Media" from "Digital Marketing" on a monthly summary.
SECTIONS: list[tuple[str, list[Category]]] = [
    ("Creative & Design", [Category.GRAPHIC_DESIGN, Category.AI_CREATIVE]),
    ("Video Production", [Category.VIDEO_PRODUCTION]),
    ("Digital & Social", [Category.DIGITAL_MARKETING, Category.SOCIAL_MEDIA]),
    ("Website & Development", [Category.WEBSITE, Category.DEVELOPMENT]),
    ("Documentation & Reporting", [Category.DOCUMENTATION, Category.PRESENTATION]),
    ("Research", [Category.RESEARCH]),
    ("Coordination", [Category.MANAGEMENT]),
]

# Below this an item is a guess, and a guess does not get a confident sentence.
# It is counted in a note instead, which is what prompts a mapping rule.
LOW_CONFIDENCE = 0.4


def _fmt_minutes(minutes: int) -> str:
    """Minutes as `3h 20m`. One formatter, so no two sections disagree."""
    h, m = divmod(max(0, int(minutes)), 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


class PeriodReportGenerator:
    """Builds weekly and monthly reports from activities and sessions."""

    def __init__(self, deduplicator: Deduplicator | None = None) -> None:
        # A 90-minute gap for within-day grouping; merge_across_days then
        # collapses the same work appearing on several days.
        self._dedup = deduplicator or Deduplicator(gap_minutes=90)

    # ------------------------------------------------------------------
    def _items(self, rows: list[Any]) -> list[WorkItem]:
        return merge_across_days(self._dedup.group(rows))

    @staticmethod
    def _by_section(items: list[WorkItem]) -> dict[str, list[WorkItem]]:
        lookup: dict[Category, str] = {}
        for heading, categories in SECTIONS:
            for category in categories:
                lookup[category] = heading

        grouped: dict[str, list[WorkItem]] = defaultdict(list)
        for item in items:
            heading = lookup.get(item.category)
            if heading is None:
                continue  # Unclassified is counted in a note, never a section.
            grouped[heading].append(item)
        return grouped

    # ------------------------------------------------------------------
    def monthly(
        self,
        rows: list[Any],
        period_start: str,
        period_end: str,
        session_stats: dict[str, int] | None = None,
        sessions_by_category: dict[str, int] | None = None,
    ) -> str:
        label = self._month_label(period_start)

        if not rows:
            return (
                f"# MONTHLY WORK REPORT — {label}\n\n"
                "No tracked activity for this period.\n"
            )

        items = self._items(rows)
        confident = [i for i in items if i.confidence >= LOW_CONFIDENCE]
        uncertain = [i for i in items if i.confidence < LOW_CONFIDENCE]
        sections = self._by_section(confident)

        lines: list[str] = [f"# MONTHLY WORK REPORT — {label}", ""]

        for heading, _ in SECTIONS:
            group = sections.get(heading)
            if not group:
                continue  # An empty heading says the month was thin; omitting
                          # it says nothing, which is correct.
            lines.append(f"## {heading}")
            lines.append("")
            for line in self._monthly_lines(group):
                lines.append(f"- {line}")
            lines.append("")

        lines += self._statistics(items, rows, session_stats, sessions_by_category)

        if uncertain:
            lines += [
                "",
                f"_{len(uncertain)} item(s) could not be matched to a project with "
                "confidence and are not listed above. Add a mapping rule in "
                "Settings so they appear under the right brand._",
            ]

        lines += [
            "",
            "---",
            "",
            "_Generated locally by Mohamed Work Tracker from recorded file "
            "activity. Every line traces to recorded events; nothing is "
            "inferred as complete._",
            "",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def weekly(
        self,
        rows: list[Any],
        period_start: str,
        period_end: str,
        session_stats: dict[str, int] | None = None,
    ) -> str:
        if not rows:
            return (
                f"# Weekly Review — {period_start} to {period_end}\n\n"
                "No tracked activity this week.\n"
            )

        items = self._items(rows)
        by_project: dict[str, int] = defaultdict(int)
        for item in items:
            by_project[item.project] += 1

        # STATUS IS HONEST ABOUT WHAT IT CANNOT KNOW.
        #
        # Nothing observable in a file event means "finished", so `completed`
        # stays at zero until an evidence source exists. Reporting a guess here
        # would be the single most damaging thing this file could do — a weekly
        # review is read by somebody deciding what to chase.
        needs_follow_up = [i for i in items if i.confidence < LOW_CONFIDENCE]
        in_progress = [i for i in items if i.confidence >= LOW_CONFIDENCE]

        lines: list[str] = [
            f"# Weekly Review — {period_start} to {period_end}",
            "",
            "## This Week",
            "",
            f"Detected work items: **{len(items)}**",
            f"Projects: **{len(by_project)}**",
            "",
        ]

        for project, count in sorted(by_project.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {project}: {count}")
        lines.append("")

        if session_stats:
            lines += [
                f"Sittings: {session_stats.get('sessions', 0)}"
                f" · tracked span {_fmt_minutes(session_stats.get('tracked_minutes', 0))}",
                "",
            ]

        lines += [
            "## Status",
            "",
            f"- In progress: **{len(in_progress)}**",
            f"- Needs follow-up: **{len(needs_follow_up)}**",
            "- Completed: **0**",
            "",
            "_Completion is never inferred from file activity. A file stops "
            "changing when the work is done, when it is abandoned, and when "
            "somebody goes on leave — so nothing here is marked complete "
            "without evidence this phase does not yet collect._",
            "",
        ]

        sections = self._by_section(in_progress)
        if sections:
            lines += ["## Work", ""]
            for heading, _ in SECTIONS:
                group = sections.get(heading)
                if not group:
                    continue
                lines.append(f"### {heading}")
                lines.append("")
                for item in self._ordered(group):
                    lines.append(f"- {self._sentence(item)}")
                lines.append("")

        lines += ["---", "", "_Generated locally. Raw activity stays on this device._", ""]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    @staticmethod
    def _monthly_lines(items: list[WorkItem]) -> list[str]:
        """
        One line per project per discipline, described by DISCIPLINE.

        THE FILENAME MUST NOT SURFACE IN A MONTHLY REPORT. The first version
        reused the deduplicator's sentence, which is built from the file family
        — so the output read "Produced spring for Miss Platinum". "spring" is a
        filename stem, and a stem presented as a deliverable is exactly the raw
        log the brief rules out. It also leaks a filename into a document that
        gets forwarded.

        A family name is useful in a DAILY report, where the reader remembers
        what "spring" was this morning. A month later it is noise, and what the
        reader actually wants is "how much design work, for whom".

        So the month aggregates to (project × discipline) and names the
        discipline. Counts carry the magnitude; no adjective does.
        """
        DISCIPLINE_PHRASE: dict[Category, str] = {
            Category.GRAPHIC_DESIGN: "Produced and revised design assets",
            Category.AI_CREATIVE: "Generated and refined AI creative assets",
            Category.VIDEO_PRODUCTION: "Produced and edited video content",
            Category.SOCIAL_MEDIA: "Produced social media content",
            Category.DIGITAL_MARKETING: "Produced campaign assets",
            Category.WEBSITE: "Updated website content",
            Category.DEVELOPMENT: "Developed application content",
            Category.DOCUMENTATION: "Prepared reports and documentation",
            Category.PRESENTATION: "Built presentation material",
            Category.RESEARCH: "Carried out research",
            Category.MANAGEMENT: "Coordinated across the department",
        }

        merged: dict[tuple[str, Category], dict[str, int]] = {}
        for item in items:
            key = (item.project, item.category)
            bucket = merged.setdefault(key, {"files": 0, "sittings": 0})
            bucket["files"] += item.file_count
            bucket["sittings"] += item.session_count

        lines: list[str] = []
        # Most substantial first — a director reads the top three lines.
        for (project, category), counts in sorted(
            merged.items(), key=lambda kv: (-kv[1]["sittings"], -kv[1]["files"], kv[0][0])
        ):
            phrase = DISCIPLINE_PHRASE.get(category, "Worked on")
            text = phrase
            if project and project != "Unclassified":
                text = f"{text} for {project}"

            parts: list[str] = []
            if counts["files"]:
                parts.append(f"{counts['files']} file{'s' if counts['files'] != 1 else ''}")
            if counts["sittings"] > 1:
                parts.append(f"{counts['sittings']} sittings")
            if parts:
                text = f"{text} ({', '.join(parts)})"
            lines.append(text)
        return lines

    @staticmethod
    def _ordered(items: list[WorkItem]) -> list[WorkItem]:
        """Most substantial first — a director reads the top three lines."""
        return sorted(items, key=lambda i: (-i.session_count, -i.file_count, i.project))

    @staticmethod
    def _sentence(item: WorkItem) -> str:
        """
        One line of a report.

        The verb comes from the deduplicator, which derived it from the
        discipline. All this adds is the brand and a magnitude — and the
        magnitude is a plain count, never an adjective.
        """
        text = item.action
        if item.project and item.project != "Unclassified":
            text = f"{text} for {item.project}"

        # Across several sittings is a genuine fact about the month and is the
        # one thing worth adding; "extensive" would not be.
        if item.session_count > 1:
            text = f"{text} ({item.session_count} sittings)"
        return text

    @staticmethod
    def _statistics(
        items: list[WorkItem],
        rows: list[Any],
        session_stats: dict[str, int] | None,
        sessions_by_category: dict[str, int] | None,
    ) -> list[str]:
        projects = {i.project for i in items if i.project != "Unclassified"}
        files = {
            r["filename"] for r in rows
            if isinstance(r, dict) or "filename" in r.keys()
            if r["filename"]
        } if rows else set()

        lines = ["## Statistics", ""]
        lines.append(f"Projects worked on: {len(projects)}")
        lines.append(f"Unique files: {len(files)}")
        if session_stats:
            lines.append(f"Work sessions: {session_stats.get('sessions', 0)}")
            # NOT "hours worked". A span is the gap between the first and last
            # file of a sitting and contains whatever else happened in it.
            lines.append(
                f"Tracked activity span: {_fmt_minutes(session_stats.get('tracked_minutes', 0))}"
            )
        if sessions_by_category:
            for category, count in sessions_by_category.items():
                lines.append(f"{category} sessions: {count}")
        lines.append("")
        return lines

    @staticmethod
    def _month_label(period_start: str) -> str:
        try:
            return datetime.strptime(period_start, "%Y-%m-%d").strftime("%B %Y")
        except ValueError:
            return period_start

    # ------------------------------------------------------------------
    @staticmethod
    def write(content: str, reports_dir: Path | str, name: str) -> Path:
        """Atomic, for the same reason the daily report is: people open it at once."""
        import os
        import tempfile

        out_dir = Path(reports_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{name}.md"

        fd, tmp = tempfile.mkstemp(dir=str(out_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

        log.info("Report written to %s", target)
        return target


# ----------------------------------------------------------------------
# Period helpers
# ----------------------------------------------------------------------
def month_bounds(any_day: date | str) -> tuple[str, str]:
    """First and last day of the month containing `any_day`, inclusive."""
    d = any_day if isinstance(any_day, date) else datetime.strptime(any_day, "%Y-%m-%d").date()
    first = d.replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return first.isoformat(), (next_month - timedelta(days=1)).isoformat()


def week_bounds(any_day: date | str) -> tuple[str, str]:
    """
    The Sunday-to-Saturday week containing `any_day`.

    SUNDAY, not Monday. Kuwait works Sunday to Thursday, and a review that runs
    Monday to Sunday splits the working week across two reports and puts the
    weekend in the middle of one. The brief asks for a Thursday review, which
    only makes sense on a week that starts on Sunday.
    """
    d = any_day if isinstance(any_day, date) else datetime.strptime(any_day, "%Y-%m-%d").date()
    # Python's weekday(): Monday is 0, Sunday is 6. Shift so Sunday is 0.
    offset = (d.weekday() + 1) % 7
    start = d - timedelta(days=offset)
    return start.isoformat(), (start + timedelta(days=6)).isoformat()
