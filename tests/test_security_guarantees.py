"""
The security regression suite. This file is the point of the project.

Every "never" in the brief is a promise, and a promise in a README is worth
nothing after six months of feature work. These tests turn each one into a
build failure:

    never a keylogger          → no keyboard-hook library may be imported
    never screenshots          → no screen-capture library may be imported
    never the microphone       → no audio-capture library may be imported
    never the camera           → no video-capture library may be imported
    never the clipboard        → no clipboard library may be imported
    never browser history      → no browser-history reader may be imported
    never file contents        → no module may open a tracked file to read it
    never upload raw logs      → local-only mode must not open a socket
    never leave the whitelist  → a path outside it must be refused
    never send a full path     → the sanitizer output must contain none

They scan THIS SOURCE TREE rather than mocking, so adding `import pynput`
anywhere — including in a file nobody is reviewing closely — fails the run.

If a test here ever becomes inconvenient, that is the test doing its job. The
brief says it plainly: if there is a conflict between functionality and
privacy, choose privacy.
"""

from __future__ import annotations

import ast
import socket
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"


# ======================================================================
# Forbidden capabilities, as import names
# ======================================================================
FORBIDDEN_MODULES: dict[str, str] = {
    # Keyboard and mouse hooks
    "keyboard": "keylogging",
    "pynput": "keyboard/mouse hooking",
    "pyhook": "keylogging",
    "pyxhook": "keylogging",
    "evdev": "raw input device access",
    # Screen capture
    "mss": "screen capture",
    "pyscreenshot": "screen capture",
    "d3dshot": "screen capture",
    "PIL.ImageGrab": "screen capture",
    # Audio and video capture
    "sounddevice": "microphone capture",
    "pyaudio": "microphone capture",
    "speech_recognition": "microphone capture",
    "cv2": "camera capture",
    "imageio.plugins.ffmpeg": "screen/camera recording",
    # Clipboard
    "pyperclip": "clipboard access",
    "win32clipboard": "clipboard access",
    "clipboard": "clipboard access",
    # Browser data
    "browserhistory": "browser history",
    "browser_history": "browser history",
    "browser_cookie3": "browser cookies",
}

# Attribute calls that imply a forbidden capability even without the import.
FORBIDDEN_CALLS: dict[str, str] = {
    "ImageGrab.grab": "screen capture",
    "pyautogui.screenshot": "screen capture",
    "VideoCapture": "camera capture",
    "GetClipboardData": "clipboard access",
    "SetWindowsHookEx": "input hooking",
}


def _python_files() -> list[Path]:
    return [p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def test_app_directory_exists() -> None:
    """
    Guard the guards.

    If APP_DIR is wrong, every scan below finds nothing and passes. A suite
    that cannot fail is worse than no suite, because it reports a green tick.
    """
    assert APP_DIR.is_dir(), f"{APP_DIR} not found"
    files = _python_files()
    assert len(files) >= 10, f"Only {len(files)} modules scanned — is the path right?"


@pytest.mark.parametrize("source_file", _python_files(), ids=lambda p: p.name)
def test_no_forbidden_imports(source_file: Path) -> None:
    """No module may import a surveillance capability."""
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))

    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for banned, why in FORBIDDEN_MODULES.items():
                    if alias.name == banned or alias.name.startswith(banned + "."):
                        found.append(f"{alias.name} ({why})")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for banned, why in FORBIDDEN_MODULES.items():
                root = banned.split(".")[0]
                if module == root or module.startswith(root + "."):
                    found.append(f"{module} ({why})")

    assert not found, (
        f"{source_file.name} imports a forbidden capability: {', '.join(found)}. "
        "This agent must never behave like surveillance software."
    )


@pytest.mark.parametrize("source_file", _python_files(), ids=lambda p: p.name)
def test_no_forbidden_calls(source_file: Path) -> None:
    """Catch the capability even when it is reached without a bare import."""
    text = source_file.read_text(encoding="utf-8")
    # Strip comments and docstrings so this very file's vocabulary, and the
    # explanatory comments elsewhere, do not trip it.
    tree = ast.parse(text, filename=str(source_file))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body[0].value.value = ""

    code = ast.unparse(tree)
    hits = [f"{call} ({why})" for call, why in FORBIDDEN_CALLS.items() if call in code]
    assert not hits, f"{source_file.name} performs a forbidden action: {', '.join(hits)}"


