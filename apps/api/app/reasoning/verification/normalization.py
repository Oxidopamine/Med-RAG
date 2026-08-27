"""Surface normalization shared by the deterministic validators.

Every comparison in this package is made between normalized forms, never between raw
strings. ``0.50 mg``, ``.5 milligrams``, and ``0.5mg`` are the same measurement, and a
validator that reported otherwise would withhold correct claims until nobody trusted
it. Normalization is therefore part of the safety argument rather than tidying: a
false ``UNSUPPORTED`` is cheap once but expensive repeatedly, because the eventual fix
is always to loosen the check.

The normalizers are intentionally conservative in one direction. An unrecognized unit
token is dropped to ``None`` rather than guessed at, so an unknown unit degrades to a
bare numeric check instead of inventing a mismatch.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Written-out integers appear in guideline prose about as often as digits ("at six
# months", "two tablets"), so a validator that only reads digits would call a correct
# paraphrase unsupported. The map stops at the range that actually occurs in dosing and
# interval language; beyond it, guidelines use digits.
_WORD_NUMBERS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
}

_ORDINAL_WORDS: dict[str, int] = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "twelfth": 12,
}

# Canonical unit spellings. The key set is deliberately clinical: mass, volume,
# duration, and the concentration units that carry dosing and monitoring thresholds.
_UNIT_ALIASES: dict[str, str] = {
    "mg": "mg",
    "milligram": "mg",
    "milligramme": "mg",
    "g": "g",
    "gm": "g",
    "gram": "g",
    "gramme": "g",
    "kg": "kg",
    "kilogram": "kg",
    "kilogramme": "kg",
    "mcg": "mcg",
    "ug": "mcg",
    "µg": "mcg",
    "μg": "mcg",
    "microgram": "mcg",
    "ng": "ng",
    "nanogram": "ng",
    "ml": "ml",
    "cc": "ml",
    "millilitre": "ml",
    "milliliter": "ml",
    "l": "l",
    "litre": "l",
    "liter": "l",
    "dl": "dl",
    "decilitre": "dl",
    "deciliter": "dl",
    "iu": "iu",
    "unit": "iu",
    "%": "%",
    "percent": "%",
    "pct": "%",
    "mmhg": "mmhg",
    "mmol": "mmol",
    "mol": "mol",
    "meq": "meq",
    "cell": "cell",
    "copy": "copy",
    "copie": "copy",
    "min": "minute",
    "minute": "minute",
    "h": "hour",
    "hr": "hour",
    "hour": "hour",
    "day": "day",
    "week": "week",
    "wk": "week",
    "month": "month",
    "mo": "month",
    "year": "year",
    "yr": "year",
    "mm3": "mm3",
    "mm³": "mm3",
    "m2": "m2",
    "m²": "m2",
}

# A unit token may be a composite such as ``mg/kg`` or ``cells/mm3``; each side is
# canonicalized independently and rejoined, so ``copies/mL`` and ``copy per ml`` agree.
_COMPOSITE_SEPARATOR = re.compile(r"\s*(?:/|\bper\b)\s*")

_MEASUREMENT = re.compile(
    r"(?P<value>\d[\d,]*(?:\.\d+)?|\.\d+)"
    r"\s*"
    r"(?P<unit>%|[A-Za-zµμ][A-Za-z0-9µμ²³]*"
    r"(?:\s*(?:/|\bper\b)\s*[A-Za-z0-9µμ²³\.]+)*)?"
)

_WORD_MEASUREMENT = re.compile(
    r"\b(?P<value>" + "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True)) + r")\b"
    r"\s*(?P<unit>[A-Za-zµμ][A-Za-z0-9µμ²³]*)?",
    re.IGNORECASE,
)

_ORDINAL_DIGITS = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)

_QUOTE_CHARACTERS = dict.fromkeys(map(ord, "“”„″"), '"')
_DASH_CHARACTERS = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Measurement:
    """A numeric value with its canonical unit, or ``None`` when it carries no unit."""

    value: Decimal
    unit: str | None

    def render(self) -> str:
        text = format(self.value.normalize(), "f")
        return f"{text} {self.unit}" if self.unit else text


def normalize_text(text: str) -> str:
    """Fold the typographic variation that must never decide a comparison."""

    folded = unicodedata.normalize("NFKC", text)
    folded = folded.translate(_QUOTE_CHARACTERS).translate(_DASH_CHARACTERS)
    return _WHITESPACE.sub(" ", folded).strip()


def canonical_unit(token: str | None) -> str | None:
    """Reduce a unit token to its canonical spelling, or ``None`` if unrecognized.

    Returning ``None`` for an unknown token is what keeps the unit validator from
    firing on ordinary prose: in ``400 patients`` the trailing word is captured as a
    candidate unit, fails to canonicalize, and the measurement degrades to a bare
    number.
    """

    if not token:
        return None
    cleaned = normalize_text(token).strip().lower().rstrip(".")
    if not cleaned:
        return None
    if cleaned == "%":
        return "%"

    parts = [part for part in _COMPOSITE_SEPARATOR.split(cleaned) if part]
    if not parts:
        return None

    canonical_parts: list[str] = []
    for part in parts:
        singular = part[:-1] if len(part) > 1 and part.endswith("s") else part
        mapped = _UNIT_ALIASES.get(part) or _UNIT_ALIASES.get(singular)
        if mapped is None:
            return None
        canonical_parts.append(mapped)
    return "/".join(canonical_parts)


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None


def extract_measurements(text: str, *, bare_word_numbers: bool = True) -> tuple[Measurement, ...]:
    """Every numeric value in ``text``, with its canonical unit where it has one.

    Digits and written-out numbers are both read, and a digit ordinal (``1st``) is
    reduced to its value so that it agrees with the word form (``first``) a guideline
    is equally likely to use.

    ``bare_word_numbers`` controls the one asymmetry in this package. English number
    words are frequently determiners rather than quantities - "one of the recommended
    regimens", "first-line therapy" - so reading them as values on the claim side
    manufactures numbers that were never asserted and then fails to find them in the
    evidence. Extraction is therefore generous when indexing evidence and conservative
    when reading a claim: pass ``False`` to keep only word numbers that carry a real
    unit, which is what distinguishes "at six months" from "one of the options".
    """

    prepared = _ORDINAL_DIGITS.sub(r"\1", normalize_text(text))
    found: list[Measurement] = []

    for match in _MEASUREMENT.finditer(prepared):
        value = _to_decimal(match.group("value"))
        if value is not None:
            found.append(Measurement(value=value, unit=canonical_unit(match.group("unit"))))

    for match in _WORD_MEASUREMENT.finditer(prepared):
        word = match.group("value").lower()
        unit = canonical_unit(match.group("unit"))
        if unit is None and not bare_word_numbers:
            continue
        found.append(Measurement(value=Decimal(_WORD_NUMBERS[word]), unit=unit))

    if bare_word_numbers:
        for word, value in _ORDINAL_WORDS.items():
            if re.search(rf"\b{word}\b", prepared, re.IGNORECASE):
                found.append(Measurement(value=Decimal(value), unit=None))

    return tuple(found)


def measurement_index(
    text: str, *, bare_word_numbers: bool = True
) -> tuple[set[Decimal], set[tuple[Decimal, str]]]:
    """Split extracted measurements into the two sets the validators compare against.

    The bare-value set answers "does this number occur here at all"; the paired set
    answers "does it occur here carrying this unit". Keeping them apart is what lets
    the numeric and unit validators report different failures for ``500 mg`` against
    ``500 mL`` - the number is present, the pairing is not.
    """

    values: set[Decimal] = set()
    pairs: set[tuple[Decimal, str]] = set()
    for measurement in extract_measurements(text, bare_word_numbers=bare_word_numbers):
        values.add(measurement.value)
        if measurement.unit is not None:
            pairs.add((measurement.value, measurement.unit))
    return values, pairs
