# Project Mapping

The brands and projects this agent classifies work into, and the disciplines
each one usually involves.

This file is **reference, not configuration**. The agent reads its rules from
`config.json`; this document explains what those rules mean so that a person —
or an agent writing a report — uses the same vocabulary.

It deliberately contains no client names, no contract details, no figures and
no internal strategy. It is safe to read; it is not a business document.

---

## Brands

| Project | Brand | Usual disciplines |
|---|---|---|
| **Platinum** | Platinum | Marketing · Creative · Digital |
| **Platinum Fit** | Platinum Fit | Product · Creative · Marketing |
| **Miss Platinum** | Miss Platinum | Marketing · Creative |
| **Al Jazira Club** | Al Jazira Club | Sports Marketing |
| **KFA** | KFA | Partnership · Sports Marketing |
| **Mashhor Hub** | Mashhor Hub | Product · Development · Marketing |
| **Corporate** | Holding | Management · Reporting · Coordination |

---

## Matching

Rules match a **case-insensitive substring** of the path.

**The longest match wins.** `Platinum Fit` and `Platinum` both match a
`D:\Work\Platinum Fit\...` path, and the more specific rule is the correct one.
Specificity is decided by the length of the match string, not by the order rules
appear in `config.json` — an order nobody maintains correctly over a year.

Example rule set:

```json
{
  "project_rules": [
    { "match": "platinum fit",   "project": "Platinum Fit",   "brand": "Platinum Fit",   "category": "Product / Creative / Marketing" },
    { "match": "miss platinum",  "project": "Miss Platinum",  "brand": "Miss Platinum",  "category": "Marketing / Creative" },
    { "match": "platinum",       "project": "Platinum",       "brand": "Platinum",       "category": "Marketing / Creative / Digital" },
    { "match": "al jazira",      "project": "Al Jazira Club", "brand": "Al Jazira Club", "category": "Sports Marketing" },
    { "match": "kfa",            "project": "KFA",            "brand": "KFA",            "category": "Partnership / Sports Marketing" },
    { "match": "mashhor",        "project": "Mashhor Hub",    "brand": "Mashhor Hub",    "category": "Product / Development / Marketing" }
  ]
}
```

---

## When nothing matches

The result is **`Unclassified`**, with confidence `0.0`.

This is a correct answer, not a failure. The alternative — attributing work to
the most frequently seen project — puts a deliverable under the wrong brand in a
report somebody forwards to its manager.

`Unclassified` is a prompt to add one rule. A confident wrong answer is a
correction in a meeting.

If the same folder keeps producing unclassified items, add a rule for it. The
`project-classifier` skill will suggest one; it will not write one on its own.

---

## Disciplines

Decided by file extension first, folder name second. These are the categories a
report groups by:

| Discipline | Typical extensions |
|---|---|
| Graphic Design | `.psd` `.ai` `.indd` `.fig` `.sketch` `.xd` `.eps` `.svg` |
| Video Production | `.aep` `.prproj` `.mp4` `.mov` `.drp` `.mogrt` `.wav` |
| AI Creative Production | *(by folder — `ai/`, `midjourney/`, `generated/`)* |
| Development | `.py` `.js` `.ts` `.tsx` `.cs` `.dart` `.sql` |
| Website | `.html` `.css` `.scss` |
| Documentation / Reporting | `.docx` `.xlsx` `.pdf` `.md` `.csv` |
| Presentation | `.pptx` `.key` |
| Social Media | *(by folder — `social/`, `instagram/`, `reels/`)* |
| Digital Marketing | *(by folder — `ads/`, `campaign/`, `media/`)* |
| Research | *(by folder — `research/`)* |

**Images are ambiguous.** A `.jpg` under `Social` is a social asset; the same
`.jpg` under `Research` is a reference somebody saved. Extension alone is not
enough, so the folder decides.

---

## Adding a project

1. Create the folder under an approved root.
2. Add a rule in Settings (or `config.json`) with the shortest substring that
   uniquely identifies it.
3. Check the next daily report shows it under the right brand.

Keep the match string short and distinctive. `"kfa"` is better than
`"KFA Partnership 2026 Final"` — the first survives a folder rename, the second
does not.
