"""The five deterministic validators.

Each one takes an atomic claim and the evidence that claim cites, and returns findings.
None of them consults a model, none of them has a threshold to tune, and each answers
a question with a decidable answer:

``NUMERIC``      every number asserted by the claim occurs in the cited evidence
``UNIT``         every number that carries a unit occurs carrying that same unit
``OPERATOR``     every threshold direction matches the direction the evidence states
``QUOTE``        every quoted span occurs verbatim in one single cited passage
``PROVENANCE``   every cited record is approved, current, locatable, and readable

The validators only ever report ``UNSUPPORTED`` for a positive mismatch and
``UNRESOLVED`` for an unreadable check. Silence is a pass. Where two validators would
fire on the same defect the narrower one stays quiet - a value missing from the
evidence entirely is a ``NUMERIC`` finding, so ``UNIT`` and ``OPERATOR`` do not also
report it and turn one defect into three.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.reasoning.verification.normalization import (
    canonical_unit,
    measurement_index,
    normalize_text,
)
from app.reasoning.verification.schemas import (
    AtomicClaim,
    ValidatorFinding,
    ValidatorName,
    VerifiableEvidence,
    VerificationStatus,
)

# Lifecycle states in which a record must not be used to support a rendered claim, even
# though it was retrieved. Retrieval filters on the active release already, so reaching
# one of these here means the release and the record disagree; the claim is withheld
# rather than rendered against a withdrawn recommendation.
_UNUSABLE_LIFECYCLE = frozenset({"WITHDRAWN", "SUPERSEDED", "RETRACTED", "DRAFT"})

_NUMBER = r"\d[\d,]*(?:\.\d+)?|\.\d+"
_UNIT = r"%|[A-Za-zµμ][A-Za-z0-9µμ²³]*(?:\s*(?:/|per)\s*[A-Za-z0-9µμ²³\.]+)*"
_HEDGE = r"(?:approximately\s+|about\s+|roughly\s+|at\s+)?"

# Phrases that state a bound before the number they bound. Longer phrases are listed
# first so that "greater than or equal to" is not consumed by "greater than", which
# would invert the very check this validator exists to make.
_LEADING_OPERATORS: tuple[tuple[str, str], ...] = (
    ("greater than or equal to", "GE"),
    ("less than or equal to", "LE"),
    ("no less than", "GE"),
    ("not less than", "GE"),
    ("no fewer than", "GE"),
    ("no more than", "LE"),
    ("not more than", "LE"),
    ("no greater than", "LE"),
    ("a minimum of", "GE"),
    ("a maximum of", "LE"),
    ("minimum of", "GE"),
    ("maximum of", "LE"),
    ("greater than", "GT"),
    ("more than", "GT"),
    ("less than", "LT"),
    ("fewer than", "LT"),
    ("at least", "GE"),
    ("at most", "LE"),
    ("up to", "LE"),
    ("exceeding", "GT"),
    ("exceeds", "GT"),
    ("above", "GT"),
    ("below", "LT"),
    ("under", "LT"),
    ("over", "GT"),
    ("≥", "GE"),
    (">=", "GE"),
    ("⩾", "GE"),
    ("≤", "LE"),
    ("<=", "LE"),
    ("⩽", "LE"),
    (">", "GT"),
    ("<", "LT"),
)

# Phrases that state a bound after the number, which English does about as often.
_TRAILING_OPERATORS: tuple[tuple[str, str], ...] = (
    ("or more", "GE"),
    ("or greater", "GE"),
    ("or above", "GE"),
    ("or higher", "GE"),
    ("or less", "LE"),
    ("or fewer", "LE"),
    ("or below", "LE"),
    ("or lower", "LE"),
)

_DIRECTION_NAMES = {"GE": "at least", "GT": "greater than", "LE": "at most", "LT": "less than"}

_LEADING_PATTERN = re.compile(
    r"(?P<operator>" + "|".join(re.escape(phrase) for phrase, _ in _LEADING_OPERATORS) + r")"
    r"\s*" + _HEDGE + r"(?P<value>" + _NUMBER + r")\s*(?P<unit>" + _UNIT + r")?",
    re.IGNORECASE,
)

_TRAILING_PATTERN = re.compile(
    r"(?P<value>" + _NUMBER + r")\s*(?P<unit>" + _UNIT + r")?\s*"
    r"(?P<operator>" + "|".join(re.escape(phrase) for phrase, _ in _TRAILING_OPERATORS) + r")",
    re.IGNORECASE,
)

_LEADING_LOOKUP = {phrase.lower(): code for phrase, code in _LEADING_OPERATORS}
_TRAILING_LOOKUP = {phrase.lower(): code for phrase, code in _TRAILING_OPERATORS}

_QUOTED_SPAN = re.compile(r'"([^"]{3,400})"')


@dataclass(frozen=True)
class Comparison:
    operator: str
    value: Decimal
    unit: str | None

    def render(self) -> str:
        direction = _DIRECTION_NAMES[self.operator]
        value = format(self.value.normalize(), "f")
        return f"{direction} {value} {self.unit}".strip() if self.unit else f"{direction} {value}"

    def agrees_with(self, other: Comparison) -> bool:
        """Two comparisons agree when direction and value match and units do not clash.

        A unit stated on one side only is not a disagreement: evidence reading
        ``>= 50 mg`` supports a claim reading ``at least 50``, because the claim has
        restated the threshold without repeating the unit.
        """

        if self.operator != other.operator or self.value != other.value:
            return False
        if self.unit is not None and other.unit is not None:
            return self.unit == other.unit
        return True


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", ""))
    except Exception:  # noqa: BLE001 - malformed numerics are simply not comparisons
        return None


def extract_comparisons(text: str) -> tuple[Comparison, ...]:
    """Every explicit threshold in ``text``, in canonical (direction, value, unit) form."""

    prepared = normalize_text(text)
    found: list[Comparison] = []

    scans = ((_LEADING_PATTERN, _LEADING_LOOKUP), (_TRAILING_PATTERN, _TRAILING_LOOKUP))
    for pattern, lookup in scans:
        for match in pattern.finditer(prepared):
            value = _to_decimal(match.group("value"))
            if value is None:
                continue
            operator = lookup.get(match.group("operator").strip().lower())
            if operator is None:
                continue
            found.append(
                Comparison(
                    operator=operator,
                    value=value,
                    unit=canonical_unit(match.group("unit")),
                )
            )
    return tuple(found)


def _readable(evidence: Sequence[VerifiableEvidence]) -> list[VerifiableEvidence]:
    return [item for item in evidence if item.readable_text is not None]


def _unreadable_finding(
    validator: ValidatorName, atomic: AtomicClaim, evidence: Sequence[VerifiableEvidence]
) -> ValidatorFinding:
    cited = ", ".join(item.evidence_id for item in evidence) or "none"
    return ValidatorFinding(
        validator=validator,
        status=VerificationStatus.UNRESOLVED,
        detail=(
            "no cited evidence exposes readable text, so this check could not be "
            f"performed (cited: {cited})"
        ),
        atomic_index=atomic.index,
    )


def validate_provenance(
    atomic: AtomicClaim, evidence: Sequence[VerifiableEvidence]
) -> tuple[ValidatorFinding, ...]:
    """Check that every cited record may actually stand behind a rendered claim."""

    if not evidence:
        return (
            ValidatorFinding(
                validator=ValidatorName.PROVENANCE,
                status=VerificationStatus.UNSUPPORTED,
                detail="the claim cites no evidence",
                atomic_index=atomic.index,
            ),
        )

    findings: list[ValidatorFinding] = []
    for item in evidence:
        if item.approval_status != "APPROVED":
            findings.append(
                ValidatorFinding(
                    validator=ValidatorName.PROVENANCE,
                    status=VerificationStatus.UNSUPPORTED,
                    detail=(
                        f"{item.evidence_id} is {item.approval_status} rather than APPROVED"
                    ),
                    atomic_index=atomic.index,
                )
            )
            continue
        if item.lifecycle_status.upper() in _UNUSABLE_LIFECYCLE:
            findings.append(
                ValidatorFinding(
                    validator=ValidatorName.PROVENANCE,
                    status=VerificationStatus.UNSUPPORTED,
                    detail=f"{item.evidence_id} is {item.lifecycle_status.upper()}",
                    atomic_index=atomic.index,
                )
            )
            continue
        if item.locator_count < 1:
            findings.append(
                ValidatorFinding(
                    validator=ValidatorName.PROVENANCE,
                    status=VerificationStatus.UNSUPPORTED,
                    detail=(
                        f"{item.evidence_id} carries no locator, so a reader cannot be "
                        "shown where the claim came from"
                    ),
                    atomic_index=atomic.index,
                )
            )
            continue
        if item.readable_text is None:
            findings.append(
                ValidatorFinding(
                    validator=ValidatorName.PROVENANCE,
                    status=VerificationStatus.UNRESOLVED,
                    detail=(
                        f"{item.evidence_id} exposes no readable text, so its content "
                        "cannot be checked"
                    ),
                    atomic_index=atomic.index,
                )
            )
    return tuple(findings)


def validate_numeric(
    atomic: AtomicClaim, evidence: Sequence[VerifiableEvidence]
) -> tuple[ValidatorFinding, ...]:
    """Check that every number the claim asserts occurs in the cited evidence."""

    claim_values, _ = measurement_index(atomic.text, bare_word_numbers=False)
    if not claim_values:
        return ()

    readable = _readable(evidence)
    if not readable:
        return (_unreadable_finding(ValidatorName.NUMERIC, atomic, evidence),)

    evidence_values: set[Decimal] = set()
    for item in readable:
        values, _ = measurement_index(item.readable_text or "")
        evidence_values |= values

    cited = ", ".join(item.evidence_id for item in readable)
    return tuple(
        ValidatorFinding(
            validator=ValidatorName.NUMERIC,
            status=VerificationStatus.UNSUPPORTED,
            detail=(
                f"the value {format(value.normalize(), 'f')} does not occur in cited "
                f"evidence ({cited})"
            ),
            atomic_index=atomic.index,
        )
        for value in sorted(claim_values - evidence_values)
    )


def validate_units(
    atomic: AtomicClaim, evidence: Sequence[VerifiableEvidence]
) -> tuple[ValidatorFinding, ...]:
    """Check that every value carrying a unit occurs carrying that same unit.

    This is the check that separates ``500 mg`` from ``500 mL``. A value the evidence
    never mentions at all is left to the numeric validator, so a single fabricated
    number is reported once rather than twice.
    """

    _, claim_pairs = measurement_index(atomic.text, bare_word_numbers=False)
    if not claim_pairs:
        return ()

    readable = _readable(evidence)
    if not readable:
        return (_unreadable_finding(ValidatorName.UNIT, atomic, evidence),)

    evidence_values: set[Decimal] = set()
    evidence_pairs: set[tuple[Decimal, str]] = set()
    for item in readable:
        values, pairs = measurement_index(item.readable_text or "")
        evidence_values |= values
        evidence_pairs |= pairs

    findings: list[ValidatorFinding] = []
    for value, unit in sorted(claim_pairs - evidence_pairs):
        if value not in evidence_values:
            continue
        rendered_value = format(value.normalize(), "f")
        stated_units = sorted(
            found for existing, found in evidence_pairs if existing == value
        )
        if stated_units:
            observed = ", ".join(f"{rendered_value} {found}" for found in stated_units)
            detail = (
                f"the claim states {rendered_value} {unit} but the cited evidence states "
                f"{observed}"
            )
        else:
            detail = (
                f"the claim attaches the unit {unit} to {rendered_value}, which the cited "
                "evidence states without that unit"
            )
        findings.append(
            ValidatorFinding(
                validator=ValidatorName.UNIT,
                status=VerificationStatus.UNSUPPORTED,
                detail=detail,
                atomic_index=atomic.index,
            )
        )
    return tuple(findings)


def validate_operators(
    atomic: AtomicClaim, evidence: Sequence[VerifiableEvidence]
) -> tuple[ValidatorFinding, ...]:
    """Check that every threshold the claim states matches the evidence's direction.

    Two defects are caught here and they are the ones that change a recommendation
    while reusing its numbers: inverting a bound (evidence ``>= 50``, claim ``under
    50``), and inventing one (evidence states a plain dose, claim turns it into a
    minimum).
    """

    claim_comparisons = extract_comparisons(atomic.text)
    if not claim_comparisons:
        return ()

    readable = _readable(evidence)
    if not readable:
        return (_unreadable_finding(ValidatorName.OPERATOR, atomic, evidence),)

    evidence_comparisons: list[Comparison] = []
    evidence_values: set[Decimal] = set()
    for item in readable:
        text = item.readable_text or ""
        evidence_comparisons.extend(extract_comparisons(text))
        values, _ = measurement_index(text)
        evidence_values |= values

    findings: list[ValidatorFinding] = []
    seen: set[tuple[str, Decimal, str | None]] = set()
    for comparison in claim_comparisons:
        key = (comparison.operator, comparison.value, comparison.unit)
        if key in seen:
            continue
        seen.add(key)

        if any(comparison.agrees_with(other) for other in evidence_comparisons):
            continue
        if comparison.value not in evidence_values:
            # The number itself is absent; the numeric validator reports that defect.
            continue

        conflicting = [
            other for other in evidence_comparisons if other.value == comparison.value
        ]
        if conflicting:
            observed = ", ".join(sorted({other.render() for other in conflicting}))
            detail = (
                f"the claim states {comparison.render()} but the cited evidence states "
                f"{observed}"
            )
        else:
            detail = (
                f"the claim states {comparison.render()}, a bound the cited evidence does "
                "not state"
            )
        findings.append(
            ValidatorFinding(
                validator=ValidatorName.OPERATOR,
                status=VerificationStatus.UNSUPPORTED,
                detail=detail,
                atomic_index=atomic.index,
            )
        )
    return tuple(findings)


def validate_quotes(
    atomic: AtomicClaim, evidence: Sequence[VerifiableEvidence]
) -> tuple[ValidatorFinding, ...]:
    """Check that every quoted span occurs verbatim inside one single cited passage.

    Containment is required within one passage rather than across the union. A quotation
    assembled from two different guidelines is presented to a reader as something a
    source said, and no source said it.
    """

    quoted = _QUOTED_SPAN.findall(normalize_text(atomic.text))
    if not quoted:
        return ()

    readable = _readable(evidence)
    if not readable:
        return (_unreadable_finding(ValidatorName.QUOTE, atomic, evidence),)

    passages = [normalize_text(item.readable_text or "").casefold() for item in readable]
    cited = ", ".join(item.evidence_id for item in readable)

    findings: list[ValidatorFinding] = []
    for span in dict.fromkeys(quoted):
        needle = normalize_text(span).casefold()
        if any(needle in passage for passage in passages):
            continue
        findings.append(
            ValidatorFinding(
                validator=ValidatorName.QUOTE,
                status=VerificationStatus.UNSUPPORTED,
                detail=(
                    f'the quoted span "{span}" does not occur verbatim in any single '
                    f"cited passage ({cited})"
                ),
                atomic_index=atomic.index,
            )
        )
    return tuple(findings)


Validator = Callable[[AtomicClaim, Sequence[VerifiableEvidence]], tuple[ValidatorFinding, ...]]

# Provenance runs first so that an unusable citation is reported as the provenance
# defect it is, rather than as the content mismatches it causes downstream.
VALIDATORS: tuple[Validator, ...] = (
    validate_provenance,
    validate_numeric,
    validate_units,
    validate_operators,
    validate_quotes,
)
