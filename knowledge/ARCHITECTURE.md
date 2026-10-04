# Architecture

## The shape

```
Approved folders (whitelist)
        │
        ▼
  watchdog events
        │
   ┌────┴─────────────────────────────┐
   │  1. privacy gate   paused/private?│   ← nothing past here when closed
   │  2. whitelist      inside a root?  │   ← nothing past here learns the path
   │  3. exclusions     temp? .git?     │
   │  4. debounce       seen recently?  │
   └────┬─────────────────────────────┘
        ▼
   classify (project · brand · category)
        ▼
   encrypt path  ──────────────► SQLite (WAL)
        ▼
   deduplicate → work items
        ▼
   ┌────────────────┬──────────────────┐
   │ local report   │  sanitize        │
   │ (Markdown)     │      ▼           │
   │ no AI needed   │  AI provider     │  ← optional, off by default
   └────────────────┴──────────────────┘
```

The vertical order of the first box is the security design. See
[SECURITY.md](SECURITY.md).

---

## Why these choices

**SQLite, not a server.** One user, one machine, a few hundred rows a day. A
Postgres dependency would mean a service to install, run and keep patched for a
workload that fits comfortably in a file.

**WAL mode.** The tracker writes from watchdog's observer thread while the UI
reads on the main one. In the default rollback journal those block each other,
and a blocked writer inside a filesystem callback means watchdog's queue backs
up behind a UI repaint.

**`synchronous=NORMAL`, not `FULL`.** With WAL this is durable across
application crashes and risks only the last transactions on a power cut. `FULL`
would `fsync` on every file the person saves. For activity metadata that trade
is obviously right.

**A short `busy_timeout`.** If the database is locked, dropping one activity is
better than holding up a watchdog callback. The person's file save must never
wait on this program.

**One connection per thread.** SQLite connections are not thread-safe, and the
usual workaround — `check_same_thread=False` plus a lock — turns every read into
a queue behind every write.

**Deterministic grouping before AI.** The step that decides what a report is
*about* must be reproducible. An AI asked to group five logo files would
sometimes be cleverer and sometimes merge the Platinum logo with the Miss
Platinum logo because the names rhyme. The AI's job starts afterwards, at
wording.

**The local report needs no AI at all.** The default configuration has AI off,
so the Markdown generator has to produce something sendable from the database
alone. A report that needs a key to exist is a report that stops existing when
the key expires.

---

## Modules

| Module | Responsibility |
|---|---|
| `config.py` | The whitelist and every default. Fails closed on a corrupt file. |
| `database/db.py` | Connections, WAL, repositories. The only module writing SQL. |
| `database/migrations.py` | Versioned schema, so an upgrade keeps six months of history. |
| `security/encryption.py` | DPAPI, with a Fernet fallback that says it is weaker. |
| `security/privacy.py` | The gate. One place answers "may I collect this?". |
| `security/audit.py` | A closed vocabulary of actions; drops path-like detail. |
| `security/retention.py` | Age-based deletion, audited by count. |
| `tracking/filters.py` | Containment, exclusions, debounce, file families. |
| `tracking/filesystem_tracker.py` | The only module touching the outside world. |
| `intelligence/classifier.py` | Extension → discipline, path → project. No AI. |
| `intelligence/deduplicator.py` | Events → work items. No AI. |
| `intelligence/sanitizer.py` | The choke point before anything could leave. |
| `reporting/daily_report.py` | Markdown, locally, with no AI. |
| `main.py` | The CLI. Every privacy control is reachable from it. |

---

## Why a CLI before a GUI

Every privacy control must be reachable and scriptable before it is wrapped in a
window. A control that only exists behind a GUI cannot be tested, cannot be
automated, and cannot be verified by anyone who does not trust the GUI.

The Phase 2 desktop app calls exactly the functions in `main.py`.

---

## Phases

**Phase 1 (this)** — structure, config, database, encryption, filesystem
tracking, privacy gate, classification, deduplication, sanitizer, retention,
audit, daily report, CLI, tests.

Note that the privacy controls and the sanitizer are in Phase 1, not deferred.
They are the spine; building the features first and the controls later produces
a program whose controls are retrofitted around what was convenient.

**Phase 2** — PySide6 desktop app, work sessions, application tracking,
dashboard, weekly report.

**Phase 3** — AI provider abstraction behind the sanitizer, Gmail, Calendar,
Drive, reconciliation against local activity.

**Phase 4** — monthly report pipeline, evidence viewer, analytics, installer,
Task Scheduler registration, hardening.

---

## The MCP question

A Skill is **not** a connector. Publishing these skills does not give any agent
access to this Windows machine — a skill teaches an agent how to behave when
data arrives, and the data has to get there some other way.

The intended path, when it is built:

```
Windows tracker → local SQLite → sanitizer → local MCP server → agent → skills → report
```

The MCP server is the piece that does not exist yet, and it is deliberately
*after* the sanitizer in that chain. Nothing reaches an agent that has not
already been stripped.

---

## Reliability

- Survives restart — state is in SQLite, not in memory.
- Recovers from crashes — WAL replays; no manual repair step.
- Handles unavailable folders — a disconnected drive warns and is skipped,
  never fatal.
- Handles locked files — `stat` is wrapped; a file being written by Premiere is
  routine.
- Never blocks a file edit — short timeouts, and nothing opens a tracked file.
- Never modifies a monitored file — nothing in the tracker writes.
- Refuses to watch its own data directory — otherwise each write produces an
  event, which produces a write.
- Exceptions never escape a watchdog callback — one that does kills the observer
  thread, after which the program looks healthy and tracks nothing.
