# Reporting Rules

How activity becomes a sentence somebody will read.

These rules exist because the failure mode of an automatic report is not that it
is empty — it is that it is **confident and wrong**. A report nobody can trust
gets quietly ignored after two months, which is worse than never having built
one.

---

## The core distinction

A report describes **work**. A log describes **software**.

| Bad | Good |
|---|---|
| Modified 17 PSD files. | Developed and revised campaign creative assets for Platinum. |
| Sent 6 emails to IT. | Coordinated with IT regarding application content and technical requirements. |
| Opened Premiere Pro 23 times. | Produced and edited promotional video content. |
| 4 files in `Platinum Fit/Campaign`. | Produced campaign visuals for Platinum Fit. |
| Exported 12 JPGs. | Prepared and exported final campaign assets. |

The right-hand column is not just better wording. It answers the question a
director actually has — *what did the department produce?* — and the left-hand
column does not.

---

## Never invent an achievement

Every line must trace to a work item, and every work item to a recorded event.

If the evidence does not support a claim, the claim does not appear. Not in a
weaker form, not hedged — absent.

| Evidence | Permitted | Not permitted |
|---|---|---|
| 6 files edited over 2 hours | "Produced campaign creative assets" | "Led the campaign" |
| 1 file touched once | "Worked on the brief" | "Completed the brief" |
| 19 days of revisions | "Developed and revised over the month" | "Finalised and delivered" |
| Nothing in a category | *(omit the section)* | "No major activity this month" |

---

## Never claim completion without evidence

A file stops changing when it is done, when it is abandoned, and when the person
goes on holiday. Those are indistinguishable from the outside.

Status values:

- **`in_progress`** — the default, and what most items are
- **`needs_follow_up`** — activity stopped mid-stream, or a blocker is visible
- **`completed`** — requires stated evidence: a final export, an approval email,
  a calendar event that occurred

In a typical week, `completed` is a small number. That is correct, not a
measurement failure — most work in a week is not finished in that week. A report
claiming otherwise is one somebody will check.

---

## Never exaggerate

Avoid every adjective that cannot be verified from the data:

> ~~successfully~~ ~~comprehensive~~ ~~significant~~ ~~extensive~~
> ~~strategic~~ ~~seamless~~ ~~robust~~ ~~world-class~~

The work is the claim. "Produced and edited promotional video content for the Q1
campaign" needs no adjective, and an adjective would only weaken it by inviting
the reader to discount the sentence.

---

## Group by discipline, not by project

A director reads the department's **output**. Projects belong inside the lines,
not as the headings.

```markdown
## Video Production
- Produced and edited promotional video content for Platinum.
- Worked on event and sports campaign video materials for Al Jazira Club.
```

Not:

```markdown
## Platinum
- Video work
## Al Jazira Club
- Video work
```

Section order — Creative & Design, Video Production, Digital & App, Website,
Development, Events, Coordination, Documentation & Reporting, Research. Creative
first, because that is what most of these months are made of, and a report whose
first section is usually empty reads as a broken report.

Use only sections that have content. An empty heading says the month was thin;
omitting it says nothing, which is correct when nothing happened.

---

## Merge repetition

Nineteen days of logo revisions is **one line**, not nineteen.

The local pipeline merges within a day; the monthly report merges across days.
If a line would read "worked on the logo" for the fourth time in one document,
the merge did not happen.

---

## Statistics must be countable

Report only what is actually there:

```
Projects worked on: 12
Unique files: 143
Work sessions: 37
Design sessions: 18
Video sessions: 9
```

**Never report "hours worked" or "focused time".** The agent records an
*activity window* — the gap between the first and last file touched — which
includes lunch, meetings, and an afternoon where the laptop was open on the
wrong desk. Presenting it as worked time produces a number somebody repeats.

If a duration is needed, use a bucket: `short`, `medium`, `long`.

---

## Flag uncertainty, do not hide it

Items with confidence below 0.4 either go in a short note at the end:

> _3 items could not be matched to a project. Add a mapping rule in Settings._

or are left out. They never receive a confident sentence.

A low-confidence item is still real work — it just needs a rule. Hiding it
entirely means the rule never gets added, and the same work goes missing again
next month.

---

## Voice

Third person, past tense, plain English. Arabic reports follow the same rules;
the vocabulary changes, the honesty does not.

The reader is a busy director who will spend ninety seconds on this. Write for
that.