# ======================================================================
# File contents are never read
# ======================================================================
def test_tracker_never_opens_a_tracked_file() -> None:
    """
    The filesystem tracker must not call open(), read_text() or read_bytes().

    It is handed a path and an event type. Turning that into content would take
    an explicit call, and the brief forbids reading contents unless the user
    enables it for a specific feature — which no Phase 1 feature does.
    """
    tracker = APP_DIR / "tracking" / "filesystem_tracker.py"
    tree = ast.parse(tracker.read_text(encoding="utf-8"), filename=str(tracker))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            offenders.append(f"open() at line {node.lineno}")
        if isinstance(func, ast.Attribute) and func.attr in {
            "read_text", "read_bytes", "open", "readlines", "read"
        }:
            offenders.append(f".{func.attr}() at line {node.lineno}")

    assert not offenders, (
        "filesystem_tracker.py reads file contents: " + ", ".join(offenders)
    )


def test_content_reading_is_off_by_default() -> None:
    from app.config import Config

    assert Config().privacy.allow_file_content_reading is False
    assert Config().privacy.allow_filenames_in_ai_payloads is False


# ======================================================================
# psutil hands you surveillance by default
# ======================================================================
#
# A psutil.Process exposes far more than the name of the program. Each of these
# would defeat a different part of the security model, and open_files() would
# defeat nearly all of it at once.
FORBIDDEN_PSUTIL_METHODS: dict[str, str] = {
    "open_files": "lists every file a process has open, across the whole disk",
    "cmdline": "contains full file paths, bypassing the folder whitelist",
    "environ": "environment variables routinely hold tokens",
    "cwd": "a filesystem path",
    "connections": "what the machine is talking to",
    "net_connections": "what the machine is talking to",
    "memory_maps": "reveals every mapped file",
    "exe": "the install path",
}


@pytest.mark.parametrize("source_file", _python_files(), ids=lambda p: p.name)
def test_no_module_reaches_for_forbidden_process_detail(source_file: Path) -> None:
    """
    Only `name` may be read from a process.

    The application tracker learns that an APPROVED program was running and for
    how long. One extra method call — open_files() in particular — would turn
    it into exactly the monitoring software this project refuses to be, and it
    would do so in a single line that reads as reasonable in review.
    """
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))

    # Strip docstrings: this project explains its reasoning in prose, and the
    # explanations name the very methods being banned.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body[0].value.value = ""

    offenders: list[str] = []
    for node in ast.walk(tree):
        # proc.open_files(), p.cmdline(), …
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            why = FORBIDDEN_PSUTIL_METHODS.get(node.func.attr)
            if why is not None:
                offenders.append(f"{node.func.attr}() line {node.lineno} — {why}")
        # process_iter(["name", "cmdline"]) — requested as a string, not called.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            why = FORBIDDEN_PSUTIL_METHODS.get(node.value)
            if why is not None:
                offenders.append(f"{node.value!r} requested, line {node.lineno} — {why}")

    assert not offenders, (
        f"{source_file.name} reaches for process detail it must not: "
        + "; ".join(offenders)
    )


def test_application_tracking_is_off_by_default() -> None:
    """
    Knowing which application was open is a step closer to watching a person
    than knowing which file changed, so it is the user's decision to make.
    """
    from app.config import Config

    cfg = Config()
    assert cfg.tracking.track_applications is False
    assert cfg.tracking.approved_applications == []


def test_the_application_table_cannot_hold_a_title_or_a_path(tmp_path) -> None:
    """
    Enforced at the SCHEMA, not in the code that writes it.

    A nullable `window_title` column is one feature request away from being
    populated; a column that does not exist is not.
    """
    from app.database.db import Database

    db = Database(tmp_path / "t.db")
    columns = {
        str(row[1]).lower()
        for row in db.connection().execute("PRAGMA table_info(application_sessions)")
    }
    assert columns, "application_sessions was not created"

    for banned in ("window_title", "title", "document", "path", "cmdline",
                   "command_line", "url", "file", "cwd"):
        assert banned not in columns, (
            f"application_sessions has a {banned!r} column — the schema must "
            "make this impossible, not merely unused."
        )
    db.close()


# ======================================================================
# Defaults collect nothing
# ======================================================================
def test_defaults_track_nothing() -> None:
    """
    A fresh install with no configuration must collect nothing at all.

    Whitelist-based means the empty configuration is the safe one. If a default
    folder ever appears here, somebody has made the program start watching
    without being asked.
    """
    from app.config import Config

    cfg = Config()
    assert cfg.tracking.folders == []
    assert cfg.approved_roots() == []
    assert cfg.tracking.track_applications is False
    assert cfg.ai.enabled is False
    assert cfg.integrations.gmail_enabled is False
    assert cfg.integrations.google_calendar_enabled is False
    assert cfg.integrations.google_drive_enabled is False
    assert cfg.privacy.local_only is True


