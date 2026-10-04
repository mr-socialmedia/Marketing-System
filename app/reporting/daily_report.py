"""
The daily report. Markdown, generated locally, with no AI involved.

A REPORT THAT NEEDS AI TO EXIST IS A REPORT THAT STOPS EXISTING WHEN THE KEY
EXPIRES. The default configuration has AI switched off, so this has to produce
something a person can actually send with nothing but the database. The AI
layer, when enabled, improves the wording of what this already says — it does
not supply the content.

IT DOES NOT CLAIM COMPLETION. Nothing observable in a file event means a task
was finished: a file stops changing when it is done, when it is abandoned, and
when the person goes on holiday. Every line here says what was worked on. The
brief's rule — never invent task completion — is enforced by there being no
code path that emits the word.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..database.models import Category, WorkItem
from ..intelligence.deduplicator import Deduplicator

log = logging.getLogger(__name__)


# The order sections appear in. Creative work first because that is what this
# department's days are mostly made of, and a report whose first section is
# usually empty reads as a broken report.
SECTION_ORDER: list[Category] = [
    Category.GRAPHIC_DESIGN,
    Category.VIDEO_PRODUCTION,
    Category.AI_CREATIVE,
    Category.SOCIAL_MEDIA,
    Category.DIGITAL_MARKETING,
    Category.WEBSITE,
    Category.DEVELOPMENT,
    Category.PRESENTATION,
    Category.DOCUMENTATION,
    Category.RESEARCH,
    Category.MANAGEMENT,
    Category.UNCLASSIFIED,
]


class DailyReportGenerator:
    """Rows for one day → a Markdown report."""

    def __init__(self, deduplicator: Deduplicator | None = None) -> None:
        self._dedup = deduplicator or Deduplicator()

    # ------------------------------------------------------------------
    def generate(self, rows: list[Any], for_date: date | str) -> str:
        day = for_date if isinstance(for_date, str) else for_date.isoformat()

        if not rows:
            # An empty day is a real answer, and saying so beats an empty file
            # that looks like the program broke.
            return (
                f"# Work Report — {day}\n\n"
                "No tracked activity for this day.\n\n"
                "_This can mean a day off, work outside the approved folders, "
                "or tracking having been paused._\n"
            )

        items = self._dedup.group(rows)
        return self._render(items, day, len(rows))

    # ------------------------------------------------------------------
    def _render(self, items: list[WorkItem], day: str, raw_count: int) -> str:
        by_category: dict[Category, list[WorkItem]] = defaultdict(list)
        for item in items:
            by_category[item.category].append(item)

        projects = sorted({i.project for i in items if i.project != "Unclassified"})
        files = sum(i.file_count for i in items)

        lines: list[str] = [f"# Work Report — {day}", ""]

        # --- summary ---------------------------------------------------
        lines += [
            "## Summary",
            "",
            f"- Work items: **{len(items)}**",
            f"- Projects: **{len(projects)}**" + (f" — {', '.join(projects)}" if projects else ""),
            f"- Files touched: **{files}**",
            f"- Raw events recorded: {raw_count}",
            "",
        ]

        first = min((i.first_at for i in items if i.first_at), default=None)
        last = max((i.last_at for i in items if i.last_at), default=None)
        if first and last:
            # An activity WINDOW, not "hours worked". The gap between the first
            # and last file touched includes lunch, meetings and everything
            # else; calling it worked time would be a number someone repeats.
            lines += [
                f"- Activity window: {first.astimezone().strftime('%H:%M')}"
                f" – {last.astimezone().strftime('%H:%M')}",
                "",
            ]

        # --- the work --------------------------------------------------
        lines += ["## Work", ""]
        for category in SECTION_ORDER:
            group = by_category.get(category)
            if not group:
                continue
            lines.append(f"### {category.value}")
            lines.append("")
            for item in sorted(group, key=lambda i: i.first_at or datetime.min):
                suffix = f" — {item.project}" if item.project != "Unclassified" else ""
                marker = ""
                # Flagged rather than hidden. A low-confidence item is still
                # real work; it just needs a project rule, and hiding it means
                # the rule never gets added.
                if item.confidence < 0.4:
                    marker = "  _(low confidence — add a project rule)_"
                lines.append(f"- {item.action}{suffix}{marker}")
            lines.append("")

        unclassified = by_category.get(Category.UNCLASSIFIED, [])
        if unclassified:
            lines += [
                "---",
                "",
                f"_{len(unclassified)} item(s) could not be matched to a project. "
                "Add a mapping rule in Settings so they appear under the right one._",
                "",
            ]

        lines += [
            "---",
            "",
            "_Generated locally by Mohamed Work Tracker. "
            "Raw activity stays on this device._",
            "",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def write(self, content: str, reports_dir: Path | str, day: str) -> Path:
        """
        Write the report to disk, atomically.

        Atomic because a report is something a person opens the moment it is
        generated, and a half-written Markdown file is one they will send.
        """
        import os
        import tempfile

        out_dir = Path(reports_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"daily-{day}.md"

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
