---
name: privacy-sanitizer
description: Strip local file paths, usernames, hostnames and other identifiers from work activity before it reaches any external AI service, and refuse to process anything that still contains them.
---

# Privacy Sanitizer

Use this skill before any work activity is sent to an external service, and whenever you need to check whether a payload is safe to send.

It is a **gate**, not a cleanup pass. Its job is as much to refuse as to transform.

## Goal

What the device knows:

```
D:\Company\Platinum\App\Confidential\ClientData\launch-final-v6.psd
C:\Users\mohamed.r\Desktop\notes.txt
\\NAS01\Finance\Q1\budget.xlsx
modified 14:03:22, 14:09:51, 14:31:08 …
```

What an AI service may receive:

```
Project:   Platinum App
Category:  Graphic Design
Action:    Creative revision
Files:     1
Date:      2026-03-02
Duration:  medium
```

Everything in the first block that is not in the second is removed because it identifies a person, a client, a machine or a working pattern — and none of it is needed to write a sentence about the work.

## What is removed, and why each one

| Removed | Why |
|---|---|
| Full paths | `...\Confidential\ClientData\...` describes an org chart and a client list |
| Drive letters, UNC shares | Names infrastructure |
| Usernames | `C:\Users\mohamed.r` identifies a person, and appears in nearly every Windows path |
| Hostnames, machine names | Identifies a device |
| Filenames (by default) | `termination-letter-ahmed.docx` discloses a third party who agreed to none of this |
| Email addresses, phone numbers | Sometimes present in exported asset names |
| Exact timestamps | `47 minutes from 14:03` reconstructs somebody's afternoon |

## What survives

Project, brand, category, action description, file counts, session counts, the **date** (not the time), a duration **bucket** (`short` / `medium` / `long`), and file **extensions**.

That is enough to write "Produced campaign visuals for Platinum Fit" and not enough to reconstruct anything about the machine, the client, or the people involved.

## Rules

**Fail closed.** If a payload has a shape you do not recognise, drop it. A sanitizer that forwards what it does not understand is an allowlist pretending to be a denylist.

**Check the output, not just the input.** After transforming, scan the result again for path-shaped text. The local implementation does exactly this in `sanitize_batch`, and the reason is that a field added later can carry a path through a transform that was written before it existed.

**Drop, do not redact, in the audit trail.** `[redacted]` still says "something was here". For an audit line the right answer is no detail at all.

**An unidentified item is not sent.** If the project is `Unclassified`, drop it. An AI given "something happened" will write a confident sentence about it, and an invented achievement in a monthly report is the exact failure this pipeline exists to prevent.

**Filenames are off unless the user turned them on.** There is one switch, `allow_filenames_in_ai_payloads`, it defaults to false, and it is the user's decision made once and visibly. Do not reason your way past it because a filename "would obviously be fine here".

**Never reconstruct what was removed.** If you can infer the client from the project name plus the category, that is the user's own vocabulary and is fine. If you find yourself inferring a path, a person or a machine, stop.

## Refusal

If you are handed data that still contains a path, a username or a raw timestamp:

1. Do not process it.
2. Do not repeat the value back in your response — that puts it in a transcript.
3. Report it as a sanitizer failure, naming the **field**, not the value.

Example: `Field "action" still contained a Windows path. Not processed.`

A sanitizer bug that is quietly worked around is a sanitizer bug that stays.

## Note on where this runs

The authoritative sanitizer is **local**, in `app/intelligence/sanitizer.py`, and it runs before anything leaves the device. This skill describes the same contract so that an agent receiving the data behaves consistently with it — and so that a payload which should never have arrived is recognised as a bug rather than processed.

Nothing in this skill is a substitute for the local one. If the two ever disagree, the local one is right.
