"""
Work out the project, the brand and the category — without AI.

DETERMINISTIC FIRST, AND MOSTLY DETERMINISTIC ONLY. A file in
`D:\\Work\\Platinum Fit\\Campaign\\hero.psd` is a Platinum Fit design job, and
no language model is needed to say so. Extensions and folder names carry nearly
all of the signal; AI is for turning a list of facts into a sentence, not for
establishing the facts.

EVERY ANSWER CARRIES A CONFIDENCE, and `Unclassified` is a legitimate output.
The alternative — guessing a project because it is the most common one — puts
work under the wrong brand in a report somebody forwards to a director. An
honest "Unclassified" is a prompt to add a rule; a confident wrong answer is a
correction in a meeting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePath

from ..config import ProjectRule
from ..database.models import Category

log = logging.getLogger(__name__)


# Extension → category. The single highest-signal rule available, and it costs
# nothing: the extension is already in the event.
EXTENSION_CATEGORIES: dict[str, Category] = {
    # Design
    ".psd": Category.GRAPHIC_DESIGN, ".psb": Category.GRAPHIC_DESIGN,
    ".ai": Category.GRAPHIC_DESIGN, ".indd": Category.GRAPHIC_DESIGN,
    ".fig": Category.GRAPHIC_DESIGN, ".sketch": Category.GRAPHIC_DESIGN,
    ".xd": Category.GRAPHIC_DESIGN, ".afdesign": Category.GRAPHIC_DESIGN,
    ".afphoto": Category.GRAPHIC_DESIGN, ".cdr": Category.GRAPHIC_DESIGN,
    ".eps": Category.GRAPHIC_DESIGN, ".svg": Category.GRAPHIC_DESIGN,

    # Video
    ".aep": Category.VIDEO_PRODUCTION, ".prproj": Category.VIDEO_PRODUCTION,
    ".mp4": Category.VIDEO_PRODUCTION, ".mov": Category.VIDEO_PRODUCTION,
    ".avi": Category.VIDEO_PRODUCTION, ".mkv": Category.VIDEO_PRODUCTION,
    ".drp": Category.VIDEO_PRODUCTION, ".veg": Category.VIDEO_PRODUCTION,
    ".fcpxml": Category.VIDEO_PRODUCTION, ".mogrt": Category.VIDEO_PRODUCTION,
    ".wav": Category.VIDEO_PRODUCTION, ".aif": Category.VIDEO_PRODUCTION,

    # Development
    ".py": Category.DEVELOPMENT, ".js": Category.DEVELOPMENT,
    ".ts": Category.DEVELOPMENT, ".tsx": Category.DEVELOPMENT,
    ".jsx": Category.DEVELOPMENT, ".cs": Category.DEVELOPMENT,
    ".java": Category.DEVELOPMENT, ".go": Category.DEVELOPMENT,
    ".rs": Category.DEVELOPMENT, ".php": Category.DEVELOPMENT,
    ".sql": Category.DEVELOPMENT, ".sh": Category.DEVELOPMENT,
    ".dart": Category.DEVELOPMENT, ".swift": Category.DEVELOPMENT,
    ".kt": Category.DEVELOPMENT, ".vue": Category.DEVELOPMENT,

    # Website
    ".html": Category.WEBSITE, ".htm": Category.WEBSITE,
    ".css": Category.WEBSITE, ".scss": Category.WEBSITE,

    # Documents
    ".docx": Category.DOCUMENTATION, ".doc": Category.DOCUMENTATION,
    ".xlsx": Category.DOCUMENTATION, ".xls": Category.DOCUMENTATION,
    ".csv": Category.DOCUMENTATION, ".pdf": Category.DOCUMENTATION,
    ".md": Category.DOCUMENTATION, ".txt": Category.DOCUMENTATION,
    ".odt": Category.DOCUMENTATION, ".ods": Category.DOCUMENTATION,

    # Presentations
    ".pptx": Category.PRESENTATION, ".ppt": Category.PRESENTATION,
    ".key": Category.PRESENTATION, ".odp": Category.PRESENTATION,
}

# Folder-name hints, applied only when the extension is ambiguous. A .jpg under
# "Social" is a social asset; the same .jpg under "Research" is a reference.
FOLDER_CATEGORY_HINTS: dict[str, Category] = {
    "social": Category.SOCIAL_MEDIA,
    "instagram": Category.SOCIAL_MEDIA,
    "tiktok": Category.SOCIAL_MEDIA,
    "reels": Category.SOCIAL_MEDIA,
    "ads": Category.DIGITAL_MARKETING,
    "campaign": Category.DIGITAL_MARKETING,
    "media": Category.DIGITAL_MARKETING,
    "website": Category.WEBSITE,
    "web": Category.WEBSITE,
    "research": Category.RESEARCH,
    "report": Category.DOCUMENTATION,
    "reports": Category.DOCUMENTATION,
    "ai": Category.AI_CREATIVE,
    "midjourney": Category.AI_CREATIVE,
    "generated": Category.AI_CREATIVE,
}

# Images are everywhere and mean nothing on their own, so they are resolved by
# folder rather than by extension.
AMBIGUOUS_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".heic"}


@dataclass(frozen=True)
class Classification:
    project: str
    brand: str | None
    category: Category
    confidence: float
    matched_rule: str | None = None


class Classifier:
    """
    Rules first, folder structure second, extension always.

    The rules come from config and are the user's own vocabulary. The fallback
    reads the folder immediately under an approved root, which is how almost
    everyone organises work — `D:\\Work\\<Client>\\...` — and is right often
    enough to be useful while being obviously a guess, which is what the lower
    confidence says.
    """

    UNCLASSIFIED = "Unclassified"

    def __init__(self, rules: list[ProjectRule]) -> None:
        # Longest match first. "Platinum Fit" and "Platinum" both match a
        # Platinum Fit path, and the more specific rule is the right one — so
        # specificity is decided by rule length, not by config order, which
        # nobody maintains correctly.
        self._rules = sorted(rules, key=lambda r: len(r.match), reverse=True)

    # ------------------------------------------------------------------
    def category_for(self, path: str | PurePath) -> tuple[Category, float]:
        p = PurePath(str(path))
        ext = p.suffix.lower()
        parts = [part.lower() for part in p.parts[:-1]]

        if ext and ext not in AMBIGUOUS_EXTENSIONS:
            mapped = EXTENSION_CATEGORIES.get(ext)
            if mapped is not None:
                return mapped, 0.9

        # Nearest folder wins: the one closest to the file describes it best.
        for part in reversed(parts):
            for hint, category in FOLDER_CATEGORY_HINTS.items():
                if hint in part:
                    return category, 0.6

        if ext in AMBIGUOUS_EXTENSIONS:
            return Category.GRAPHIC_DESIGN, 0.35
        return Category.UNCLASSIFIED, 0.1

    def project_for(self, path: str | PurePath, root: PurePath | None = None
                    ) -> tuple[str, str | None, str | None, float]:
        """(project, brand, matched_rule, confidence)."""
        text = str(path).lower()

        for rule in self._rules:
            if rule.match in text:
                return rule.project, rule.brand, rule.match, 0.95

        # No rule matched. Fall back to the first folder under the approved
        # root, which is a structural guess and is scored as one.
        if root is not None:
            try:
                rel = PurePath(str(path)).relative_to(root)
                if len(rel.parts) > 1:
                    return rel.parts[0], None, None, 0.45
            except ValueError:
                pass

        return self.UNCLASSIFIED, None, None, 0.0

    def classify(self, path: str | PurePath, root: PurePath | None = None) -> Classification:
        project, brand, matched, p_conf = self.project_for(path, root)
        category, c_conf = self.category_for(path)

        # The weaker of the two, because an item is only as trustworthy as its
        # least certain part. Averaging would let a confident category hide an
        # unidentified project, which is the mistake that matters.
        return Classification(
            project=project,
            brand=brand,
            category=category,
            confidence=round(min(p_conf, c_conf), 2),
            matched_rule=matched,
        )
