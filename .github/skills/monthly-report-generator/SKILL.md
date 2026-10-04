---
name: monthly-report-generator
description: Turn structured work items into a management-ready monthly or weekly work report, grouped by discipline, written in professional language, with no invented achievements and no exaggeration.
---

# Monthly Report Generator

Use this skill when structured work items need to become a report a manager will read.

It consumes the output of `work-activity-analyzer`. It does not interpret raw events itself — if you were handed raw file activity, run that skill first.

## Goal

A monthly report is **not a raw activity log**. It is a short account of what the department produced, written so a director can read it in two minutes and know what happened.

**Bad** — a disk audit with a title:

```
Modified 17 PSD files.
Sent 6 emails to IT.
Opened Premiere Pro 23 times.
```

**Good** — the same month, described as work:

```
Developed and revised campaign creative assets for Platinum.
Coordinated with IT on application content and technical requirements.
Produced and edited promotional video content for the Q1 campaign.
```

The difference is not vocabulary. It is that the second describes **work**, and the first describes **software**.

## Structure

```markdown
# MONTHLY WORK REPORT — March 2026

## Creative & Design
- ...

## Video Production
- ...

## Digital & App
- ...

## Coordination
- ...

## Statistics
Projects worked on: 12
Unique files: 143
Work sessions: 37
```

Use only the sections that have content. An empty "Events" heading says the month was thin; omitting it says nothing, which is correct when nothing happened.

Section order: Creative & Design, Video Production, Digital & App, Website, Development, Events, Coordination, Documentation & Reporting, Research. Creative first because that is what most of these months are made of.

## Rules

**Never invent an achievement.** Every line must trace to a work item. If the items do not support a claim, the claim does not go in. This is the single most important rule in this skill: a monthly report is forwarded, quoted in reviews, and remembered.

**Never exaggerate.** "Led the campaign" when the evidence is six edited files is a lie that holds up until someone asks. Write "Produced campaign creative assets" — accurate, and still good work.

**Never claim completion without evidence.** Items arrive marked `in_progress`, `needs_follow_up` or `completed`. Carry that through. If an item says in progress, the report says the work was advanced, not delivered.

**Group by discipline, not by project.** A director reads the department's output, not a per-folder breakdown. Projects belong inside the lines ("for Platinum Fit"), not as the headings.

**Merge repetition across the month.** Nineteen days of logo revisions is one line, not nineteen. The analyzer has already merged within a day; this merges across days.

**Keep the statistics honest.** Count what is actually there. Do not round up, do not estimate hours, and do not report "focused work time" as if it were measured — the agent records an activity window, which includes lunch.

**Say when something is uncertain.** Items with confidence below 0.4 either go in a short "Unclassified activity" note at the end or are left out entirely. They never get a confident sentence.

**Write in the third person, past tense, plain English.** No adjectives that cannot be verified: no "successfully", no "comprehensive", no "significant". The work is the claim.

## Weekly variant

The same rules, shorter, with a status breakdown:

```markdown
## This Week

Detected work items: 21
Projects: 7

Platinum: 9 · Miss Platinum: 4 · KFA: 3 · Corporate: 5

In progress: 14
Needs follow-up: 5
Completed: 2
```

`Completed` is small and will usually be small. That is correct, not a measurement failure — most work in a week is not finished in that week.

## Privacy

The report may be forwarded, printed and filed. It must therefore never contain:

- a local file path, a drive letter, a UNC share or a username
- a client's personal data, an email address or a phone number
- an exact timestamp that reconstructs somebody's working day
- the name of a file whose name is itself sensitive

Project names, brand names and discipline names are expected and fine.

If an item you were given contains any of the above, the sanitizer failed. Report that rather than quietly cleaning it — a sanitizer bug that is worked around silently is one that stays.
