---
name: work-activity-analyzer
description: Analyze approved professional device activity, deduplicate repetitive events, group them into meaningful work sessions, and convert them into structured work items for daily, weekly, and monthly reporting.
---

# Work Activity Analyzer

Use this skill whenever raw work activity, file metadata, application activity, or project events need to be interpreted into work items.

Do **not** use it to write a finished report — that is `monthly-report-generator`. This skill produces the structured items that one consumes.

## Goal

Turn repetitive low-level events into a small number of meaningful professional work items.

Raw events:

```
logo-v1.psd          modified  09:12
logo-v2.psd          modified  09:31
logo-v3.psd          modified  09:48
logo-final.psd       modified  10:14
logo-final-final.psd modified  10:35
Photoshop active throughout
```

One work item:

```
Designed and revised the Platinum campaign logo (5 files, 5 revisions)
```

Five rows, one morning, one thing done.

## Rules

**Never treat every file save as a separate task.** The agent's local debouncer already collapses repeated saves of the same file; your job is the next level up — collapsing a *family* of related files into the piece of work they belong to.

**Group by all five signals, not one.** Project, file family (version-stripped name), category, parent folder, and time proximity. Any single signal on its own produces wrong groupings: time alone merges two projects worked on in the same hour; name alone merges the Platinum logo with the Miss Platinum logo.

**Deterministic grouping has already happened. Respect it.** The local pipeline groups before you ever see the data. Do not regroup from scratch; refine wording, merge items that are obviously the same work described twice, and split one that clearly contains two deliverables.

**Never invent completion.** Nothing in a file event means a task finished. A file stops changing when it is done, when it is abandoned, and when the person goes on holiday. Use `in_progress` unless there is explicit evidence — a final export, an approval email, a calendar event that happened.

**Unclassified is a valid answer.** If the project cannot be identified, say `Unclassified` and set a low confidence. Do not guess the most frequent project. A confident wrong brand is a correction in front of a director; an honest gap is a prompt to add a mapping rule.

**Keep evidence references.** Every item carries the ids it was built from, so a claim can be traced back.

**Weight by repetition, not by file count.** Twenty revisions of one file is more work than one save of twenty files. `repeat_count` carries that signal.

## Privacy

You will receive sanitized data. Never request, infer, or reconstruct:

- keystrokes, passwords, clipboard contents
- screenshots, screen recordings, microphone, camera
- browser history, browser tabs, private messages
- file contents
- full local file paths, usernames, hostnames
- exact timestamps beyond the date and a duration bucket

If a payload arrives containing any of the above, **stop and report it as a sanitizer failure.** Do not process it and do not repeat the value back.

Only approved work activity reaches you. There is no step where asking for more is correct.

## Output

Return structured items:

```json
{
  "project": "Platinum Fit",
  "brand": "Platinum Fit",
  "category": "Graphic Design",
  "action": "Designed and revised campaign key visuals",
  "session": "2026-03-02, medium",
  "status": "in_progress",
  "evidence": ["activity:1182", "activity:1187"],
  "confidence": 0.82
}
```

`status` is one of `in_progress`, `needs_follow_up`, or `completed` — and `completed` requires stated evidence.

`confidence` below 0.4 must be flagged in the output so the report can mark it rather than present it as fact.

Never output a raw local file path. If one would make the item clearer, say so and let the user decide; do not include it unprompted.