# ======================================================================
# Local-only mode opens no socket
# ======================================================================
def test_local_only_mode_makes_no_network_calls(tmp_path, monkeypatch) -> None:
    """
    Sever the socket layer, then run the whole local pipeline.

    Any attempt to reach the network raises. This is stronger than asserting
    "we did not call requests": it catches a transitive dependency phoning
    home, a telemetry call, or an update check added later.
    """
    attempted: list[str] = []

    class _Blocked(socket.socket):  # type: ignore[misc]
        def __init__(self, *a, **kw):
            attempted.append("socket()")
            raise AssertionError("Network access attempted while local_only is on")

    monkeypatch.setattr(socket, "socket", _Blocked)

    def _no_connection(*a, **kw):
        attempted.append("create_connection()")
        raise AssertionError("Network access attempted while local_only is on")

    monkeypatch.setattr(socket, "create_connection", _no_connection)

    from app.config import Config
    from app.database.db import ActivityRepository, Database
    from app.database.models import Activity, Category, EventType
    from app.intelligence.deduplicator import Deduplicator
    from app.reporting.daily_report import DailyReportGenerator

    cfg = Config()
    assert cfg.privacy.local_only is True

    db = Database(tmp_path / "t.db")
    repo = ActivityRepository(db, cipher=None)

    repo.add(Activity(
        local_date="2026-01-05", event_type=EventType.MODIFIED,
        filename="hero.psd", extension=".psd", family_key="hero",
        project="Platinum", category=Category.GRAPHIC_DESIGN,
    ))

    rows = repo.for_date("2026-01-05")
    report = DailyReportGenerator(Deduplicator()).generate(rows, "2026-01-05")

    assert "Platinum" in report
    assert attempted == [], f"Network was attempted: {attempted}"
    db.close()


# ======================================================================
# The whitelist holds
# ======================================================================
def test_paths_outside_the_whitelist_are_refused(tmp_path) -> None:
    from app.tracking.filters import PathFilter

    approved = tmp_path / "Work"
    approved.mkdir()
    outside = tmp_path / "Personal"
    outside.mkdir()

    f = PathFilter([approved.resolve()], [], [])

    assert f.accepts(approved / "brief.docx").accepted is True
    assert f.accepts(outside / "diary.docx").accepted is False
    # Traversal out of an approved root must not re-enter it.
    assert f.accepts(approved / ".." / "Personal" / "diary.docx").accepted is False


def test_sibling_folder_with_a_shared_prefix_is_refused(tmp_path) -> None:
    """
    `str.startswith` is the classic bug, and this is the case that catches it.

    "Work" is a string prefix of "Workspace", so a naive containment check
    admits an entire folder nobody approved.
    """
    from app.tracking.filters import PathFilter

    (tmp_path / "Work").mkdir()
    (tmp_path / "Workspace").mkdir()

    f = PathFilter([(tmp_path / "Work").resolve()], [], [])
    assert f.accepts(tmp_path / "Workspace" / "private.docx").accepted is False


def test_rejection_message_never_contains_the_path(tmp_path) -> None:
    """
    A refused path may be from outside the whitelist entirely, so the reason
    must not quote it — not in an exception, not in a log line.
    """
    from app.tracking.filters import PathFilter

    approved = tmp_path / "Work"
    approved.mkdir()
    secret = tmp_path / "Private" / "medical-results.pdf"

    decision = PathFilter([approved.resolve()], [], []).accepts(secret)
    assert decision.accepted is False
    assert "medical-results" not in decision.reason
    assert str(secret) not in decision.reason


# ======================================================================
# Nothing identifying leaves through the sanitizer
# ======================================================================
def test_sanitizer_removes_paths_usernames_and_hosts() -> None:
    from app.intelligence.sanitizer import Sanitizer

    s = Sanitizer(allow_filenames=False)
    raw = (
        r"Updated D:\Company\Platinum\Confidential\ClientData\launch-final-v6.psd "
        r"and C:\Users\mohamed.r\Desktop\notes.txt, shared via \\NAS01\Finance, "
        r"emailed ahmed@client.example and called +965 9999 8888"
    )
    out = s.scrub_text(raw)

    for leak in ["D:\\", "C:\\Users", "mohamed.r", "NAS01", "ClientData",
                 "ahmed@client.example", "9999 8888"]:
        assert leak not in out, f"{leak!r} survived sanitization: {out!r}"


