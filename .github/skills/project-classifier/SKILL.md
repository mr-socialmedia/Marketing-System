---
name: project-classifier
description: Map work activity to the correct project, brand and discipline using the configured mapping rules, and return Unclassified with a low confidence rather than guessing when no rule fits.
---

# Project Classifier

Use this skill when activity needs to be attributed to a project and a brand, or when the mapping rules themselves need reviewing.

The local agent already classifies deterministically from folder names and extensions. Use this skill when that produced `Unclassified`, when a mapping looks wrong, or when the user is deciding what rules to add.

## The brands

See `knowledge/PROJECT_MAPPING.md` for the authoritative list. It is the user's own vocabulary and it changes; this skill reads it rather than hard-coding names.

## Rules

**The most specific rule wins.** "Platinum Fit" and "Platinum" both match a Platinum Fit path. Specificity is decided by the length of the match, not by the order the rules were typed — an order nobody maintains correctly over a year.

**Extension decides the discipline; folder decides the project.** `.psd` is design wherever it lives. Which *project* it belongs to comes from the path. These are two independent questions and conflating them is how a Platinum video gets filed as a Miss Platinum design.

**Images are ambiguous and must be resolved by folder.** A `.jpg` under `Social` is a social asset; the same `.jpg` under `Research` is a reference someone saved. Extension alone is not enough.

**`Unclassified` is a correct answer.** Do not guess the most frequent project. The cost of a wrong attribution is a report filed under the wrong brand and forwarded to its manager. The cost of `Unclassified` is a prompt to add one rule.

**Confidence takes the weaker half.** An item is only as trustworthy as its least certain part. A confident `.psd` category must not hide a project that was never identified — averaging the two is precisely the error that hides the half that matters.

**Suggest rules, do not invent them.** If activity repeatedly lands in `Unclassified` under the same folder, say so and propose a rule for the user to approve:

> 14 items this month came from a folder named `KFA-2026`. Add a rule mapping `kfa` → project **KFA**, category **Partnership / Sports Marketing**?

Never write a rule into the configuration on your own.

## Privacy

Classify from what you are given: project names, folder *labels*, extensions, categories. Never ask for the full path in order to classify better — the folder label under the approved root is deliberately all that is exposed, and it is enough.

If the label alone is insufficient, return `Unclassified` and suggest a rule. That is the designed outcome, not a limitation to work around.

## Output

```json
{
  "project": "Platinum Fit",
  "brand": "Platinum Fit",
  "category": "Graphic Design",
  "confidence": 0.95,
  "matched_rule": "platinum fit",
  "suggestion": null
}
```

When nothing matches:

```json
{
  "project": "Unclassified",
  "brand": null,
  "category": "Graphic Design",
  "confidence": 0.0,
  "matched_rule": null,
  "suggestion": "Add a rule mapping 'kfa-2026' to a project."
}
```
