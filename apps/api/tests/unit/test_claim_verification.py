"""The deterministic validators are the rendering safety argument, so they are tested
directly - including the cases where they must stay silent.

Two kinds of test carry equal weight here. The catch tests show that a fabricated
number, a swapped unit, an inverted threshold, or an invented quotation is withheld.
The quiet tests show that ordinary correct paraphrase is not, because a validator that
withholds correct claims is removed within a week and then catches nothing at all.
"""

from __future__ import annotations

import pytest

from app.reasoning.verification import (
    DeterministicClaimVerifier,
    VerifiableEvidence,
    VerificationStatus,
    plan_atomic_claims,
)
from app.reasoning.verification.normalization import canonical_unit, extract_measurements
from app.reasoning.verification.schemas import ValidatorName, combine
from app.reasoning.verification.validators import extract_comparisons

GUIDELINE = (
    "Adults starting first-line antiretroviral therapy should receive dolutegravir "
    "50 mg once daily. Measure viral load at six months and at 12 months. Switch "
    "regimen if viral load is at least 1000 copies/mL on two consecutive tests. "
    "Do not co-administer with rifampicin unless the dose is increased to 50 mg "
    "twice daily."
)


def evidence(**overrides) -> dict[str, VerifiableEvidence]:
    record = VerifiableEvidence(evidence_id="EV_a", exact_text=GUIDELINE)
    for field, value in overrides.items():
        record = VerifiableEvidence(**{**record.__dict__, field: value})
    return {record.evidence_id: record}


def verify(claim: str, evidence_map: dict[str, VerifiableEvidence] | None = None):
    resolved = evidence_map if evidence_map is not None else evidence()
    return DeterministicClaimVerifier().verify(claim, list(resolved), resolved)


def validators_that_fired(result) -> set[ValidatorName]:
    return {finding.validator for finding in result.failures}


# --- claims that must pass -------------------------------------------------------


@pytest.mark.parametrize(
    "claim",
    [
        "Give dolutegravir 50 mg once daily.",
        "Measure viral load at 6 months.",
        "Measure viral load at six months.",
        "Switch regimen if viral load is at least 1000 copies/mL.",
        "Switch regimen if viral load is 1,000 copies per mL or more.",
        "Increase the dose to 50 mg twice daily with rifampicin.",
        "The guideline states \"do not co-administer with rifampicin\".",
        "Dolutegravir is recommended for adults starting therapy.",
        "Use one of the recommended first-line regimens.",
        "Dolutegravir 50.0 mg is the first-line dose.",
    ],
)
def test_faithful_claims_are_supported(claim: str) -> None:
    assert verify(claim).status is VerificationStatus.SUPPORTED


def test_written_and_digit_numbers_are_the_same_number() -> None:
    """``six months`` in the guideline must support ``6 months`` in the claim."""

    assert verify("Measure viral load at 6 months.").status is VerificationStatus.SUPPORTED
    assert verify("Measure viral load at twelve months.").status is VerificationStatus.SUPPORTED


def test_bare_number_words_in_a_claim_are_not_read_as_quantities() -> None:
    """``one of`` and ``first-line`` are determiners, not asserted values."""

    result = verify("Use one of the first-line regimens.")
    assert result.status is VerificationStatus.SUPPORTED


# --- claims that must be withheld ------------------------------------------------


def test_a_fabricated_value_is_unsupported() -> None:
    result = verify("Give dolutegravir 400 mg once daily.")

    assert result.status is VerificationStatus.UNSUPPORTED
    assert ValidatorName.NUMERIC in validators_that_fired(result)
    assert "400" in result.summary()


def test_a_swapped_unit_is_unsupported_even_though_the_number_is_present() -> None:
    result = verify("Give dolutegravir 50 mL once daily.")

    assert result.status is VerificationStatus.UNSUPPORTED
    assert validators_that_fired(result) == {ValidatorName.UNIT}
    assert "50 mg" in result.summary()


def test_an_inverted_threshold_is_unsupported() -> None:
    result = verify("Switch regimen if viral load is under 1000 copies/mL.")

    assert result.status is VerificationStatus.UNSUPPORTED
    assert ValidatorName.OPERATOR in validators_that_fired(result)
    assert "at least 1000" in result.summary()


def test_a_threshold_the_evidence_never_states_is_unsupported() -> None:
    """The evidence gives a plain dose; the claim turns it into a floor."""

    result = verify("Give at least 50 mg of dolutegravir.")

    assert result.status is VerificationStatus.UNSUPPORTED
    assert ValidatorName.OPERATOR in validators_that_fired(result)


def test_a_fabricated_quotation_is_unsupported() -> None:
    result = verify('The guideline states "stop all therapy immediately".')

    assert result.status is VerificationStatus.UNSUPPORTED
    assert ValidatorName.QUOTE in validators_that_fired(result)


def test_a_quotation_spliced_across_passages_is_unsupported() -> None:
    """Each half is present, but no single source said the whole sentence."""

    split = {
        "EV_a": VerifiableEvidence("EV_a", "Measure viral load at six months."),
        "EV_b": VerifiableEvidence("EV_b", "Switch regimen on two consecutive tests."),
    }
    result = verify(
        'The guideline states "at six months switch regimen".', split
    )

    assert result.status is VerificationStatus.UNSUPPORTED
    assert ValidatorName.QUOTE in validators_that_fired(result)


def test_one_fabricated_half_withholds_the_whole_claim() -> None:
    """The defect atomic planning exists to catch: a grounded half carrying a bad one."""

    result = verify("Give dolutegravir 50 mg once daily. Measure viral load at 9 months.")

    assert len(result.atomic_claims) == 2
    assert result.status is VerificationStatus.UNSUPPORTED
    assert any(finding.atomic_index == 1 for finding in result.failures)


