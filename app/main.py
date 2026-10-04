"""
The command line. Phase 1 has no GUI; this is the whole interface.

A CLI FIRST IS NOT A SHORTCUT. Every privacy control has to be reachable and
scriptable before any of it is wrapped in PySide6, because a control that only
exists behind a window cannot be tested, cannot be automated, and cannot be
verified by anyone who does not trust the window. The Phase 2 GUI calls exactly
these functions.

    python run.py status                    what is being tracked, right now
    python run.py watch                     start tracking (foreground)
    python run.py pause | resume
    python run.py private on | off
    python run.py report [--date YYYY-MM-DD]
    python run.py folders add|remove|list
    python run.py retention run|preview
    python run.py delete-day [--date YYYY-MM-DD]
    python run.py audit
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import date, datetime
from pathlib import Path

from .config import Config, load_config, save_config
from .database.db import ActivityRepository, AuditRepository, Database, ReportRepository
from .intelligence.classifier import Classifier
from .intelligence.deduplicator import Deduplicator
from .reporting.daily_report import DailyReportGenerator
from .security.audit import AuditLog
from .security.encryption import build_cipher
from .security.privacy import PrivacyGate
from .security.retention import RetentionManager

log = logging.getLogger("tracker")


def _setup_logging(log_dir: Path, verbose: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_dir / "tracker.log", encoding="utf-8"))
    except OSError:
        pass  # A read-only log directory must not stop the program running.

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        handlers=handlers,
    )
    # watchdog is extremely chatty at DEBUG and would bury everything else.
    logging.getLogger("watchdog").setLevel(logging.WARNING)


class Application:
    """Wires everything together once, so the commands stay short."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.db = Database(config.database_path)

        data_dir = Path(config.database_path).parent
        self.cipher = build_cipher(data_dir / "path.key")

        self.activities = ActivityRepository(self.db, self.cipher)
        self.audit_repo = AuditRepository(self.db)
        self.audit = AuditLog(self.audit_repo)
        self.gate = PrivacyGate(
            paused=config.privacy.paused,
            # Never restored. Private mode resets on every start — see
            # security/privacy.py for why stickiness is the wrong default.
            private_mode=False,
            audit_sink=self.audit,
        )
        self.classifier = Classifier(config.project_rules)
        self.reports = ReportRepository(self.db)
        self.retention = RetentionManager(
            self.db, config.retention, self.activities, self.audit_repo
        )

    def close(self) -> None:
        self.db.checkpoint()
        self.db.close()


# ======================================================================
# Commands
# ======================================================================
def cmd_status(app: Application) -> int:
    """
    What is being collected. The screen that has to be honest.

    Printed in full every time, with no "see settings for details". The brief's
    standard is that the user should never wonder what the software collects,
    and that means showing all of it in one place rather than making them hunt.
    """
    status = app.gate.status()
    cfg = app.config
    roots = cfg.approved_roots()

    print(f"\n  {status.label}")
    print(f"  {status.reason}\n")

    print("  Monitored folders")
    if roots:
        for r in roots:
            mark = "" if r.exists() else "   (not available right now)"
            print(f"    - {r}{mark}")
    else:
        print("    (none — nothing is being tracked)")

    print("\n  Application tracking")
    if cfg.tracking.track_applications:
        apps = cfg.tracking.approved_applications or ["(none approved)"]
        print(f"    ON — {', '.join(apps)}")
    else:
        print("    OFF")

    print("\n  Integrations")
    ints = cfg.integrations
    enabled = [n for n, on in (
        ("Gmail", ints.gmail_enabled),
        ("Google Calendar", ints.google_calendar_enabled),
        ("Google Drive", ints.google_drive_enabled),
    ) if on]
    print(f"    {', '.join(enabled) if enabled else 'none'}")

    print("\n  AI")
    print(f"    {'ON — ' + cfg.ai.provider if cfg.ai.enabled else 'OFF'}")

    print("\n  Network")
    print(f"    {'local only — nothing leaves this device' if cfg.privacy.local_only else 'network allowed'}")

    print("\n  Retention")
    print(f"    raw activity {cfg.retention.raw_activity_days} days"
          f"  ·  audit {cfg.retention.audit_log_days} days"
          f"  ·  reports {'kept' if cfg.retention.reports_days == 0 else str(cfg.retention.reports_days) + ' days'}")

    print("\n  Never collected")
    print("    keystrokes · passwords · clipboard · screenshots · screen recording")
    print("    microphone · camera · browser history · browser tabs · file contents")

    today = date.today().isoformat()
    print(f"\n  Today: {app.activities.count_for_date(today)} activities recorded")
    print(f"  Path encryption: {app.cipher.scheme}\n")
    return 0


