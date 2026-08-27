"""Deterministic decomposition of a model claim into independently checkable units.

Planning exists to close one specific hole. A model claim such as "Start dolutegravir
50 mg once daily and monitor viral load at 3 months" is checked as a single string by a
naive validator, so a fabricated half can ride along on a grounded half - every number
in the sentence is present *somewhere* in the cited passages, and the sentence passes.
Splitting first and requiring every part to pass separately removes that.

Segmentation is conservative by design. Sentence terminators and semicolons end a unit;
coordinating conjunctions do not. "500 mg and 1 g" and "monitor CD4 and viral load"
have the same surface shape and opposite correct splits, so splitting on ``and`` would
be a guess. Coordination therefore stays inside one unit, where the numeric and unit
validators still check both values independently.
"""

from __future__ import annotations

import re

from app.reasoning.verification.normalization import normalize_text
from app.reasoning.verification.schemas import AtomicClaim

# Terminators followed by whitespace and a capital or digit. Requiring the following
# character keeps decimals ("0.5 mg") and the abbreviations below from ending a unit.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[\"(]?[A-Z0-9])")
_SEMICOLON_BREAK = re.compile(r"\s*;\s*")

# Abbreviations whose trailing period is not a sentence end. The list is short on
# purpose: each entry is one that actually occurs in guideline prose, and a miss costs
# only an over-split unit, which is checked against the same citation set anyway.
_ABBREVIATIONS = (
    "e.g.",
    "i.e.",
    "vs.",
    "approx.",
    "no.",
    "fig.",
    "tab.",
    "dr.",
    "prof.",
    "wt.",
    "max.",
    "min.",
)

_PLACEHOLDER = "\x00"


def _protect_abbreviations(text: str) -> str:
    protected = text
    for abbreviation in _ABBREVIATIONS:
        protected = re.sub(
            re.escape(abbreviation),
            abbreviation.replace(".", _PLACEHOLDER),
            protected,
            flags=re.IGNORECASE,
        )
    return protected


def _restore_abbreviations(text: str) -> str:
    return text.replace(_PLACEHOLDER, ".")


def plan_atomic_claims(claim_text: str) -> tuple[AtomicClaim, ...]:
    """Carve ``claim_text`` into atomic claims, in order.

    A claim that resists splitting comes back as a single unit rather than as an empty
    plan, so an unsplittable claim is still verified rather than silently skipped.
    """

    prepared = _protect_abbreviations(normalize_text(claim_text))

    segments: list[str] = []
    for sentence in _SENTENCE_BREAK.split(prepared):
        segments.extend(_SEMICOLON_BREAK.split(sentence))

    planned: list[AtomicClaim] = []
    for segment in segments:
        restored = _restore_abbreviations(segment).strip()
        if not restored or not any(character.isalnum() for character in restored):
            continue
        planned.append(AtomicClaim(index=len(planned), text=restored))

    if not planned:
        collapsed = _restore_abbreviations(prepared).strip()
        if collapsed:
            planned.append(AtomicClaim(index=0, text=collapsed))
    return tuple(planned)
