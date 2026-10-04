# Mohamed Work Tracker Agent

Turns real work activity on a Windows machine into daily, weekly and monthly
work reports — without manual task entry, and without behaving like employee
surveillance software.

> **Your raw activity stays on this device.**

---

## What it does

Watches **folders you explicitly approve**, notices that files were created,
modified, moved or renamed, works out which project and discipline that belongs
to, collapses thirty saves of one Photoshop file into one piece of work, and
writes a report you could send to a manager.

## What it never does

No keylogging. No screenshots. No screen recording. No microphone. No camera.
No clipboard. No browser history. No browser tabs. No reading of file contents.
No monitoring of anything outside the folders you added.

These are not settings. [`tests/test_security_guarantees.py`](tests/test_security_guarantees.py)
scans the source tree and **fails the build** if any of those capabilities
appears — including in a file nobody is reviewing closely. A promise in a README
survives about six months of feature work; a failing build survives indefinitely.

See [knowledge/SECURITY.md](knowledge/SECURITY.md) for the full contract.

---

## Requirements

- Windows 10 or 11
- **Python 3.11 or newer** — [python.org/downloads](https://www.python.org/downloads/)
  (tick *Add python.exe to PATH* during installation)

## Install

```powershell
cd mohamed-work-tracker-agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## First run

```powershell
# 1. See what is being tracked. On a fresh install: nothing.
python run.py status

# 2. Approve a folder. Nothing is watched until you do this.
python run.py folders add "D:\Work\Platinum"
python run.py folders add "D:\Work\Marketing"

# 3. Start tracking. Ctrl+C to stop.
python run.py watch

# 4. After a day's work, generate the report.
python run.py report
```

The report is printed and written to `reports/daily-YYYY-MM-DD.md`.

---

## Commands

| Command | What it does |
|---|---|
| `status` | Everything being collected, in one screen |
| `watch` | Start tracking (foreground) |
| `pause` / `resume` | Stop and restart collection |
| `private on` / `private off` | Private Mode — stronger, resets on restart |
| `report [--date YYYY-MM-DD]` | Generate a daily report |
| `weekly [--date …]` | The Sunday-to-Saturday review |
| `monthly [--date …]` | The management-ready monthly report |
| `sessions [--date …]` | Rebuild and show the day's sittings |
| `folders list\|add\|remove` | Manage the whitelist |
| `retention preview\|run` | See or run the deletion sweep |
| `delete-day [--date …]` | Delete one day's activity |
| `audit` | The privacy audit trail |

---

## Configuration

`config.json` is created on first save. The defaults collect **nothing**:

```json
{
  "tracking": {
    "folders": [],
    "track_applications": false,
    "debounce_seconds": 90,
    "session_timeout_minutes": 15
  },
  "privacy": {
    "local_only": true,
    "allow_file_content_reading": false,
    "allow_filenames_in_ai_payloads": false
  },
  "retention": {
    "raw_activity_days": 180,
    "audit_log_days": 30,
    "reports_days": 0
  },
  "ai": { "enabled": false, "provider": "none" },
  "project_rules": [
    { "match": "platinum fit", "project": "Platinum Fit", "brand": "Platinum Fit" },
    { "match": "platinum",     "project": "Platinum",     "brand": "Platinum" }
  ]
}
```

The **longest** matching rule wins, so `platinum fit` beats `platinum`
regardless of the order they appear in. See
[knowledge/PROJECT_MAPPING.md](knowledge/PROJECT_MAPPING.md).

---

## Verifying the privacy claims

Do not take the README's word for it:

```powershell
pytest tests/test_security_guarantees.py -v
```

That suite asserts, among other things, that no module imports a keylogging or
screen-capture library, that the filesystem tracker contains no `open()` call,
that a path outside the whitelist is refused and never appears in the rejection
message, that the plaintext path never reaches the database file, and that the
whole local pipeline runs with the socket layer severed.

```powershell
pytest                      # everything
```

---

## Skills

Four skills in [`.github/skills/`](.github/skills/), kept separate on purpose —
one large skill is one large thing to get wrong:

| Skill | Job |
|---|---|
| `work-activity-analyzer` | Raw events → work items |
| `monthly-report-generator` | Work items → a management-ready report |
| `privacy-sanitizer` | The contract for what may leave the device |
| `project-classifier` | Activity → project and brand, or an honest `Unclassified` |

**A Skill is not a connector.** Publishing these does not give any agent access
to this machine. A skill teaches an agent how to behave when data arrives; the
data still has to get there — see the MCP note in
[knowledge/ARCHITECTURE.md](knowledge/ARCHITECTURE.md).

---

## Status

**Phase 1 is complete and verified on Windows**: configuration, database,
encryption (DPAPI), filesystem tracking, the privacy gate, classification,
deduplication, the sanitizer, retention, the audit trail, work sessions, and
daily, weekly and monthly reports — with 124 passing tests.

The privacy controls are in Phase 1 rather than deferred. Building the features
first and the controls later produces a program whose controls are retrofitted
around whatever was convenient.

**Phase 2** — PySide6 desktop app, application tracking, dashboard.
Work sessions, the weekly review and the monthly report are done.
**Phase 3** — AI provider abstraction behind the sanitizer, Gmail, Calendar, Drive.
**Phase 4** — monthly pipeline, evidence viewer, installer, Task Scheduler.

---

## Licence

Private. Not for distribution.
