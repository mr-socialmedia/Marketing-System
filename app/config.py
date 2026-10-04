"""
Configuration, and the whitelist that is the whole security model.

THE WHITELIST IS NOT A FILTER — IT IS THE BOUNDARY.

This agent does not monitor the computer and then exclude things. It monitors
nothing until a folder is explicitly added, and a path that is not inside an
approved root is never seen by any other module. The difference matters: a
blacklist that is missing an entry leaks; a whitelist that is missing an entry
merely tracks less than it could.

Every default here is the quiet one. Application tracking is off. AI is off.
Every integration is off. Nothing reads file contents. A fresh install with no
configuration collects nothing at all, and the user has to decide, folder by
folder, what this program is allowed to notice.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)

APP_NAME = "mohamed-work-tracker"

# The project root, so a portable install keeps its data beside itself rather
# than scattering it through the user profile.
ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG_PATH = ROOT / "config.json"


class ProjectRule(BaseModel):
    """One mapping from a path or filename fragment to a project."""

    match: str = Field(..., min_length=1, description="Case-insensitive substring matched against the path")
    project: str = Field(..., min_length=1)
    brand: str | None = None
    category: str | None = None

    @field_validator("match")
    @classmethod
    def _normalise(cls, v: str) -> str:
        return v.strip().lower()


class RetentionConfig(BaseModel):
    """
    How long anything is kept.

    Raw activity ages out; reports do not, because a report is something the
    person wrote and may need years later, while a raw event is scaffolding.
    `reports_days = 0` means keep forever, which is the default and the only
    unbounded value in this file.
    """

    raw_activity_days: int = Field(180, ge=1, le=3650)
    audit_log_days: int = Field(30, ge=1, le=3650)
    reports_days: int = Field(0, ge=0, le=3650)


class TrackingConfig(BaseModel):
    """
    What may be watched.

    `folders` empty means nothing is tracked. That is a valid, working, and
    deliberately useless state — the program starts, says it is watching
    nothing, and waits.
    """

    folders: list[str] = Field(default_factory=list)

    # OFF BY DEFAULT, per the brief. Knowing which application was open is a
    # step closer to watching a person than knowing which file changed, so it
    # is opt-in even though it would make the reports better.
    track_applications: bool = False
    approved_applications: list[str] = Field(default_factory=list)

    # Collapse repeated saves of the same file. Thirty saves of a PSD over an
    # afternoon is one piece of work, not thirty.
    debounce_seconds: int = Field(90, ge=1, le=3600)

    # Gap after which a new work session begins.
    session_timeout_minutes: int = Field(15, ge=1, le=240)

    ignored_extensions: list[str] = Field(
        default_factory=lambda: [
            ".tmp", ".temp", ".lock", ".bak", ".crdownload", ".partial",
            ".cache", ".db-journal", ".swp", ".~lock", ".asd", ".wbk",
        ]
    )
    ignored_directories: list[str] = Field(
        default_factory=lambda: [
            "node_modules", ".git", ".svn", ".hg", "appdata", "temp", "tmp",
            "cache", "venv", ".venv", "__pycache__", ".idea", ".vscode",
            "$recycle.bin", "system volume information", ".next", "dist",
            "build", ".pytest_cache", "obj", "bin",
        ]
    )

    @field_validator("ignored_extensions")
    @classmethod
    def _lower_ext(cls, v: list[str]) -> list[str]:
        return [e.strip().lower() for e in v if e.strip()]

    @field_validator("ignored_directories")
    @classmethod
    def _lower_dirs(cls, v: list[str]) -> list[str]:
        return [d.strip().lower() for d in v if d.strip()]


class PrivacyConfig(BaseModel):
    """
    The switches that stop collection, and the one that stops the network.

    `local_only` is the strongest statement this program makes. When it is
    true, no module may open a socket for any reason — not telemetry, not
    update checks, not AI. `tests/test_security_guarantees.py` asserts it by
    severing the socket layer and running the tracker, so it is a property of
    the build rather than a promise in a README.
    """

    local_only: bool = True
    paused: bool = False
    private_mode: bool = False

    # Reading a file's bytes is categorically different from noticing that it
    # changed. It stays off, it is per-feature when it ever arrives, and no
    # Phase 1 code path consults it.
    allow_file_content_reading: bool = False

    # Filenames can be sensitive ("resignation-draft.docx"). Off means the
    # sanitizer replaces them with their extension and family before anything
    # leaves this machine.
    allow_filenames_in_ai_payloads: bool = False


class AIConfig(BaseModel):
    """Disabled. Present so the shape is fixed before anything implements it."""

    enabled: bool = False
    provider: Literal["none", "openai", "gemini", "local"] = "none"
    model: str | None = None
    # Never a key. Keys live in the OS credential store; see security/encryption.
    api_key_ref: str | None = None


class IntegrationsConfig(BaseModel):
    gmail_enabled: bool = False
    google_calendar_enabled: bool = False
    google_drive_enabled: bool = False


class Config(BaseModel):
    """The whole configuration. Serialised to config.json, never to the DB."""

    version: int = 1
    tracking: TrackingConfig = Field(default_factory=TrackingConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    project_rules: list[ProjectRule] = Field(default_factory=list)

    database_path: str = str(ROOT / "data" / "tracker.db")
    reports_dir: str = str(ROOT / "reports")
    log_dir: str = str(ROOT / "logs")

    # ------------------------------------------------------------------
    def approved_roots(self) -> list[Path]:
        """
        The monitored folders, resolved and de-duplicated.

        Resolution happens HERE and nowhere else, so every containment check in
        the program compares real paths. A check against an unresolved path is
        defeated by `D:\\Work\\..\\Other`, and symlinks make the same trick
        work without any `..` in sight.
        """
        roots: list[Path] = []
        for raw in self.tracking.folders:
            try:
                p = Path(raw).expanduser().resolve(strict=False)
            except (OSError, RuntimeError) as exc:
                log.warning("Skipping unusable monitored folder %r: %s", raw, exc)
                continue
            if p not in roots:
                roots.append(p)
        return roots

    def is_collecting(self) -> bool:
        """False whenever anything says stop. Checked before every write."""
        return not (self.privacy.paused or self.privacy.private_mode)


# ----------------------------------------------------------------------
# Load / save
# ----------------------------------------------------------------------
def load_config(path: Path | str | None = None) -> Config:
    """
    Read config.json, or return safe defaults.

    A corrupt or unreadable file yields DEFAULTS rather than an exception, and
    defaults track nothing. Failing closed here matters: the alternative is a
    program that crashes on startup after a bad edit and gets "fixed" by
    someone loosening the parser.
    """
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        log.info("No config at %s; starting with defaults (tracking nothing).", cfg_path)
        return Config()

    try:
        raw: Any = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Config at %s is unreadable (%s). Using defaults.", cfg_path, exc)
        return Config()

    try:
        return Config.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError and anything odd
        log.error("Config at %s is invalid (%s). Using defaults.", cfg_path, exc)
        return Config()


def save_config(config: Config, path: Path | str | None = None) -> None:
    """
    Write config.json atomically.

    Via a temporary file and os.replace: a half-written config is a config that
    parses as garbage, and the loader above would then silently fall back to
    defaults — which would look exactly like the user's monitored folders
    having been wiped.
    """
    import os
    import tempfile

    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump_json(indent=2)

    fd, tmp = tempfile.mkstemp(dir=str(cfg_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, cfg_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
