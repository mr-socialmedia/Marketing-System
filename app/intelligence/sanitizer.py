"""
The sanitizer — the last thing that runs before anything could leave the device.

THIS IS A CHOKE POINT, NOT A HELPER. Nothing in this program may hand data to
an AI provider, an HTTP client or an export without passing it through here
first. The value of that is structural: there is one function to audit, one
place to test, and one thing to read when somebody asks "what exactly would you
send?".

WHAT IT REMOVES, AND WHY EACH ONE.

  Full paths          `D:\\Company\\Platinum\\Confidential\\ClientData\\...`
                      describes an org chart and a client list. The project is
                      the only part that carries meaning for a report.
  Filenames           `termination-letter-ahmed.docx` is a disclosure about a
                      third party who never agreed to any of this. Off by
                      default; the extension and the family survive.
  Usernames           `C:\\Users\\mohamed.r\\...` identifies a person; it also
                      appears in nearly every Windows path.
  Hostnames, UNC      `\\\\NAS01\\Finance` names infrastructure.
  Drive letters       Low value, non-zero signal.
  Emails, phones      Sometimes sit in filenames of exported assets.

WHAT SURVIVES: project, brand, category, action, counts, approximate times.
That is enough to write "Produced campaign visuals for Platinum Fit" and not
enough to reconstruct anything about the machine, the client or the people.

FAIL CLOSED. An unrecognised shape is dropped, not passed through. A sanitizer
that forwards what it does not understand is an allowlist pretending to be a
denylist.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)


# Anything resembling a filesystem path, in any of the shapes Windows produces.
_PATH_PATTERNS = [
    re.compile(r"[a-zA-Z]:[\\/][^\s\"'<>|]*"),      # C:\... or C:/...
    re.compile(r"\\\\[^\s\"'<>|]+"),                 # \\server\share
    re.compile(r"(?:/[^\s/\"'<>|]+){2,}"),           # /home/user/...
]

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Deliberately loose: a false positive costs a redacted string, a false
# negative costs a leaked number.
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")

_REDACTED = "[redacted]"


@dataclass
class SanitizedWorkItem:
    """
    The ONLY shape that may be sent to an external service.

    A closed set of fields rather than a dict, so adding something sendable is
    an edit to this class and shows up in review — which is where a new field
    carrying a filename would be caught.
    """

    project: str
    category: str
    action: str
    brand: str | None = None
    file_count: int = 0
    session_count: int = 0
    approximate_date: str | None = None       # YYYY-MM-DD, never a timestamp
    duration_bucket: str | None = None        # "short" | "medium" | "long"
    file_types: list[str] = field(default_factory=list)   # extensions only
    confidence: float = 0.5

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "project": self.project,
            "category": self.category,
            "action": self.action,
            "file_count": self.file_count,
            "session_count": self.session_count,
            "confidence": self.confidence,
        }
        if self.brand:
            payload["brand"] = self.brand
        if self.approximate_date:
            payload["date"] = self.approximate_date
        if self.duration_bucket:
            payload["duration"] = self.duration_bucket
        if self.file_types:
            payload["file_types"] = self.file_types
        return payload


class Sanitizer:
    """
    Strip identifying detail. Constructed with the user's explicit choices.

    `allow_filenames` defaults to False and is the only switch that widens what
    leaves the machine. It exists because sometimes a filename genuinely is the
    deliverable name — but it is the user's decision, made once, visibly.
    """

    def __init__(self, allow_filenames: bool = False) -> None:
        self._allow_filenames = bool(allow_filenames)

    # ------------------------------------------------------------------
    def scrub_text(self, text: str | None) -> str:
        """
        Remove paths, emails and phone numbers from free text.

        Paths first: an email inside a path would otherwise be redacted twice
        and leave a confusing `[redacted]` fragment behind.
        """
        if not text:
            return ""
        out = str(text)
        for pattern in _PATH_PATTERNS:
            out = pattern.sub(_REDACTED, out)
        out = _EMAIL.sub(_REDACTED, out)
        out = _PHONE.sub(_REDACTED, out)
        return re.sub(r"\s{2,}", " ", out).strip()

    def contains_path(self, text: str | None) -> bool:
        """For the security tests, and for asserting before any send."""
        if not text:
            return False
        return any(p.search(str(text)) for p in _PATH_PATTERNS)

    # ------------------------------------------------------------------
    @staticmethod
    def duration_bucket(minutes: float | int | None) -> str | None:
        """
        A bucket, not a number.

        "47 minutes starting at 14:03" reconstructs a working pattern. "medium"
        is enough for a report to say the work was substantial without
        describing somebody's afternoon.
        """
        if minutes is None:
            return None
        m = float(minutes)
        if m < 30:
            return "short"
        if m < 120:
            return "medium"
        return "long"

    def sanitize_item(self, item: Any) -> SanitizedWorkItem | None:
        """
        A WorkItem (or anything with those attributes) → a sendable item.

        Returns None when the input has no project at all. Sending an
        unidentified item is sending noise that an AI will happily invent a
        story about, and an invented achievement in a monthly report is the
        failure mode this whole pipeline is built to avoid.
        """
        project = getattr(item, "project", None)
        if not project or project == "Unclassified":
            return None

        category = getattr(item, "category", None)
        category_name = getattr(category, "value", None) or str(category or "Unclassified")

        first = getattr(item, "first_at", None)
        approx = first.strftime("%Y-%m-%d") if isinstance(first, datetime) else None

        duration = None
        last = getattr(item, "last_at", None)
        if isinstance(first, datetime) and isinstance(last, datetime):
            duration = self.duration_bucket((last - first).total_seconds() / 60.0)

        file_types: list[str] = []
        for ext in getattr(item, "file_types", []) or []:
            e = str(ext).lower()
            if e.startswith(".") and len(e) <= 8 and e[1:].isalnum():
                file_types.append(e)

        action = self.scrub_text(getattr(item, "action", ""))
        if self._allow_filenames:
            # Even when allowed, the text still goes through scrub_text above,
            # so a filename survives but an embedded path does not.
            pass
        else:
            # Strip anything that looks like a file name with an extension.
            action = re.sub(r"\b[\w \-()]+\.[a-zA-Z0-9]{2,5}\b", "a file", action)

        return SanitizedWorkItem(
            project=self.scrub_text(project) or "Unclassified",
            brand=self.scrub_text(getattr(item, "brand", None)) or None,
            category=category_name,
            action=action,
            file_count=int(getattr(item, "file_count", 0) or 0),
            session_count=int(getattr(item, "session_count", 0) or 0),
            approximate_date=approx,
            duration_bucket=duration,
            file_types=sorted(set(file_types)),
            confidence=float(getattr(item, "confidence", 0.5) or 0.5),
        )

    def sanitize_batch(self, items: list[Any]) -> list[dict[str, Any]]:
        """
        Sanitize a batch, and assert the result before returning it.

        The assertion is the point. It re-checks the OUTPUT for path-shaped
        text rather than trusting that the transforms above were complete — so
        a future field added to SanitizedWorkItem that carries a path is caught
        here instead of on the wire.
        """
        out: list[dict[str, Any]] = []
        for item in items:
            sanitized = self.sanitize_item(item)
            if sanitized is None:
                continue
            payload = sanitized.to_payload()

            leaked = [
                f"{k}={v!r}" for k, v in payload.items()
                if isinstance(v, str) and self.contains_path(v)
            ]
            if leaked:
                # Drop it. Never "clean it again and hope".
                log.error("Sanitizer output still contained a path; item dropped.")
                continue
            out.append(payload)
        return out