def cmd_watch(app: Application) -> int:
    from .tracking.filesystem_tracker import FilesystemTracker, TrackerError

    roots = app.config.approved_roots()
    if not roots:
        print("No monitored folders configured. Add one:")
        print("  python run.py folders add \"D:\\\\Work\\\\Platinum\"")
        return 1

    try:
        tracker = FilesystemTracker(
            roots=roots,
            gate=app.gate,
            classifier=app.classifier,
            repository=app.activities,
            ignored_extensions=app.config.tracking.ignored_extensions,
            ignored_directories=app.config.tracking.ignored_directories,
            debounce_seconds=app.config.tracking.debounce_seconds,
            data_dir=Path(app.config.database_path).parent,
        )
    except TrackerError as exc:
        print(f"Cannot start: {exc}")
        return 1

    tracker.start()
    app.audit.record("tracker_started")
    print(f"\n  Tracking {len(tracker.watched_roots)} folder(s). Ctrl+C to stop.")
    print(f"  {app.gate.status().label}\n")

    stopping = False

    def _handle_signal(_sig, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        pass  # SIGTERM is not available in every Windows context.

    try:
        while not stopping:
            time.sleep(0.5)
    finally:
        tracker.stop()
        app.audit.record("tracker_stopped")
        stats = tracker.stats()
        print(f"\n  Stopped. Seen {stats['seen']}, stored {stats['stored']}, "
              f"debounced {stats['dropped_debounce']}, "
              f"filtered {stats['dropped_filter']}, "
              f"skipped while private/paused {stats['dropped_privacy']}.\n")
    return 0


def cmd_pause(app: Application) -> int:
    status = app.gate.pause()
    app.config.privacy.paused = True
    save_config(app.config)
    print(f"  {status.label}")
    return 0


def cmd_resume(app: Application) -> int:
    status = app.gate.resume()
    app.config.privacy.paused = False
    save_config(app.config)
    print(f"  {status.label}")
    return 0


def cmd_private(app: Application, on: bool) -> int:
    status = app.gate.enable_private_mode() if on else app.gate.disable_private_mode()
    # Deliberately NOT persisted. It resets on restart by design.
    print(f"  {status.label}")
    if on:
        print("  Nothing will be recorded until this is turned off or the app restarts.")
    return 0


def cmd_report(app: Application, day: str | None) -> int:
    target = day or date.today().isoformat()
    try:
        datetime.strptime(target, "%Y-%m-%d")
    except ValueError:
        print(f"  {target!r} is not a date (expected YYYY-MM-DD).")
        return 1

    rows = app.activities.for_date(target)
    generator = DailyReportGenerator(Deduplicator())
    content = generator.generate(rows, target)
    path = generator.write(content, app.config.reports_dir, target)

    app.reports.save("daily", target, target, content, str(path))
    app.audit.record("report_generated", f"daily {target}")

    print(f"\n{content}\n")
    print(f"  Saved to {path}")
    return 0


def cmd_folders(app: Application, action: str, folder: str | None) -> int:
    cfg = app.config
    if action == "list":
        for r in cfg.approved_roots():
            print(f"  {r}")
        if not cfg.tracking.folders:
            print("  (none)")
        return 0

    if not folder:
        print("  A folder path is required.")
        return 1

    path = Path(folder).expanduser()
    if action == "add":
        if not path.exists():
            print(f"  {path} does not exist.")
            return 1
        resolved = str(path.resolve())
        if resolved in cfg.tracking.folders:
            print("  Already monitored.")
            return 0
        cfg.tracking.folders.append(resolved)
        save_config(cfg)
        app.audit.record("folder_added")
        print(f"  Now monitoring {resolved}")
        return 0

    if action == "remove":
        resolved = str(path.resolve())
        before = len(cfg.tracking.folders)
        cfg.tracking.folders = [f for f in cfg.tracking.folders
                                if str(Path(f).resolve()) != resolved]
        if len(cfg.tracking.folders) == before:
            print("  That folder was not monitored.")
            return 1
        save_config(cfg)
        app.audit.record("folder_removed")
        print(f"  Stopped monitoring {resolved}")
        return 0

    print(f"  Unknown action {action!r}.")
    return 1


def cmd_retention(app: Application, run: bool) -> int:
    if run:
        removed = app.retention.run()
        print(f"  Removed: {removed}")
    else:
        print(f"  A sweep now would remove: {app.retention.preview()}")
    return 0


def cmd_delete_day(app: Application, day: str | None) -> int:
    target = day or date.today().isoformat()
    count = app.activities.count_for_date(target)
    if count == 0:
        print(f"  Nothing recorded for {target}.")
        return 0
    removed = app.activities.delete_for_date(target)
    app.audit.record("data_deleted", f"activities={removed} date={target}")
    print(f"  Deleted {removed} activity record(s) for {target}.")
    return 0


def cmd_audit(app: Application) -> int:
    rows = app.audit.recent(50)
    if not rows:
        print("  No audit entries.")
        return 0
    for row in rows:
        detail = f"  {row['detail']}" if row["detail"] else ""
        print(f"  {row['occurred_at']}  {row['action']}{detail}")
    return 0


# ======================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="work-tracker",
        description="Mohamed Work Tracker — local-first, privacy-first work activity tracking.",
    )
    p.add_argument("--config", type=str, default=None, help="Path to config.json")
    p.add_argument("-v", "--verbose", action="store_true")

    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Show exactly what is being tracked")
    sub.add_parser("watch", help="Start tracking in the foreground")
    sub.add_parser("pause", help="Pause tracking")
    sub.add_parser("resume", help="Resume tracking")

    private = sub.add_parser("private", help="Private Mode on or off")
    private.add_argument("state", choices=["on", "off"])

    report = sub.add_parser("report", help="Generate a daily report")
    report.add_argument("--date", type=str, default=None)

    folders = sub.add_parser("folders", help="Manage monitored folders")
    folders.add_argument("action", choices=["list", "add", "remove"])
    folders.add_argument("folder", nargs="?", default=None)

    retention = sub.add_parser("retention", help="Preview or run a retention sweep")
    retention.add_argument("action", choices=["preview", "run"])

    delete = sub.add_parser("delete-day", help="Delete one day's activity")
    delete.add_argument("--date", type=str, default=None)

    sub.add_parser("audit", help="Show the privacy audit trail")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    _setup_logging(Path(config.log_dir), args.verbose)

    app = Application(config)
    try:
        if args.command == "status":
            return cmd_status(app)
        if args.command == "watch":
            return cmd_watch(app)
        if args.command == "pause":
            return cmd_pause(app)
        if args.command == "resume":
            return cmd_resume(app)
        if args.command == "private":
            return cmd_private(app, args.state == "on")
        if args.command == "report":
            return cmd_report(app, args.date)
        if args.command == "folders":
            return cmd_folders(app, args.action, args.folder)
        if args.command == "retention":
            return cmd_retention(app, args.action == "run")
        if args.command == "delete-day":
            return cmd_delete_day(app, args.date)
        if args.command == "audit":
            return cmd_audit(app)
        return 1
    finally:
        app.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
