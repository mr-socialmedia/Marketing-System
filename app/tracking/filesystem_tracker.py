"""
The filesystem watcher. The only module that touches the outside world.

THE ORDER OF CHECKS IN `_handle` IS THE SECURITY DESIGN:

    1. privacy gate      — collecting at all?
    2. whitelist         — inside an approved folder?
    3. exclusions        — temp file, build output, .git?
    4. debounce          — already recorded this?
    5. classify, encrypt, store

Nothing above step 2 ever learns the path's value. The gate is first because
when Private Mode is on the right amount of work to do is none, and the
whitelist is second because every later step is allowed to handle the path.

THIS MODULE NEVER OPENS A FILE. It calls `os.stat` for a size and nothing else.
`watchdog` hands over a path and an event type; turning that into content would
take an explicit `open()`, and there is not one in this file. The security test
suite greps for it.

IT ALSO NEVER WRITES TO A MONITORED FOLDER. The data directory lives beside the
application, never under a watched root — otherwise the tracker's own writes
would be events, which would produce more writes. A deliberate check at startup
refuses that configuration rather than discovering it as a loop.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..database.models import Activity, EventType
from .filters import Debouncer, PathFilter, family_key

if TYPE_CHECKING:  # pragma: no cover
    from ..database.db import ActivityRepository
    from ..intelligence.classifier import Classifier
    from ..security.privacy import PrivacyGate

log = logging.getLogger(__name__)

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except ImportError:  # pragma: no cover - declared in requirements
    WATCHDOG_AVAILABLE = False
    FileSystemEventHandler = object  # type: ignore[assignment,misc]
    Observer = None  # type: ignore[assignment]


class TrackerError(RuntimeError):
    pass


class _Handler(FileSystemEventHandler):  # type: ignore[misc]
    """
    Translates watchdog callbacks into stored activities.

    Every method is defensive to the point of swallowing exceptions, and that
    is not laziness: an exception escaping a watchdog callback kills the
    observer thread, after which the program looks healthy and silently tracks
    nothing for the rest of the day.
    """

    def __init__(
        self,
        *,
        gate: PrivacyGate,
        path_filter: PathFilter,
        debouncer: Debouncer,
        classifier: Classifier,
        repository: ActivityRepository,
        roots: list[Path],
    ) -> None:
        super().__init__()
        self._gate = gate
        self._filter = path_filter
        self._debounce = debouncer
        self._classifier = classifier
        self._repo = repository
        self._roots = roots
        self._stats = {"seen": 0, "stored": 0, "dropped_privacy": 0,
                       "dropped_filter": 0, "dropped_debounce": 0}
        self._stats_lock = threading.Lock()

    # ------------------------------------------------------------------
    def on_created(self, event: Any) -> None:
        self._handle(event, EventType.CREATED)

    def on_modified(self, event: Any) -> None:
        self._handle(event, EventType.MODIFIED)

    def on_moved(self, event: Any) -> None:
        # The DESTINATION is the fact worth keeping: a rename to
        # "logo-approved.psd" is the meaningful half of the event.
        self._handle(event, EventType.MOVED, path=getattr(event, "dest_path", None))

    def on_deleted(self, event: Any) -> None:
        self._handle(event, EventType.DELETED)

    # ------------------------------------------------------------------
    def _handle(self, event: Any, event_type: EventType, path: str | None = None) -> None:
        try:
            if getattr(event, "is_directory", False):
                return

            # ---- 1. privacy -------------------------------------------
            # Before anything reads the path. When this is closed the program
            # does not learn that a file changed, only that an event arrived.
            if not self._gate.allows_collection():
                self._bump("dropped_privacy")
                return

            raw = path or getattr(event, "src_path", None)
            if not raw:
                return

            with self._stats_lock:
                self._stats["seen"] += 1

            # ---- 2 & 3. whitelist and exclusions ----------------------
            decision = self._filter.accepts(raw)
            if not decision.accepted:
                self._bump("dropped_filter")
                # The reason is logged, never the path: a rejected path may be
                # from outside the whitelist entirely.
                log.debug("Event ignored (%s)", decision.reason)
                return

            # ---- 4. debounce ------------------------------------------
            should_record, repeats = self._debounce.should_record(raw, event_type.value)
            if not should_record:
                self._bump("dropped_debounce")
                return

            # ---- 5. classify and store --------------------------------
            self._store(raw, event_type, decision.root, repeats)

        except Exception:
            # Never propagate. See the class docstring.
            log.exception("Failed to handle a filesystem event; tracking continues.")

    def _store(self, raw_path: str, event_type: EventType,
               root: Path | None, repeats: int) -> None:
        p = Path(raw_path)

        # os.stat reads METADATA, not contents. It is wrapped because a file
        # being written by Premiere is routinely locked, and a tracker must
        # never be the reason a save fails.
        size: int | None = None
        if event_type is not EventType.DELETED:
            try:
                size = p.stat().st_size
            except (OSError, PermissionError):
                size = None

        classification = self._classifier.classify(p, root)

        now = datetime.now(timezone.utc)
        activity = Activity(
            occurred_at=now,
            # The LOCAL day, not the UTC one. A file saved at 01:30 Kuwait
            # belongs to that day's report; grouping by UTC would file it under
            # the previous one for everyone east of Greenwich.
            local_date=datetime.now().strftime("%Y-%m-%d"),
            event_type=event_type,
            full_path=str(p),
            filename=p.name,
            extension=p.suffix.lower() or None,
            family_key=family_key(p),
            # The LEAF folder only. The full chain is the sensitive part and it
            # is already in the encrypted path.
            parent_folder=p.parent.name or None,
            root_label=root.name if root else None,
            project=classification.project,
            brand=classification.brand,
            category=classification.category,
            repeat_count=repeats,
            size_bytes=size,
        )

        if self._repo.add(activity) is not None:
            self._bump("stored")

    def _bump(self, key: str) -> None:
        with self._stats_lock:
            self._stats[key] += 1

    def stats(self) -> dict[str, int]:
        with self._stats_lock:
            return dict(self._stats)


class FilesystemTracker:
    """Starts and stops the observer, and refuses an unsafe configuration."""

    def __init__(
        self,
        *,
        roots: list[Path],
        gate: PrivacyGate,
        classifier: Classifier,
        repository: ActivityRepository,
        ignored_extensions: list[str],
        ignored_directories: list[str],
        debounce_seconds: int = 90,
        data_dir: Path | None = None,
    ) -> None:
        if not WATCHDOG_AVAILABLE:
            raise TrackerError("watchdog is not installed; run `pip install -r requirements.txt`")

        self._roots = [r for r in roots if r.exists()]
        missing = [r for r in roots if not r.exists()]
        for m in missing:
            # Not fatal. A folder on a disconnected external drive should not
            # stop the other three being watched.
            log.warning("Monitored folder is not available right now: %s", m)

        if data_dir is not None:
            self._assert_not_self_watching(data_dir)

        self._filter = PathFilter(self._roots, ignored_extensions, ignored_directories)
        self._debounce = Debouncer(debounce_seconds)
        self._handler = _Handler(
            gate=gate, path_filter=self._filter, debouncer=self._debounce,
            classifier=classifier, repository=repository, roots=self._roots,
        )
        self._observer: Any = None

    def _assert_not_self_watching(self, data_dir: Path) -> None:
        """
        Refuse to watch a folder containing our own database.

        Otherwise every write produces an event, which produces a write. The
        exclusion list would usually catch it, but "usually" is the wrong
        standard for a feedback loop that fills a disk.
        """
        resolved = data_dir.resolve()
        for root in self._roots:
            if resolved == root or resolved.is_relative_to(root):
                raise TrackerError(
                    f"The data directory ({resolved}) is inside a monitored folder "
                    f"({root}). The tracker would record its own writes. Move the "
                    f"data directory or remove that folder."
                )

    # ------------------------------------------------------------------
    def start(self) -> None:
        if not self._roots:
            log.warning("No monitored folders are available. Nothing is being tracked.")
            return

        self._observer = Observer()
        for root in self._roots:
            self._observer.schedule(self._handler, str(root), recursive=True)
            log.info("Watching %s", root)
        self._observer.daemon = True
        self._observer.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=timeout)
        self._observer = None
        log.info("Tracker stopped. %s", self.stats())

    @property
    def running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def stats(self) -> dict[str, int]:
        return self._handler.stats()

    @property
    def watched_roots(self) -> list[Path]:
        return list(self._roots)