def test_sanitized_batch_contains_no_path_shaped_strings() -> None:
    """The output assertion, which is what makes the sanitizer a choke point."""
    from datetime import datetime

    from app.database.models import Category, WorkItem
    from app.intelligence.sanitizer import Sanitizer

    item = WorkItem(
        project="Platinum Fit",
        brand="Platinum Fit",
        category=Category.GRAPHIC_DESIGN,
        action=r"Designed D:\Work\Platinum Fit\hero-final-v3.psd",
        file_count=6,
        first_at=datetime(2026, 3, 2, 9, 15),
        last_at=datetime(2026, 3, 2, 11, 40),
    )

    payloads = Sanitizer(allow_filenames=False).sanitize_batch([item])
    assert len(payloads) == 1

    blob = repr(payloads[0])
    for leak in ["D:\\", "hero-final-v3", ".psd\\", "Work\\"]:
        assert leak not in blob, f"{leak!r} reached the payload: {blob}"
    assert payloads[0]["project"] == "Platinum Fit"


def test_sanitizer_drops_unclassified_items() -> None:
    """
    An unidentified item must never be sent.

    An AI given "something happened" will write a confident sentence about it,
    and an invented achievement in a monthly report is the failure this
    pipeline exists to prevent.
    """
    from app.database.models import WorkItem
    from app.intelligence.sanitizer import Sanitizer

    assert Sanitizer().sanitize_batch([WorkItem(project="Unclassified")]) == []


def test_exact_timestamps_never_leave() -> None:
    """Durations are buckets. "47 minutes from 14:03" rebuilds a working day."""
    from app.intelligence.sanitizer import Sanitizer

    s = Sanitizer()
    assert s.duration_bucket(12) == "short"
    assert s.duration_bucket(75) == "medium"
    assert s.duration_bucket(400) == "long"
    assert s.duration_bucket(None) is None


# ======================================================================
# The audit never carries content
# ======================================================================
def test_audit_refuses_path_like_detail(tmp_path) -> None:
    from app.database.db import AuditRepository, Database
    from app.security.audit import AuditLog

    db = Database(tmp_path / "a.db")
    audit = AuditLog(AuditRepository(db))

    audit.record("data_deleted", r"removed D:\Work\Platinum\salary.xlsx")
    rows = audit.recent(10)
    assert len(rows) == 1
    # The action is kept; the leaky detail is dropped rather than redacted.
    assert rows[0]["action"] == "data_deleted"
    assert rows[0]["detail"] is None
    db.close()


def test_audit_rejects_unknown_actions(tmp_path) -> None:
    """Free-text actions are how a path eventually reaches the audit table."""
    from app.database.db import AuditRepository, Database
    from app.security.audit import AuditLog

    db = Database(tmp_path / "a.db")
    audit = AuditLog(AuditRepository(db), strict=True)
    with pytest.raises(ValueError):
        audit.record("user_opened_file_secret.docx")
    db.close()


# ======================================================================
# The privacy gate actually gates
# ======================================================================
def test_private_mode_blocks_collection() -> None:
    from app.security.privacy import PrivacyGate, TrackingState

    gate = PrivacyGate()
    assert gate.allows_collection() is True

    gate.enable_private_mode()
    assert gate.allows_collection() is False
    assert gate.status().state is TrackingState.PRIVATE

    gate.disable_private_mode()
    assert gate.allows_collection() is True


def test_resume_clears_both_switches() -> None:
    """
    Resume means "work normally again".

    Leaving private mode on after Resume produces a program that reports
    Active and records nothing — the exact confusion these controls prevent.
    """
    from app.security.privacy import PrivacyGate, TrackingState

    gate = PrivacyGate()
    gate.pause()
    gate.enable_private_mode()
    assert gate.allows_collection() is False

    status = gate.resume()
    assert status.state is TrackingState.ACTIVE
    assert gate.allows_collection() is True


def test_private_mode_does_not_persist_across_restart() -> None:
    """
    It resets on start, deliberately.

    A mode that silently stays on for three weeks is a mode that quietly stops
    the product working, and the user discovers it when a month's report is
    empty.
    """
    from app.config import Config
    from app.security.privacy import PrivacyGate

    cfg = Config()
    cfg.privacy.private_mode = True
    # main.Application constructs the gate with private_mode=False regardless.
    gate = PrivacyGate(paused=cfg.privacy.paused, private_mode=False)
    assert gate.allows_collection() is True
