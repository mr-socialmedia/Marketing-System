"""
Start with Windows — visibly, and without ever asking for Administrator.

THE STARTUP FOLDER, NOT A SCHEDULED TASK AND NOT A REGISTRY KEY.

The first version used `schtasks /SC ONLOGON /RL LIMITED`, which is the
textbook answer. On the machine this was built for it returns "Access is
denied": the policy requires elevation even to register a task that runs as the
current user with no elevation. A feature that needs an administrator to enable
is a feature most people never enable.

So it writes a `.cmd` file into the per-user Startup folder instead, and that
turns out to be the better mechanism for this program rather than merely the
one that works:

  * NO ELEVATION, EVER. The folder is inside the user's own profile.
  * IT IS VISIBLE. `shell:startup` in Explorer shows a file the person can read
    and delete. A tracker that can only be disabled from inside itself is one
    you have to trust; a tracker you can switch off with the Delete key is one
    you can check. That matters more here than it would for most software.
  * NOTHING MACHINE-WIDE. No HKLM, no service, no SYSTEM account, no effect on
    any other user of the computer.

The registry Run key under HKCU would also avoid elevation, and is rejected for
the one reason above: it is invisible to anyone who does not know to look in
regedit.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from pathlib import Path

log = logging.getLogger(__name__)

ENTRY_NAME = "MohamedWorkTracker.cmd"


class AutostartError(RuntimeError):
    pass


def startup_dir() -> Path:
    """
    The current user's Startup folder.

    Read from APPDATA rather than hard-coded: the profile is not always under
    C:\\Users on a domain machine with folder redirection, and a hard-coded
    path there would write the file somewhere Windows never looks.
    """
    if platform.system() != "Windows":
        raise AutostartError("Autostart is Windows-only.")

    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise AutostartError("APPDATA is not set; cannot locate the Startup folder.")

    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def entry_path() -> Path:
    return startup_dir() / ENTRY_NAME


def is_registered() -> bool:
    """False rather than raising off Windows, so callers need no guard."""
    if platform.system() != "Windows":
        return False
    try:
        return entry_path().exists()
    except AutostartError:
        return False


def register(project_root: Path | None = None) -> Path:
    """
    Write the startup entry. Returns the file it wrote.

    Uses `pythonw.exe` where it exists so no console window appears at logon —
    a black window flashing up on every login is how a useful background tool
    gets uninstalled in week two.
    """
    root = (project_root or Path(__file__).resolve().parent.parent.parent).resolve()
    script = root / "run.py"
    if not script.exists():
        raise AutostartError(f"Cannot find {script}")

    runner = Path(sys.executable)
    windowless = runner.with_name("pythonw.exe")
    if windowless.exists():
        runner = windowless

    target = entry_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AutostartError(f"Cannot write to the Startup folder: {exc}") from exc

    # Every path is quoted: both the interpreter and the script sit under a
    # user profile, which contains a space on almost every machine. `start ""`
    # launches without holding a console open, and the empty string is the
    # window TITLE argument — omitting it makes `start` treat the quoted path
    # as the title and silently do nothing.
    content = (
        "@echo off\r\n"
        "rem Mohamed Work Tracker — starts tracking at logon.\r\n"
        "rem Delete this file to stop it, or run: python run.py autostart-off\r\n"
        f'cd /d "{root}"\r\n'
        f'start "" "{runner}" "{script}" watch\r\n'
    )

    try:
        # newline="" so the \r\n above survive; Python would otherwise
        # translate them and produce \r\r\n, which cmd mishandles.
        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
    except OSError as exc:
        raise AutostartError(f"Could not write {target}: {exc}") from exc

    log.info("Registered autostart at %s", target)
    return target


def unregister() -> bool:
    """Remove the entry. True if one was there to remove."""
    target = entry_path()
    if not target.exists():
        return False
    try:
        target.unlink()
    except OSError as exc:
        raise AutostartError(f"Could not remove {target}: {exc}") from exc
    log.info("Removed autostart entry %s", target)
    return True