# --- provenance ------------------------------------------------------------------


def test_unrenderable_evidence_is_unresolved_rather_than_supported() -> None:
    """The check could not be performed, which is not the same as passing."""

    result = verify("Give dolutegravir 50 mg once daily.", evidence(render_allowed=False))

    assert result.status is VerificationStatus.UNRESOLVED
    assert ValidatorName.PROVENANCE in validators_that_fired(result)


def test_unapproved_evidence_is_unsupported() -> None:
    result = verify("Give dolutegravir 50 mg once daily.", evidence(approval_status="QUARANTINED"))

    assert result.status is VerificationStatus.UNSUPPORTED
    assert ValidatorName.PROVENANCE in validators_that_fired(result)


def test_withdrawn_evidence_is_unsupported() -> None:
    result = verify("Give dolutegravir 50 mg once daily.", evidence(lifecycle_status="WITHDRAWN"))

    assert result.status is VerificationStatus.UNSUPPORTED
    assert "WITHDRAWN" in result.summary()


def test_evidence_without_a_locator_is_unsupported() -> None:
    """A claim a reader cannot be shown the source of is not renderable."""

    result = verify("Give dolutegravir 50 mg once daily.", evidence(locator_count=0))

    assert result.status is VerificationStatus.UNSUPPORTED
    assert "locator" in result.summary()


def test_a_citation_missing_from_the_verification_set_is_unsupported() -> None:
    result = DeterministicClaimVerifier().verify(
        "Give dolutegravir 50 mg once daily.", ["EV_ghost"], evidence()
    )

    assert result.status is VerificationStatus.UNSUPPORTED
    assert "EV_ghost" in result.summary()


def test_a_claim_citing_nothing_is_unsupported() -> None:
    result = DeterministicClaimVerifier().verify("Give 50 mg once daily.", [], {})

    assert result.status is VerificationStatus.UNSUPPORTED
    assert "cites no evidence" in result.summary()


# --- component behaviour ---------------------------------------------------------


def test_a_missing_value_is_reported_once_not_by_every_validator() -> None:
    """One defect must not become three findings, or the summary is unreadable."""

    result = verify("Give at least 400 mg once daily.")

    assert validators_that_fired(result) == {ValidatorName.NUMERIC}


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("mg", "mg"),
        ("Milligrams", "mg"),
        ("mL", "ml"),
        ("copies/mL", "copy/ml"),
        ("copies per mL", "copy/ml"),
        ("cells/mm3", "cell/mm3"),
        ("%", "%"),
        ("percent", "%"),
        ("patients", None),
        ("of", None),
    ],
)
def test_unit_canonicalization(token: str, expected: str | None) -> None:
    assert canonical_unit(token) == expected


def test_unrecognized_units_degrade_to_a_bare_number() -> None:
    """``400 patients`` must not be read as 400 of some unit called ``patients``."""

    measurements = extract_measurements("enrolled 400 patients")
    assert any(item.value == 400 and item.unit is None for item in measurements)


@pytest.mark.parametrize(
    ("text", "operator", "value"),
    [
        ("at least 50 mg", "GE", 50),
        ("no less than 50", "GE", 50),
        ("greater than or equal to 50", "GE", 50),
        ("more than 50", "GT", 50),
        ("up to 50", "LE", 50),
        ("no more than 50", "LE", 50),
        ("under 50", "LT", 50),
        ("50 or more", "GE", 50),
        ("50 or fewer", "LE", 50),
        (">= 50", "GE", 50),
        ("< 50", "LT", 50),
    ],
)
def test_comparison_extraction(text: str, operator: str, value: int) -> None:
    comparisons = extract_comparisons(text)
    assert any(
        item.operator == operator and item.value == value for item in comparisons
    ), f"{text!r} produced {comparisons}"


def test_longer_operator_phrases_win_over_their_prefixes() -> None:
    """``greater than or equal to`` must not be read as ``greater than``."""

    comparisons = extract_comparisons("greater than or equal to 50 mg")
    assert {item.operator for item in comparisons} == {"GE"}


def test_planning_splits_sentences_and_semicolons_but_not_conjunctions() -> None:
    plan = plan_atomic_claims("Start therapy; measure at six months. Switch if it rises.")
    assert len(plan) == 3

    conjoined = plan_atomic_claims("Give 500 mg and 1 g on alternate days.")
    assert len(conjoined) == 1


def test_planning_does_not_split_on_decimals_or_abbreviations() -> None:
    assert len(plan_atomic_claims("Give 0.5 mg daily.")) == 1
    assert len(plan_atomic_claims("Some agents, e.g. rifampicin, interact.")) == 1


def test_planning_always_returns_at_least_one_unit() -> None:
    assert len(plan_atomic_claims("50 mg")) == 1


def test_combination_never_promotes_an_outcome() -> None:
    assert combine((VerificationStatus.SUPPORTED, VerificationStatus.UNRESOLVED)) is (
        VerificationStatus.UNRESOLVED
    )
    assert combine((VerificationStatus.UNRESOLVED, VerificationStatus.UNSUPPORTED)) is (
        VerificationStatus.UNSUPPORTED
    )
    assert combine((VerificationStatus.SUPPORTED,)) is VerificationStatus.SUPPORTED
    assert combine(()) is VerificationStatus.SUPPORTED


def test_verification_is_deterministic() -> None:
    claim = "Give dolutegravir 50 mg once daily and switch above 1000 copies/mL."
    first = verify(claim)
    second = verify(claim)

    assert first.status is second.status
    assert [item.detail for item in first.findings] == [item.detail for item in second.findings]
