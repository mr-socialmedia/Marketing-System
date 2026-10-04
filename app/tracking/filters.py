"""
The whitelist, the exclusions, and the debounce.

This module decides whether an event is seen at all. Everything downstream
assumes a path reached it only because `PathFilter.accepts()` returned True,
so a bug here is not a missed feature — it is the whole security model.

THREE CHECKS, IN THIS ORDER, AND THE ORDER MATTERS:

  1. CONTAINMENT   Is the resolved path inside an approved root? A path that
                   fails this is never examined further, never logged with its
                   value, and never counted.
  2. EXCLUSION     Is it a temp file, a build artefact, a .git object? These
                   are inside approved roots by definition, so they can only be
                   removed afterwards.
  3. DEBOUNCE      Have we already recorded this file recently?

Containment first because the other two are about noise and this one is about
disclosure. Logging an excluded path at DEBUG is fine; logging a path from
outside the whitelist is the thing this program promises never to do.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePath

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilterDecision:
    """Why a path was accepted or refused. The reason is for the UI and tests."""

    accepted: bool
    reason: str
    root: Path | None = None


class PathFilter:
    """
    Containment and exclusion. Holds no state, so it is safe to share.

    Roots are passed in ALREADY RESOLVED (see Config.approved_roots). Resolving
    here too would be harmless but would hide where the guarantee comes from.
    """

    def __init__(
        self,
        roots: list[Path],
        ignored_extensions: list[str],
        ignored_directories: list[str],
    ) -> None:
        self._roots = list(roots)
        self._ignored_ext = {e.lower() if e.startswith(".") else f".{e.lower()}"
                             for e in ignored_extensions}
        self._ignored_dirs = {d.lower() for d in ignored_directories}

    # ------------------------------------------------------------------
    def _resolve(self, path: str | Path) -> Path | None:
        """
        Resolve without requiring existence.

        `strict=False` because a delete or a move reports a path that is
        already gone, and refusing to resolve it would make the filter answer
        "outside the whitelist" for exactly the events that matter most.
        """
        try:
            return Path(path).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            log.debug("Unresolvable path rejected: %s", exc)
            return None

    def containing_root(self, path: str | Path) -> Path | None:
        """
        The approved root this path sits under, or None.

        Uses Path.is_relative_to rather than string prefixes. `str.startswith`
        is the classic bug here: "D:\\Work" prefixes "D:\\Workspace\\Private",
        so a string check silently admits a sibling folder nobody approved.
        """
        resolved = self._resolve(path)
        if resolved is None:
            return None
        for root in self._roots:
            try:
                if resolved == root or resolved.is_relative_to(root):
                    return root
            except (OSError, ValueError):
                continue
        return None

    def _is_excluded(self, resolved: Path) -> str | None:
        """An exclusion reason, or None. Case-insensitive throughout: Windows."""
        suffix = resolved.suffix.lower()
        if suffix in self._ignored_ext:
            return f"ignored extension {suffix}"

        name = resolved.name.lower()
        # Office and Adobe scratch files: ~$report.docx, .~lock.sheet.ods#
        if name.startswith("~$") or name.startswith(".~lock"):
            return "application lock file"
        if name.endswith("~"):
            return "editor backup file"

        # The FULL chain of parents, not just the immediate one. A file at
        # .../node_modules/pkg/dist/index.js has an immediate parent of "dist",
        # and checking only that would let the whole dependency tree through.
        for part in resolved.parts[:-1]:
            if part.lower() in self._ignored_dirs:
                return f"ignored directory {part}"
        return None

    def accepts(self, path: str | Path) -> FilterDecision:
        root = self.containing_root(path)
        if root is None:
            # Deliberately no path in this message. It is the one case where
            # the value is outside the whitelist, so it must not be written
            # anywhere, including a debug log.
            return FilterDecision(False, "outside approved folders")

        resolved = self._resolve(path)
        if resolved is None:
            return FilterDecision(False, "unresolvable path")

        excluded = self._is_excluded(resolved)
        if excluded is not None:
            return FilterDecision(False, excluded, root)

        return FilterDecision(True, "accepted", root)


# ======================================================================
# Debounce
# ======================================================================
@dataclass
class _Seen:
    first_at: float
    last_at: float
    count: int


class Debouncer:
    """
    Collapse repeated events for the same file into one.

    THE PROBLEM THIS SOLVES IS NOT NOISE, IT IS TRUTH. Photoshop writes a PSD
    several times per save; an autosave fires every few minutes; Premiere
    touches its project file constantly while you scrub. Counting those as
    separate work items produces a report claiming thirty tasks for an
    afternoon spent on one, which is worse than no report — it is a confident,
    wrong one.

    Keyed on (path, event_type). A create and a later modify of the same file
    are different facts and both deserve to survive; two modifies ninety
    seconds apart are the same fact twice.

    `should_record` returns the decision and the repeat count so the caller can
    store "touched 14 times" as a strength signal on the single event it does
    keep.
    """

    def __init__(self, window_seconds: int = 90, max_entries: int = 20_000) -> None:
        self._window = float(window_seconds)
        self._max = int(max_entries)
        self._seen: dict[tuple[str, str], _Seen] = {}
        self._lock = threading.Lock()

    def should_record(
        self, key_path: str, event_type: str, now: float | None = None
    ) -> tuple[bool, int]:
        """
        (record_this, repeats_since_first).

        `now` is injectable so the tests can prove the window rather than
        sleeping through it.
        """
        ts = time.monotonic() if now is None else now
        key = (str(key_path).lower(), event_type)

        with self._lock:
            entry = self._seen.get(key)
            if entry is None or (ts - entry.last_at) > self._window:
                self._seen[key] = _Seen(first_at=ts, last_at=ts, count=1)
                self._evict_if_needed(ts)
                return True, 1

            # Inside the window: remember that it happened, record nothing.
            #
            # last_at is advanced, so continuous saving keeps extending the
            # window rather than letting a new event through every 90 seconds
            # during an hour of steady work. That is the intent: one sitting is
            # one item.
            entry.last_at = ts
            entry.count += 1
            return False, entry.count

    def _evict_if_needed(self, now: float) -> None:
        """
        Keep the table bounded.

        A long-running tracker over a large tree would otherwise grow one entry
        per file forever. Entries past the window carry no information — the
        next event for that key records anyway — so dropping them is free.
        Called with the lock already held.
        """
        if len(self._seen) <= self._max:
            return
        cutoff = now - self._window
        stale = [k for k, v in self._seen.items() if v.last_at < cutoff]
        for k in stale:
            del self._seen[k]

        if len(self._seen) > self._max:
            # Everything is still live. Drop the oldest half rather than
            # growing without bound; the cost is a few duplicate events.
            oldest = sorted(self._seen.items(), key=lambda kv: kv[1].last_at)
            for k, _ in oldest[: len(oldest) // 2]:
                del self._seen[k]
            log.debug("Debounce table trimmed under pressure.")

    def pending_counts(self) -> dict[tuple[str, str], int]:
        """Repeat counts, for attaching to a session when it closes."""
        with self._lock:
            return {k: v.count for k, v in self._seen.items()}

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


def family_key(path: str | Path) -> str:
    """
    The "file family" a name belongs to, for deduplication.

    logo-v1.psd, logo-v2.psd, logo-final.psd, logo-final-FINAL.psd and
    logo (2).psd are one piece of work with five names. Stripping version
    noise from the stem gives them a shared key, which is what lets the
    deduplicator say "worked on the logo" instead of listing five files.

    Deliberately deterministic and deliberately conservative. It runs before
    any AI and must never merge two genuinely different deliverables, so it
    only removes patterns that are unambiguously version decoration.
    """
    import re

    stem = PurePath(str(path)).stem.lower().strip()

    patterns = [
        r"[\s_-]*\(\d+\)$",                  # "logo (2)"
        r"[\s_-]*copy$",                     # "logo copy"
        r"[\s_-]*v\d+(\.\d+)*$",             # "logo-v2", "logo_v1.3"
        # The separator is REQUIRED here (+ not *). With * this matched the
        # trailing "26" of "report-2026" — the zero-width separator let the
        # digits start mid-number — and two passes reduced it to "report-",
        # eating a year. Requiring a separator means the digits must begin
        # immediately after a -, _ or space, which the tail of a year does not.
        r"[\s_-]+\d{1,2}$",                  # "logo-3", never "report-2026"
        r"[\s_-]*(final|latest|new|old|draft|wip|rev|revision|ver|version)$",
        r"[\s_-]*\d{4}[-_]?\d{2}[-_]?\d{2}$",  # trailing ISO-ish date
    ]

    # Repeatedly, because "logo-final-final-v2" needs several passes. Bounded
    # so a pathological name cannot spin.
    for _ in range(6):
        before = stem
        for pat in patterns:
            stem = re.sub(pat, "", stem).strip()
        if stem == before:
            break

    return stem or PurePath(str(path)).stem.lower()
