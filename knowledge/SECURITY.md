# Security and Privacy Contract

This document is the contract. If the code and this file disagree, that is a bug
in the code.

## The one sentence

**Your raw activity stays on this device.**

Everything below is the detail behind that sentence.

---

## What this agent never does

These are not configuration options. There is no setting that enables them, and
`tests/test_security_guarantees.py` fails the build if the capability appears in
the source tree at all.

| Never | Enforced by |
|---|---|
| Record keystrokes | No keyboard-hook library may be imported |
| Capture passwords | Same — there is no input capture of any kind |
| Read the clipboard | No clipboard library may be imported |
| Take screenshots | No screen-capture library may be imported |
| Record the screen | Same |
| Use the microphone | No audio-capture library may be imported |
| Use the camera | No video-capture library may be imported |
| Read browser history | No browser-history library may be imported |
| Collect browser tab titles | Nothing reads window titles |
| Inspect messaging apps | Nothing reads another process's content |
| Read file contents | The tracker has no `open()` call; a test asserts it |
| Monitor the whole computer | Whitelist only — see below |
| Upload raw activity | Local-only by default; a test severs the socket layer |
| Send full paths to an AI | The sanitizer is a choke point, and asserts its own output |

The test suite is the enforcement. A promise in a README survives about six
months of feature work; a failing build survives indefinitely.

---

## The whitelist is the boundary

This agent does not monitor the computer and then exclude things. It monitors
**nothing** until a folder is explicitly added.

A blacklist that is missing an entry leaks. A whitelist that is missing an entry
simply tracks less than it could. That asymmetry is why the model is this way
round.

Containment is checked with `Path.is_relative_to` on **resolved** paths, never
with a string prefix. `"D:\Work"` is a string prefix of `"D:\Workspace"`, so a
`startswith` check silently admits a sibling folder nobody approved. There is a
test for exactly that case.

A path that fails the containment check is never logged, never counted with its
value, and never reaches any other module. Even the rejection message omits it,
because a path from outside the whitelist is the one value that must not be
written anywhere.

---

## The two privacy switches

**Pause** — "stop for now". Reversible, remembered across restarts, the normal
control for stepping into a meeting or working on something personal.

**Private Mode** — the stronger statement. Also reversible, but it **resets on
restart** rather than persisting. That is deliberate: a mode that silently stays
on for three weeks is a mode that quietly stops the product working, and the
user discovers it when a month's report comes back empty.

Both are enforced in the filesystem event handler, **before** the event is
examined. When a switch is closed, nothing downstream learns that a file
changed. A privacy switch honoured at write time has already lost — the path has
been read, classified, held in a list, and possibly logged on the way.

`Resume` clears both. Someone who presses Resume means "work normally again",
and leaving Private Mode on because it was set separately produces a program
that reports Active and records nothing.

---

## What is stored, and how

| Column | Stored as | Why |
|---|---|---|
| Full path | **Encrypted** | `...\Legal\termination-draft.docx` is a disclosure, not a filename |
| Filename | Plain | Needed for family grouping |
| Extension | Plain | Decides the discipline |
| Parent folder | **Leaf name only** | The chain is the sensitive part and is already in the encrypted path |
| Project, brand, category | Plain | Derived, non-sensitive, and what every query filters on |
| Timestamps | Plain, local | Needed to group a day |

**File contents are not stored, in any column, in any form.** There is no
`content` column, no `preview`, no `window_title`, no `url`. The absence is the
design: a schema with a nullable `window_title` is one feature request away from
being populated.

### Encryption

Windows **DPAPI** is preferred. `CryptProtectData` ties the ciphertext to the
logged-in Windows account, so another user on the same machine cannot read the
database even with the file in hand, and there is no key file to lose or back up
by accident. Reached through `ctypes`, so it costs no dependency.

**Fernet** is the fallback for tests and non-Windows machines. It is genuinely
weaker — the key sits beside the database, so it protects against a stolen
database file and little else. The code says so at WARNING level rather than
letting the word "encrypted" imply more than it delivers.

If a path cannot be encrypted, **the path is dropped** and the activity is
stored without it. Writing the plaintext "just this once" is how a database ends
up with a readable path nobody remembers putting there.

---

## The audit trail

Every privacy-relevant action is recorded: tracking started, paused, resumed;
Private Mode on and off; a folder added or removed; an integration enabled; data
deleted; a report generated.

**It records that a control was used. It never records what was being
protected.** An audit line reading "private mode enabled while editing
salary-review.xlsx" would leak exactly the thing Private Mode was turned on for.

The action vocabulary is a closed set, and a detail that looks like a path is
**dropped, not redacted** — `[redacted]` still says "something was here".

There is no "delete one audit entry" operation. Retention trims by age, as a
whole sweep. An audit you can edit a row at a time is not one.

---

## Retention

Retention is a security control, not housekeeping. Data that no longer exists
cannot be stolen, subpoenaed, or read by whoever gets the laptop next.

| | Default |
|---|---|
| Raw activity | 180 days |
| Audit logs | 30 days |
| Reports | Kept — a report is work the person wrote |

The sweep is audited by count. After a large sweep the database is `VACUUM`ed,
because space that is not returned to the filesystem makes "we deleted it" look
untrue to anyone checking the file size.

---

## The network

`local_only` defaults to **true**. When it is on, no module may open a socket for
any reason — not telemetry, not an update check, not AI.

This is asserted by a test that severs `socket.socket` and `socket.create_connection`
and then runs the full local pipeline. That is stronger than asserting "we did
not call `requests`": it catches a transitive dependency phoning home, and it
catches a call added later by somebody who did not read this file.

---

## The sanitizer

One choke point. Nothing may hand data to an AI provider, an HTTP client or an
export without passing through `app/intelligence/sanitizer.py` first.

The value of that is structural: one function to audit, one place to test, one
thing to read when somebody asks "what exactly would you send?".

It **checks its own output**, not just its input — so a field added later that
carries a path is caught before the wire rather than after.

It **drops** anything it does not recognise. A sanitizer that forwards what it
does not understand is an allowlist pretending to be a denylist.

---

## Reporting a concern

If you find a way to make this agent collect something on this list, that is a
security bug and not a feature request. The project's standing rule, from the
brief it was built to:

> If there is any conflict between functionality and privacy, choose privacy.
